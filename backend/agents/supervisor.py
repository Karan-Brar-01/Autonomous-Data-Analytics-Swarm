"""Supervisor agent — evaluates execution state and routes self-healing retries."""

from __future__ import annotations

import json
from typing import Any, Dict, Literal, Optional

from agents.base import extract_json_object, get_gemini_flash, invoke_text
from config import get_settings
from core.state import SwarmState
from tools.sandbox import ExecutionResult

Route = Literal["janitor", "statistician", "finish"]


def _result_as_dict(result: Any) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    if isinstance(result, ExecutionResult):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return {"repr": str(result)}


def _rule_based_route(state: SwarmState, max_retries: int) -> Route:
    """Deterministic routing used as source of truth (LLM may only advise)."""
    result = state.get("latest_execution_result")
    iterations = int(state.get("iterations") or 0)
    stats = state.get("statistical_summary") or {}

    success = False
    if isinstance(result, ExecutionResult):
        success = bool(result.success)
    elif isinstance(result, dict):
        success = bool(result.get("success"))

    # If we already have stats, we are done.
    if stats and "error" not in stats:
        return "finish"

    if not success:
        if iterations < max_retries:
            return "janitor"
        return "finish"

    # Cleaning succeeded — run statistics unless already present with error-only payload.
    if not stats or "error" in stats:
        return "statistician"
    return "finish"


def run_supervisor(state: SwarmState) -> Dict[str, Any]:
    """
    Evaluate sandbox execution / swarm progress.

    On failure: route back to Janitor with stack-trace context (via state) while
    ``iterations`` < max retries (default 3). On success: route to Statistician.
    """
    settings = get_settings()
    max_retries = int(settings.max_agent_retries)
    result_dump = _result_as_dict(state.get("latest_execution_result"))
    iterations = int(state.get("iterations") or 0)

    forced_route = _rule_based_route(state, max_retries)
    rationale = "rule-based routing"

    prompt = f"""You are the Supervisor Agent for a data-analytics swarm.
Decide the next route given the execution state. Return ONLY JSON:
{{"route": "janitor"|"statistician"|"finish", "rationale": str}}

Rules you must respect:
- If latest execution failed AND iterations < {max_retries}, prefer "janitor" (self-heal).
- If latest execution failed AND iterations >= {max_retries}, prefer "finish".
- If latest cleaning execution succeeded and statistics are missing, prefer "statistician".
- If work is complete, prefer "finish".

iterations: {iterations}
max_retries: {max_retries}
statistical_summary_keys: {list((state.get("statistical_summary") or {}).keys())}
latest_execution_result:
{json.dumps(result_dump, indent=2, default=str)}
"""

    try:
        llm = get_gemini_flash(temperature=0.0)
        raw = invoke_text(llm, prompt)
        parsed = extract_json_object(raw)
        llm_route = str(parsed.get("route", "")).strip().lower()
        rationale = str(parsed.get("rationale") or rationale)
        if llm_route in {"janitor", "statistician", "finish"}:
            # Enforce hard safety limits regardless of LLM suggestion.
            if forced_route == "finish" and llm_route == "janitor" and iterations >= max_retries:
                route: Route = "finish"
                rationale = f"{rationale} (capped at {max_retries} retries)"
            elif forced_route == "janitor" and llm_route == "statistician":
                # Never skip healing when the last run failed and retries remain.
                route = "janitor"
                rationale = f"{rationale} (overridden: execution failed, retrying janitor)"
            else:
                route = llm_route  # type: ignore[assignment]
        else:
            route = forced_route
            rationale = f"invalid LLM route; fell back to {forced_route}"
    except Exception as exc:  # noqa: BLE001
        route = forced_route
        rationale = f"supervisor LLM unavailable ({exc}); used {forced_route}"

    log = f"[supervisor] route={route} iterations={iterations}/{max_retries} — {rationale}"
    return {
        "next_route": route,
        "logs": [log],
    }


def route_after_supervisor(state: SwarmState) -> str:
    """Conditional-edge mapper for LangGraph."""
    route = (state.get("next_route") or "finish").strip().lower()
    if route not in {"janitor", "statistician", "finish"}:
        return "finish"
    return route


supervisor_node = run_supervisor
