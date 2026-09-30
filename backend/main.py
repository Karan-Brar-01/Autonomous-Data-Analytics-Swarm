"""FastAPI entrypoint for the Autonomous Data Analytics Swarm."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import get_settings
from core.graph import app_graph
from core.state import SwarmState, initial_state
from tools.persistence import (
    PersistenceError,
    append_agent_action_log,
    create_swarm_task,
    ensure_persistence_schema,
    persist_swarm_result,
)
from tools.sandbox import ExecutionResult

logger = logging.getLogger(__name__)
settings = get_settings()

UPLOAD_ROOT = Path(tempfile.gettempdir()) / "adas_uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

# In-memory task registry (dataset paths + cancellation flags).
_TASKS: Dict[str, Dict[str, Any]] = {}
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class UploadResponse(BaseModel):
    task_id: str
    dataset_path: str
    filename: str
    rows: int
    columns: List[str]
    message: str = "Upload successful"


class OrchestrateRequest(BaseModel):
    dataset_path: str = Field(..., min_length=1)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await asyncio.to_thread(ensure_persistence_schema)
        logger.info("Persistence schema ready")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persistence schema unavailable at startup: %s", exc)
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Multi-agent data analytics swarm: profile, clean, and analyze tabular datasets.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    """Health-style root endpoint."""
    return {
        "service": settings.app_name,
        "status": "ok",
        "env": settings.app_env,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for local development and containers."""
    return {"status": "healthy"}


def _decode_csv_bytes(raw: bytes) -> tuple[str, str]:
    """
    Decode uploaded CSV bytes using common spreadsheet encodings.

    Returns ``(text, encoding_used)``. Prefer UTF-8; fall back to Windows / Latin encodings.
    """
    encodings = (
        "utf-8-sig",
        "utf-8",
        "cp1252",
        "latin-1",
        "iso-8859-1",
        "mac_roman",
    )
    errors: list[str] = []
    for encoding in encodings:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
            continue
    raise ValueError(
        "Unable to decode CSV. Tried UTF-8, Windows-1252, and Latin-1. "
        + "; ".join(errors[:2])
    )


