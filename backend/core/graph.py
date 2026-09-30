"""LangGraph supervisor-worker graph construction and routing."""

from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from agents.janitor import janitor_node
from agents.profiler import profiler_node
from agents.statistician import statistician_node
from agents.supervisor import route_after_supervisor, supervisor_node
from core.state import SwarmState, initial_state


def build_swarm_graph() -> StateGraph:
    """
    Construct the Autonomous Data Analytics Swarm state graph.

    Flow:
        START → profiler → janitor → supervisor ─┬→ janitor (self-heal, max 3)
                                                 ├→ statistician → END
                                                 └→ END
    """
    graph = StateGraph(SwarmState)

    graph.add_node("profiler", profiler_node)
    graph.add_node("janitor", janitor_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("statistician", statistician_node)

    graph.add_edge(START, "profiler")
    graph.add_edge("profiler", "janitor")
    graph.add_edge("janitor", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "janitor": "janitor",
            "statistician": "statistician",
            "finish": END,
        },
    )
    graph.add_edge("statistician", END)

    return graph


def compile_swarm_graph():
    """Compile the swarm into a LangGraph runnable."""
    return build_swarm_graph().compile()


# Runnable engine exported for the API / CLI layers.
app_graph = compile_swarm_graph()


def run_swarm(dataset_path: str, *, config: Optional[Dict[str, Any]] = None) -> SwarmState:
    """Convenience entrypoint: initialize state and invoke ``app_graph``."""
    state = initial_state(dataset_path)
    return app_graph.invoke(state, config=config)
