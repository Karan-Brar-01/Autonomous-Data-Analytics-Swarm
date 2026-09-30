"""LangGraph shared state for the analytics swarm."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from tools.sandbox import ExecutionResult


class SwarmState(TypedDict, total=False):
    """
    Global graph state shared across Profiler, Janitor, Statistician, and Supervisor.

    List fields use ``operator.add`` reducers so nodes append rather than overwrite.
    """

    dataset_path: str
    schema_info: Dict[str, Any]
    cleaning_code_history: Annotated[List[str], operator.add]
    latest_execution_result: Optional[ExecutionResult]
    statistical_summary: Dict[str, Any]
    iterations: int
    logs: Annotated[List[str], operator.add]
    # Orchestration: set by Supervisor — "janitor" | "statistician" | "finish"
    next_route: str


def initial_state(dataset_path: str) -> SwarmState:
    """Build a clean initial state for a new swarm run."""
    return SwarmState(
        dataset_path=dataset_path,
        schema_info={},
        cleaning_code_history=[],
        latest_execution_result=None,
        statistical_summary={},
        iterations=0,
        logs=[f"[system] Swarm initialized for dataset: {dataset_path}"],
        next_route="profiler",
    )


def append_log(message: str) -> Dict[str, List[str]]:
    """Helper: return a partial state update that appends one log line."""
    return {"logs": [message]}
