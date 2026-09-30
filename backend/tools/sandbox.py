"""Docker-sandboxed execution of untrusted Python / Pandas scripts."""

from __future__ import annotations

import base64
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import docker
from docker.errors import APIError, ContainerError, DockerException, ImageNotFound, NotFound
from docker.models.containers import Container
from pydantic import BaseModel, Field, field_validator

from config import Settings, get_settings

# Dependencies installed once into a persistent Docker volume and mounted read-only.
_SANDBOX_PACKAGES: tuple[str, ...] = ("pandas", "numpy", "scipy")
_SUPPORTED_DATA_SUFFIXES: frozenset[str] = frozenset({".csv", ".sqlite", ".db", ".sqlite3"})


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot be prepared or invoked."""


class ArtifactInfo(BaseModel):
    """A file produced inside the sandbox workspace during execution."""

    name: str
    relative_path: str
    size_bytes: int
    content_base64: str = ""
    text_content: Optional[str] = None


class ExecutionResult(BaseModel):
    """Structured result of a sandboxed code run."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    error: Optional[str] = None
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    duration_seconds: float = 0.0
    container_id: Optional[str] = None
    dataset_path_in_container: Optional[str] = None

    @field_validator("stdout", "stderr", mode="before")
    @classmethod
    def _coerce_str(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)


