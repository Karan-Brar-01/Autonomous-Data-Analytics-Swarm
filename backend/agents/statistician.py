"""Statistician agent — distribution checks and correlation analysis on cleaned data."""

from __future__ import annotations

import json
from typing import Any, Dict

from agents.base import extract_json_object, extract_python_code, get_gemini_flash, invoke_text
from core.state import SwarmState
from tools.sandbox import ExecutionResult, execute_python


_STAT_CODE_CONTRACT = """
Your code MUST:
1. Load the cleaned dataset from os.environ["DATASET_PATH"] with pandas.
2. Use pandas / numpy / scipy only (no network).
3. For each numeric column, evaluate plausible distributions (Normal, Poisson, etc.)
   using reasonable heuristics / goodness-of-fit style checks available in scipy.
4. Compute a Pearson correlation matrix for numeric columns.
5. Write a JSON report to os.path.join(os.environ["OUTPUT_DIR"], "stats.json")
   AND print the same JSON to stdout.
6. JSON shape:
{
  "distributions": [{"column": str, "best_fit": str, "details": object}],
  "correlations": {"columns": [str], "matrix": [[float, ...], ...]},
  "notes": [str]
}
""".strip()


def run_statistician(state: SwarmState) -> Dict[str, Any]:
    """Generate and sandbox-execute statistical analysis code; store structured summary."""
    dataset_path = state["dataset_path"]
    schema_info = state.get("schema_info") or {}
    logs = [f"[statistician] Analyzing cleaned dataset at {dataset_path}"]

    prompt = f"""You are the Statistician Agent. Write one executable Python script that profiles distributions
and correlations for a cleaned tabular dataset.

{_STAT_CODE_CONTRACT}

Known schema / profiler context:
{json.dumps(schema_info, indent=2, default=str)}

Return ONLY Python code (```python fence preferred).
"""

    try:
        llm = get_gemini_flash(temperature=0.1)
        raw = invoke_text(llm, prompt)
        code = extract_python_code(raw)
        if not code.strip():
            raise RuntimeError("model returned empty statistician code")
    except Exception as exc:  # noqa: BLE001
        logs.append(f"[statistician] Code generation failed: {exc}")
        return {
            "statistical_summary": {"error": str(exc)},
            "latest_execution_result": ExecutionResult(
                success=False,
                error=f"statistician code generation failed: {exc}",
                stderr=str(exc),
                exit_code=1,
            ),
            "logs": logs,
            "next_route": "finish",
        }

    result = execute_python(code, dataset_path)
    logs.append(
        f"[statistician] Sandbox finished success={result.success} exit={result.exit_code}"
    )

    summary: Dict[str, Any]
    if result.success:
        try:
            summary = extract_json_object(result.stdout)
        except Exception:
            # Fall back to artifact text if stdout was noisy.
            summary = {}
            for artifact in result.artifacts:
                if artifact.name == "stats.json" and artifact.text_content:
                    try:
                        summary = extract_json_object(artifact.text_content)
                        break
                    except Exception:  # noqa: BLE001
                        continue
            if not summary:
                summary = {
                    "raw_stdout": result.stdout,
                    "notes": ["Could not parse structured stats JSON; raw stdout retained"],
                }
        logs.append("[statistician] Statistical summary captured")
    else:
        summary = {
            "error": result.error or result.stderr,
            "stdout": result.stdout,
        }
        logs.append(f"[statistician] Analysis failed: {summary['error']}")

    return {
        "statistical_summary": summary,
        "latest_execution_result": result,
        "logs": logs,
        "next_route": "finish",
    }


statistician_node = run_statistician
