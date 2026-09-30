"""Janitor agent — writes Pandas cleaning code and executes it in the Docker sandbox."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, Optional

from agents.base import extract_python_code, get_gemini_flash, invoke_text
from core.state import SwarmState
from tools.sandbox import ExecutionResult, execute_python


_JANITOR_CODE_CONTRACT = """
Your code MUST:
1. Import only stdlib + pandas/numpy (available in the sandbox).
2. Load the dataset from os.environ["DATASET_PATH"] (CSV via pd.read_csv, SQLite via sqlite3/pandas).
3. Clean / impute / drop / cast as needed based on the reported issues.
4. Write the cleaned tabular result to:
     os.path.join(os.environ["OUTPUT_DIR"], "cleaned.csv")
5. Print a one-line JSON summary to stdout, e.g.:
     print(json.dumps({"rows": len(df), "cols": list(df.columns)}))
6. Do NOT use network, file paths outside OUTPUT_DIR/DATASET_PATH, or interactive input.
""".strip()


def _persist_cleaned_artifact(
    result: ExecutionResult,
    original_dataset: str,
) -> Optional[str]:
    """Write cleaned.csv from sandbox artifacts to a host path; return that path."""
    for artifact in result.artifacts:
        if artifact.name != "cleaned.csv":
            continue
        raw = base64.b64decode(artifact.content_base64) if artifact.content_base64 else b""
        if not raw and artifact.text_content is not None:
            raw = artifact.text_content.encode("utf-8")
        if not raw:
            return None
        out_path = Path(original_dataset).expanduser().resolve().with_name(
            f"{Path(original_dataset).stem}_cleaned.csv"
        )
        out_path.write_bytes(raw)
        return str(out_path)
    return None


def _previous_error(state: SwarmState) -> str:
    result = state.get("latest_execution_result")
    if result is None:
        return ""
    if isinstance(result, ExecutionResult):
        if result.success:
            return ""
        return result.error or result.stderr or f"exit_code={result.exit_code}"
    if isinstance(result, dict) and not result.get("success", False):
        return str(result.get("error") or result.get("stderr") or result)
    return ""


def run_janitor(state: SwarmState) -> Dict[str, Any]:
    """Generate cleaning code with Gemini Flash, execute it in the sandbox, update state."""
    dataset_path = state["dataset_path"]
    schema_info = state.get("schema_info") or {}
    iterations = int(state.get("iterations") or 0) + 1
    history = state.get("cleaning_code_history") or []
    prior_error = _previous_error(state)

    logs = [f"[janitor] Cleaning attempt {iterations} for {dataset_path}"]

    repair_block = ""
    if prior_error:
        last_code = history[-1] if history else ""
        repair_block = f"""
The previous cleaning script FAILED. Repair it using the stack trace.

Previous code:
```python
{last_code}
```

Stack trace / error:
{prior_error}
"""

    prompt = f"""You are the Janitor Agent. Write a single executable Python script that cleans a tabular dataset.

{_JANITOR_CODE_CONTRACT}

Schema / issues JSON:
{json.dumps(schema_info, indent=2, default=str)}

{repair_block}

Return ONLY the Python code (preferably in a ```python fenced block). No prose.
"""

    try:
        llm = get_gemini_flash(temperature=0.15)
        raw = invoke_text(llm, prompt)
        code = extract_python_code(raw)
        if not code.strip():
            raise RuntimeError("model returned empty cleaning code")
    except Exception as exc:  # noqa: BLE001
        failed = ExecutionResult(
            success=False,
            error=f"janitor code generation failed: {exc}",
            stderr=str(exc),
            exit_code=1,
        )
        logs.append(f"[janitor] Code generation failed: {exc}")
        return {
            "iterations": iterations,
            "latest_execution_result": failed,
            "logs": logs,
            "next_route": "supervisor",
        }

    logs.append(f"[janitor] Executing cleaning script in Docker sandbox ({len(code)} chars)")
    result = execute_python(code, dataset_path)

    updates: Dict[str, Any] = {
        "iterations": iterations,
        "cleaning_code_history": [code],
        "latest_execution_result": result,
        "logs": logs,
        "next_route": "supervisor",
    }

    if result.success:
        cleaned_path = _persist_cleaned_artifact(result, dataset_path)
        if cleaned_path:
            updates["dataset_path"] = cleaned_path
            updates["logs"] = logs + [f"[janitor] Cleaned dataset saved to {cleaned_path}"]
        else:
            updates["logs"] = logs + [
                "[janitor] Execution succeeded but cleaned.csv artifact was missing"
            ]
            # Treat missing artifact as a soft failure for the supervisor.
            updates["latest_execution_result"] = ExecutionResult(
                success=False,
                stdout=result.stdout,
                stderr=result.stderr + "\nMissing OUTPUT_DIR/cleaned.csv artifact",
                exit_code=result.exit_code,
                error="cleaned.csv artifact missing after successful exit",
                artifacts=result.artifacts,
                duration_seconds=result.duration_seconds,
                container_id=result.container_id,
                dataset_path_in_container=result.dataset_path_in_container,
                timed_out=result.timed_out,
            )
    else:
        updates["logs"] = logs + [
            f"[janitor] Sandbox execution failed: {result.error or result.stderr[:300]}"
        ]

    return updates


janitor_node = run_janitor