@app.post("/api/upload", response_model=UploadResponse)
async def upload_csv(file: UploadFile = File(...)) -> UploadResponse:
    """Receive a CSV file, validate it, and store it under a temp upload folder."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    original_name = Path(file.filename).name
    if not original_name.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    raw = await file.read()
    if not raw.strip():
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        text, encoding_used = _decode_csv_bytes(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        # Structural CSV check before pandas.
        sample = text[:8192]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        reader = csv.reader(io.StringIO(text), dialect)
        header = next(reader, None)
        if not header or not any(cell.strip() for cell in header):
            raise ValueError("CSV header row is missing or empty")
        first_row = next(reader, None)
        if first_row is None:
            raise ValueError("CSV contains a header but no data rows")

        df = pd.read_csv(io.StringIO(text))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid CSV format: {exc}") from exc

    if df.empty or len(df.columns) == 0:
        raise HTTPException(status_code=400, detail="CSV has no usable columns or rows")

    task_id = uuid.uuid4().hex
    dest = UPLOAD_ROOT / f"{task_id}_{original_name}"
    try:
        # Always persist UTF-8 so downstream agents / sandbox read consistently.
        dest.write_bytes(text.encode("utf-8"))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {exc}") from exc

    dataset_path = str(dest.resolve())
    _TASKS[task_id] = {
        "dataset_path": dataset_path,
        "filename": original_name,
        "status": "uploaded",
        "source_encoding": encoding_used,
    }

    try:
        await asyncio.to_thread(create_swarm_task, task_id, dataset_path)
    except PersistenceError as exc:
        logger.warning("Could not create DB task row for %s: %s", task_id, exc)

    logger.info(
        "Uploaded %s (%s rows, encoding=%s) as task %s",
        original_name,
        len(df),
        encoding_used,
        task_id,
    )

    return UploadResponse(
        task_id=task_id,
        dataset_path=dataset_path,
        filename=original_name,
        rows=int(len(df)),
        columns=[str(c) for c in df.columns.tolist()],
    )


def _serialize_execution(result: Any) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    if isinstance(result, ExecutionResult):
        data = result.model_dump()
        # Keep payloads light for WebSocket clients.
        artifacts = []
        for art in data.get("artifacts") or []:
            artifacts.append(
                {
                    "name": art.get("name"),
                    "relative_path": art.get("relative_path"),
                    "size_bytes": art.get("size_bytes"),
                    "text_preview": (art.get("text_content") or "")[:500],
                }
            )
        data["artifacts"] = artifacts
        return data
    if isinstance(result, dict):
        return result
    return {"repr": str(result)}


def _build_stream_event(
    *,
    task_id: str,
    agent: str,
    status: str,
    state_fragment: Dict[str, Any],
    merged: SwarmState,
) -> Dict[str, Any]:
    execution = _serialize_execution(
        state_fragment.get("latest_execution_result", merged.get("latest_execution_result"))
    )
    history = merged.get("cleaning_code_history") or []
    fragment_history = state_fragment.get("cleaning_code_history") or []
    latest_code = None
    if fragment_history:
        latest_code = fragment_history[-1]
    elif history:
        latest_code = history[-1]

    logs = state_fragment.get("logs") or []
    return {
        "type": "agent_update",
        "task_id": task_id,
        "agent": agent,
        "status": status,
        "iterations": merged.get("iterations", 0),
        "next_route": merged.get("next_route"),
        "logs": logs,
        "all_logs": merged.get("logs") or [],
        "code_generated": latest_code,
        "execution": execution,
        "stdout": (execution or {}).get("stdout") if execution else None,
        "stderr": (execution or {}).get("stderr") if execution else None,
        "error_trace": (
            (execution or {}).get("error") or (execution or {}).get("stderr")
            if execution
            else None
        ),
        "schema_info": merged.get("schema_info") or {},
        "statistical_summary": merged.get("statistical_summary") or {},
        "dataset_path": merged.get("dataset_path"),
    }


async def _safe_send(websocket: WebSocket, connected: asyncio.Event, payload: Dict[str, Any]) -> bool:
    """Send JSON if the client is still connected; never raise into the worker."""
    if not connected.is_set():
        return False
    try:
        await websocket.send_json(payload)
        return True
    except (WebSocketDisconnect, RuntimeError, ConnectionError) as exc:
        logger.info("WebSocket send aborted (client gone): %s", exc)
        connected.clear()
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebSocket send failed: %s", exc)
        connected.clear()
        return False


def _merge_state(base: SwarmState, fragment: Dict[str, Any]) -> SwarmState:
    """Apply a LangGraph update fragment onto a running state snapshot."""
    merged: Dict[str, Any] = dict(base)
    for key, value in fragment.items():
        if key in {"logs", "cleaning_code_history"} and isinstance(value, list):
            merged[key] = list(merged.get(key) or []) + list(value)
        else:
            merged[key] = value
    return merged  # type: ignore[return-value]


async def _run_swarm_worker(
    *,
    task_id: str,
    dataset_path: str,
    websocket: WebSocket,
    connected: asyncio.Event,
) -> None:
    """
    Execute ``app_graph`` asynchronously, stream updates, and persist the final result.

    Client disconnection only clears ``connected`` — this coroutine always runs to completion
    (or failure) so persistence is not skipped.
    """
    state = initial_state(dataset_path)
    merged: SwarmState = dict(state)  # type: ignore[assignment]
    final_error: Optional[str] = None
    seen_log_count = len(state.get("logs") or [])

    await _safe_send(
        websocket,
        connected,
        {
            "type": "status",
            "task_id": task_id,
            "status": "started",
            "agent": "system",
            "message": f"Orchestration started for {dataset_path}",
            "dataset_path": dataset_path,
        },
    )

    try:
        await asyncio.to_thread(create_swarm_task, task_id, dataset_path)
    except PersistenceError:
        # Task row may already exist from /api/upload.
        pass

    try:
        async for update in app_graph.astream(state, stream_mode="updates"):
            if not isinstance(update, dict):
                continue

            for agent_name, fragment in update.items():
                if not isinstance(fragment, dict):
                    fragment = {"value": fragment}

                merged = _merge_state(merged, fragment)
                event = _build_stream_event(
                    task_id=task_id,
                    agent=str(agent_name),
                    status="node_completed",
                    state_fragment=fragment,
                    merged=merged,
                )
                await _safe_send(websocket, connected, event)

                # Persist incremental action logs without failing the worker.
                new_logs = list(merged.get("logs") or [])
                if len(new_logs) > seen_log_count:
                    for line in new_logs[seen_log_count:]:
                        await asyncio.to_thread(
                            append_agent_action_log,
                            task_id,
                            agent_name=str(agent_name),
                            message=line,
                            event_type="stream",
                            payload={"agent": agent_name},
                        )
                    seen_log_count = len(new_logs)

                if fragment.get("latest_execution_result") is not None:
                    await asyncio.to_thread(
                        append_agent_action_log,
                        task_id,
                        agent_name=str(agent_name),
                        message="execution_result",
                        event_type="execution",
                        payload=_serialize_execution(fragment.get("latest_execution_result")) or {},
                    )

        status = "completed"
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        final_error = str(exc)
        logger.exception("Swarm worker failed for task %s", task_id)
        await _safe_send(
            websocket,
            connected,
            {
                "type": "error",
                "task_id": task_id,
                "status": "failed",
                "agent": "system",
                "error_trace": final_error,
                "message": f"Swarm failed: {final_error}",
            },
        )

    # Always persist final summaries / logs, even if the client disconnected.
    try:
        await asyncio.to_thread(
            persist_swarm_result,
            task_id,
            status=status,
            dataset_path=str(merged.get("dataset_path") or dataset_path),
            schema_info=dict(merged.get("schema_info") or {}),
            statistical_summary=dict(merged.get("statistical_summary") or {}),
            cleaning_code_history=list(merged.get("cleaning_code_history") or []),
            logs=list(merged.get("logs") or []),
            iterations=int(merged.get("iterations") or 0),
            error=final_error,
        )
    except PersistenceError as exc:
        logger.error("Failed to persist swarm result for %s: %s", task_id, exc)
        await _safe_send(
            websocket,
            connected,
            {
                "type": "warning",
                "task_id": task_id,
                "message": f"Result persistence failed: {exc}",
            },
        )

    if task_id in _TASKS:
        _TASKS[task_id]["status"] = status

    await _safe_send(
        websocket,
        connected,
        {
            "type": "complete",
            "task_id": task_id,
            "status": status,
            "agent": "system",
            "iterations": int(merged.get("iterations") or 0),
            "schema_info": merged.get("schema_info") or {},
            "statistical_summary": merged.get("statistical_summary") or {},
            "all_logs": merged.get("logs") or [],
            "dataset_path": merged.get("dataset_path"),
            "error_trace": final_error,
        },
    )


@app.websocket("/ws/orchestrate/{task_id}")
async def orchestrate_ws(websocket: WebSocket, task_id: str) -> None:
    """
    Run the LangGraph swarm for ``task_id`` and stream agent state updates.

    Client must send an initial JSON payload: ``{"dataset_path": "..."}``.
    Disconnections stop outbound streaming but do not cancel the background worker.
    """
    if not _SAFE_TASK_ID.match(task_id):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    connected = asyncio.Event()
    connected.set()
    worker: Optional[asyncio.Task[None]] = None

    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=60.0)
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError) as exc:
        logger.info("Orchestrate handshake failed for %s: %s", task_id, exc)
        try:
            await websocket.close(code=1008)
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        request = OrchestrateRequest.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        await _safe_send(
            websocket,
            connected,
            {"type": "error", "message": f"Invalid orchestrate payload: {exc}"},
        )
        connected.clear()
        try:
            await websocket.close(code=1008)
        except Exception:  # noqa: BLE001
            pass
        return

    client_path = Path(request.dataset_path).expanduser()
    registered = _TASKS.get(task_id, {}).get("dataset_path")

    if client_path.is_file():
        dataset_path = str(client_path.resolve())
    elif registered and Path(str(registered)).expanduser().is_file():
        dataset_path = str(Path(str(registered)).expanduser().resolve())
        await _safe_send(
            websocket,
            connected,
            {
                "type": "status",
                "task_id": task_id,
                "message": "Client dataset_path missing; using uploaded path from task registry",
                "dataset_path": dataset_path,
            },
        )
    else:
        await _safe_send(
            websocket,
            connected,
            {
                "type": "error",
                "task_id": task_id,
                "message": f"dataset_path not found: {request.dataset_path}",
            },
        )
        connected.clear()
        try:
            await websocket.close(code=1008)
        except Exception:  # noqa: BLE001
            pass
        return

    path = Path(dataset_path)

    worker = asyncio.create_task(
        _run_swarm_worker(
            task_id=task_id,
            dataset_path=str(path.resolve()),
            websocket=websocket,
            connected=connected,
        ),
        name=f"swarm-worker-{task_id}",
    )

    try:
        # Keep the socket readable until the client disconnects or the worker ends.
        while not worker.done():
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                connected.clear()
                logger.info("Client disconnected from task %s; worker continues", task_id)
                break

            if message.get("type") == "websocket.disconnect":
                connected.clear()
                break

            # Optional client cancel signal — marks status only; worker still finishes current graph.
            data = message.get("text") or message.get("bytes")
            if data:
                text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
                if "cancel" in text.lower():
                    await _safe_send(
                        websocket,
                        connected,
                        {
                            "type": "status",
                            "task_id": task_id,
                            "message": "Cancel noted; worker will finish current run then stop streaming",
                        },
                    )
    finally:
        connected.clear()
        if worker is not None:
            # Shield so handler teardown / cancellation cannot kill persistence mid-flight.
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # If the server is shutting down, still try to let the worker settle.
                if not worker.done():
                    try:
                        await worker
                    except Exception:  # noqa: BLE001
                        logger.exception("Worker ended with error after cancel for %s", task_id)
            except Exception:  # noqa: BLE001
                logger.exception("Swarm worker raised for task %s", task_id)

        if connected.is_set():
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.app_env == "development",
    )