class CodeSandbox:
    """
    Secure Python execution engine backed by the Docker Python SDK.

    User code runs in an isolated ``python:3.11-slim`` container with:
    - a temporary host workspace bind-mounted at ``/workspace`` (read/write)
    - a shared vendor volume of scientific packages at ``/vendor`` (read-only)
    - network disabled, capabilities dropped, 512MB memory, 15s wall timeout
    """

    CONTAINER_WORKSPACE = "/workspace"
    CONTAINER_VENDOR = "/vendor"
    CONTAINER_INPUT_DIR = "/workspace/input"
    CONTAINER_OUTPUT_DIR = "/workspace/output"
    USER_SCRIPT_NAME = "user_code.py"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        docker_client: Optional[docker.DockerClient] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = docker_client or docker.from_env()
        self._image = self._settings.docker_image
        self._timeout = min(int(self._settings.docker_timeout_seconds), 15)
        self._mem_limit = self._settings.docker_memory_limit
        self._vendor_volume = self._settings.docker_vendor_volume

    def execute(
        self,
        code: str,
        dataset_path: str | Path,
        *,
        timeout_seconds: Optional[int] = None,
        extra_env: Optional[Mapping[str, str]] = None,
    ) -> ExecutionResult:
        """
        Execute ``code`` inside Docker with ``dataset_path`` available in the workspace.

        The dataset (CSV or SQLite) is copied into the container workspace. Agents should
        read it via the ``DATASET_PATH`` environment variable (or ``/workspace/input/...``).
        Files written under ``/workspace/output`` are returned as artifacts.
        """
        if not isinstance(code, str) or not code.strip():
            raise SandboxError("code must be a non-empty Python source string")

        source = Path(dataset_path).expanduser().resolve()
        if not source.is_file():
            raise SandboxError(f"dataset path does not exist or is not a file: {source}")
        if source.suffix.lower() not in _SUPPORTED_DATA_SUFFIXES:
            raise SandboxError(
                f"unsupported dataset type '{source.suffix}'; "
                f"expected one of {sorted(_SUPPORTED_DATA_SUFFIXES)}"
            )

        timeout = min(timeout_seconds or self._timeout, 15)
        if timeout <= 0:
            raise SandboxError("timeout_seconds must be a positive integer (max 15)")

        started = time.perf_counter()
        workspace = Path(tempfile.mkdtemp(prefix="adas_sandbox_"))
        container: Optional[Container] = None

        try:
            self._ensure_image()
            self._ensure_vendor_packages()

            dataset_in_container = self._prepare_workspace(workspace, code, source)
            container = self._start_container(
                workspace=workspace,
                dataset_in_container=dataset_in_container,
                extra_env=extra_env or {},
            )
            return self._await_container(
                container=container,
                workspace=workspace,
                dataset_in_container=dataset_in_container,
                timeout=timeout,
                started=started,
            )
        except SandboxError as exc:
            return ExecutionResult(
                success=False,
                error=str(exc),
                stderr=str(exc),
                duration_seconds=time.perf_counter() - started,
            )
        except DockerException as exc:
            return ExecutionResult(
                success=False,
                error=f"Docker error: {exc}",
                stderr=str(exc),
                duration_seconds=time.perf_counter() - started,
            )
        except Exception as exc:  # noqa: BLE001 — surface unexpected failures cleanly
            return ExecutionResult(
                success=False,
                error=f"Sandbox failure: {exc}",
                stderr=str(exc),
                duration_seconds=time.perf_counter() - started,
            )
        finally:
            if container is not None:
                self._safe_remove_container(container)
            shutil.rmtree(workspace, ignore_errors=True)

    def _ensure_image(self) -> None:
        try:
            self._client.images.get(self._image)
        except ImageNotFound:
            try:
                self._client.images.pull(self._image)
            except DockerException as exc:
                raise SandboxError(
                    f"failed to pull sandbox image '{self._image}': {exc}"
                ) from exc
        except APIError as exc:
            raise SandboxError(f"unable to access Docker image '{self._image}': {exc}") from exc

    def _ensure_vendor_packages(self) -> None:
        """Install scientific packages into a reusable named volume (networked, one-shot)."""
        try:
            self._client.volumes.create(name=self._vendor_volume)
        except APIError:
            # Volume already exists (or name conflict) — continue and verify contents.
            pass

        marker = "/vendor/.adas_ready"
        packages = " ".join(_SANDBOX_PACKAGES)
        check_and_install = (
            f"if [ -f {marker} ]; then exit 0; fi; "
            f"pip install --no-cache-dir --target /vendor {packages} "
            f"&& printf 'ok' > {marker}"
        )

        try:
            self._client.containers.run(
                image=self._image,
                command=["bash", "-lc", check_and_install],
                volumes={self._vendor_volume: {"bind": self.CONTAINER_VENDOR, "mode": "rw"}},
                remove=True,
                network_mode="bridge",
                mem_limit=self._mem_limit,
            )
        except ContainerError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
            raise SandboxError(f"failed to provision sandbox vendor packages: {stderr}") from exc
        except DockerException as exc:
            raise SandboxError(f"failed to provision sandbox vendor packages: {exc}") from exc

    def _prepare_workspace(self, workspace: Path, code: str, dataset: Path) -> str:
        input_dir = workspace / "input"
        output_dir = workspace / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        destination = input_dir / dataset.name
        try:
            shutil.copy2(dataset, destination)
        except OSError as exc:
            raise SandboxError(f"failed to copy dataset into workspace: {exc}") from exc

        script_path = workspace / self.USER_SCRIPT_NAME
        try:
            script_path.write_text(code, encoding="utf-8")
        except OSError as exc:
            raise SandboxError(f"failed to write user script: {exc}") from exc

        return f"{self.CONTAINER_INPUT_DIR}/{dataset.name}"

    def _start_container(
        self,
        *,
        workspace: Path,
        dataset_in_container: str,
        extra_env: Mapping[str, str],
    ) -> Container:
        env = {
            "PYTHONPATH": self.CONTAINER_VENDOR,
            "PYTHONUNBUFFERED": "1",
            "DATASET_PATH": dataset_in_container,
            "OUTPUT_DIR": self.CONTAINER_OUTPUT_DIR,
            **{str(k): str(v) for k, v in extra_env.items()},
        }

        try:
            return self._client.containers.run(
                image=self._image,
                command=["python", f"{self.CONTAINER_WORKSPACE}/{self.USER_SCRIPT_NAME}"],
                volumes={
                    str(workspace): {"bind": self.CONTAINER_WORKSPACE, "mode": "rw"},
                    self._vendor_volume: {"bind": self.CONTAINER_VENDOR, "mode": "ro"},
                },
                working_dir=self.CONTAINER_WORKSPACE,
                environment=env,
                mem_limit=self._mem_limit,
                nano_cpus=1_000_000_000,
                network_mode="none",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                pids_limit=256,
                detach=True,
                stdin_open=False,
                tty=False,
            )
        except DockerException as exc:
            raise SandboxError(f"failed to start sandbox container: {exc}") from exc

    def _await_container(
        self,
        *,
        container: Container,
        workspace: Path,
        dataset_in_container: str,
        timeout: int,
        started: float,
    ) -> ExecutionResult:
        timed_out = False
        exit_code = -1
        wait_error: Optional[str] = None

        try:
            wait_result = container.wait(timeout=timeout)
            exit_code = int(wait_result.get("StatusCode", -1))
        except Exception as exc:  # docker-py raises ReadTimeout / requests timeout variants
            timed_out = True
            wait_error = f"execution timed out after {timeout}s ({exc})"
            self._safe_kill_container(container)
            try:
                wait_result = container.wait(timeout=5)
                exit_code = int(wait_result.get("StatusCode", 137))
            except Exception:  # noqa: BLE001
                exit_code = 137

        stdout = self._decode_logs(container, stdout=True, stderr=False)
        stderr = self._decode_logs(container, stdout=False, stderr=True)

        artifacts = self._collect_artifacts(workspace)
        duration = time.perf_counter() - started
        success = (not timed_out) and exit_code == 0

        error: Optional[str] = None
        if timed_out:
            error = wait_error or f"execution timed out after {timeout}s"
        elif exit_code != 0:
            error = stderr.strip() or f"process exited with code {exit_code}"

        return ExecutionResult(
            success=success,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            error=error,
            artifacts=artifacts,
            duration_seconds=duration,
            container_id=container.id,
            dataset_path_in_container=dataset_in_container,
        )

    @staticmethod
    def _decode_logs(container: Container, *, stdout: bool, stderr: bool) -> str:
        try:
            raw = container.logs(stdout=stdout, stderr=stderr)
        except DockerException:
            return ""
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    def _collect_artifacts(self, workspace: Path) -> list[ArtifactInfo]:
        """Snapshot output files into memory before the temp workspace is deleted."""
        output_dir = workspace / "output"
        artifacts: list[ArtifactInfo] = []
        if not output_dir.is_dir():
            return artifacts

        for path in sorted(output_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            relative = path.relative_to(workspace).as_posix()
            text_content: Optional[str] = None
            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                text_content = None
            artifacts.append(
                ArtifactInfo(
                    name=path.name,
                    relative_path=relative,
                    size_bytes=len(raw),
                    content_base64=base64.b64encode(raw).decode("ascii"),
                    text_content=text_content,
                )
            )
        return artifacts

    @staticmethod
    def _safe_kill_container(container: Container) -> None:
        try:
            container.kill()
        except NotFound:
            return
        except APIError:
            try:
                container.stop(timeout=1)
            except DockerException:
                return

    @staticmethod
    def _safe_remove_container(container: Container) -> None:
        try:
            container.remove(force=True)
        except (NotFound, APIError, DockerException):
            return


def execute_python(
    code: str,
    dataset_path: str | Path,
    *,
    timeout_seconds: Optional[int] = None,
    extra_env: Optional[Mapping[str, str]] = None,
    settings: Optional[Settings] = None,
) -> ExecutionResult:
    """Convenience wrapper around :class:`CodeSandbox`."""
    return CodeSandbox(settings=settings).execute(
        code,
        dataset_path,
        timeout_seconds=timeout_seconds,
        extra_env=extra_env,
    )


__all__: Sequence[str] = (
    "ArtifactInfo",
    "CodeSandbox",
    "ExecutionResult",
    "SandboxError",
    "execute_python",
)
