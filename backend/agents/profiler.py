"""Profiler agent — analyzes dataset schemas and surfaces missing-value alerts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from agents.base import extract_json_object, get_gemini_flash, invoke_text
from core.state import SwarmState


def _load_frame(dataset_path: str, sample_rows: int = 50) -> pd.DataFrame:
    path = Path(dataset_path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, nrows=sample_rows)
    if suffix in {".sqlite", ".db", ".sqlite3"}:
        with sqlite3.connect(path) as conn:
            tables = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';",
                conn,
            )
            if tables.empty:
                raise ValueError(f"no tables found in SQLite database: {path}")
            table_name = str(tables.iloc[0]["name"])
            return pd.read_sql_query(f'SELECT * FROM "{table_name}" LIMIT {sample_rows}', conn)
    raise ValueError(f"unsupported dataset type for profiler: {suffix}")


def _local_profile(df: pd.DataFrame) -> Dict[str, Any]:
    rows, cols = df.shape
    columns: List[Dict[str, Any]] = []
    missing_alerts: List[Dict[str, Any]] = []
    for name in df.columns:
        series = df[name]
        null_count = int(series.isna().sum())
        null_ratio = float(null_count / rows) if rows else 0.0
        col_info = {
            "name": str(name),
            "dtype": str(series.dtype),
            "null_count": null_count,
            "null_ratio": round(null_ratio, 6),
            "sample_values": [None if pd.isna(v) else _json_safe(v) for v in series.head(5).tolist()],
        }
        columns.append(col_info)
        if null_ratio > 0:
            missing_alerts.append(
                {
                    "column": str(name),
                    "null_ratio": round(null_ratio, 6),
                    "severity": "high" if null_ratio >= 0.3 else "medium" if null_ratio >= 0.1 else "low",
                }
            )
    return {
        "row_sample_size": rows,
        "column_count": cols,
        "columns": columns,
        "missing_value_alerts": missing_alerts,
    }


def _json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def run_profiler(state: SwarmState) -> Dict[str, Any]:
    """
    Ingest the raw dataset, compute a local profile, and ask Gemini for structured schema JSON.
    """
    dataset_path = state["dataset_path"]
    logs = [f"[profiler] Profiling dataset at {dataset_path}"]

    try:
        df = _load_frame(dataset_path)
        local = _local_profile(df)
    except Exception as exc:  # noqa: BLE001
        logs.append(f"[profiler] Failed to load dataset: {exc}")
        return {
            "logs": logs,
            "schema_info": {"error": str(exc)},
            "next_route": "janitor",
        }

    prompt = f"""You are the Profiler Agent in an autonomous data analytics swarm.
Given the local profile of a tabular dataset, return ONLY valid JSON with this shape:
{{
  "schema": {{
    "columns": [{{"name": str, "inferred_type": str, "nullable": bool}}],
    "primary_key_candidates": [str],
    "notes": [str]
  }},
  "missing_value_alerts": [{{"column": str, "null_ratio": float, "severity": "low"|"medium"|"high", "recommendation": str}}],
  "cleaning_priorities": [str],
  "dataset_issues": [str]
}}

Local profile:
{local}
"""

    try:
        llm = get_gemini_flash(temperature=0.1)
        raw = invoke_text(llm, prompt)
        schema_info = extract_json_object(raw)
        schema_info["local_profile"] = local
        logs.append("[profiler] Schema JSON generated successfully")
    except Exception as exc:  # noqa: BLE001
        # Fall back to deterministic local profile so the swarm can continue offline/API failures.
        schema_info = {
            "schema": {
                "columns": [
                    {
                        "name": c["name"],
                        "inferred_type": c["dtype"],
                        "nullable": c["null_count"] > 0,
                    }
                    for c in local["columns"]
                ],
                "primary_key_candidates": [],
                "notes": ["LLM profiling unavailable; using local profile only"],
            },
            "missing_value_alerts": local["missing_value_alerts"],
            "cleaning_priorities": [
                f"Impute or drop nulls in {a['column']}" for a in local["missing_value_alerts"]
            ],
            "dataset_issues": [f"profiler_llm_error: {exc}"],
            "local_profile": local,
        }
        logs.append(f"[profiler] LLM unavailable ({exc}); used local profile fallback")

    return {
        "schema_info": schema_info,
        "logs": logs,
        "next_route": "janitor",
    }


# LangGraph node alias
profiler_node = run_profiler
