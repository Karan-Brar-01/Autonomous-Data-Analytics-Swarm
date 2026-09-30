"""Persist swarm run summaries and agent action logs to Supabase / PostgreSQL."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from tools.db_inspector import create_db_engine

logger = logging.getLogger(__name__)

metadata = MetaData()

swarm_tasks = Table(
    "swarm_tasks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("dataset_path", Text, nullable=False),
    Column("status", String(32), nullable=False, default="pending"),
    Column("schema_info", JSON, nullable=True),
    Column("statistical_summary", JSON, nullable=True),
    Column("cleaning_code_history", JSON, nullable=True),
    Column("logs", JSON, nullable=True),
    Column("iterations", Integer, nullable=False, default=0),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)

agent_action_logs = Table(
    "agent_action_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("task_id", String(36), nullable=False, index=True),
    Column("agent_name", String(64), nullable=False),
    Column("event_type", String(64), nullable=False, default="log"),
    Column("message", Text, nullable=False, default=""),
    Column("payload", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class PersistenceError(RuntimeError):
    """Raised when swarm persistence operations fail."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_persistence_schema(engine: Optional[Engine] = None) -> None:
    """Create persistence tables if they do not already exist."""
    eng = engine or create_db_engine()
    try:
        metadata.create_all(eng, checkfirst=True)
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to ensure persistence schema: {exc}") from exc


def create_swarm_task(
    task_id: str,
    dataset_path: str,
    *,
    engine: Optional[Engine] = None,
) -> None:
    """Insert a pending swarm task row."""
    eng = engine or create_db_engine()
    now = _utcnow()
    stmt = insert(swarm_tasks).values(
        id=str(task_id),
        dataset_path=dataset_path,
        status="running",
        schema_info={},
        statistical_summary={},
        cleaning_code_history=[],
        logs=[],
        iterations=0,
        error=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    try:
        with eng.begin() as conn:
            conn.execute(stmt)
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to create swarm task '{task_id}': {exc}") from exc


def append_agent_action_log(
    task_id: str,
    *,
    agent_name: str,
    message: str,
    event_type: str = "log",
    payload: Optional[Dict[str, Any]] = None,
    engine: Optional[Engine] = None,
) -> None:
    """Append one agent action / stream event for a task."""
    eng = engine or create_db_engine()
    stmt = insert(agent_action_logs).values(
        task_id=str(task_id),
        agent_name=agent_name,
        event_type=event_type,
        message=message,
        payload=payload or {},
        created_at=_utcnow(),
    )
    try:
        with eng.begin() as conn:
            conn.execute(stmt)
    except SQLAlchemyError as exc:
        # Logging failures should not tear down the swarm worker.
        logger.warning("failed to append agent action log for %s: %s", task_id, exc)


def persist_swarm_result(
    task_id: str,
    *,
    status: str,
    dataset_path: str,
    schema_info: Optional[Dict[str, Any]] = None,
    statistical_summary: Optional[Dict[str, Any]] = None,
    cleaning_code_history: Optional[List[str]] = None,
    logs: Optional[List[str]] = None,
    iterations: int = 0,
    error: Optional[str] = None,
    snapshot_logs: bool = False,
    engine: Optional[Engine] = None,
) -> None:
    """
    Upsert the final (or latest) swarm summary into ``swarm_tasks``.

    Set ``snapshot_logs=True`` to also mirror ``logs`` into ``agent_action_logs``
    (skipped by default when stream events were already persisted incrementally).
    """
    eng = engine or create_db_engine()
    now = _utcnow()
    completed = now if status in {"completed", "failed", "cancelled"} else None

    values = {
        "dataset_path": dataset_path,
        "status": status,
        "schema_info": schema_info or {},
        "statistical_summary": statistical_summary or {},
        "cleaning_code_history": cleaning_code_history or [],
        "logs": logs or [],
        "iterations": int(iterations),
        "error": error,
        "updated_at": now,
        "completed_at": completed,
    }

    try:
        with eng.begin() as conn:
            existing = conn.execute(
                select(swarm_tasks.c.id).where(swarm_tasks.c.id == str(task_id))
            ).first()
            if existing:
                conn.execute(
                    update(swarm_tasks).where(swarm_tasks.c.id == str(task_id)).values(**values)
                )
            else:
                conn.execute(
                    insert(swarm_tasks).values(
                        id=str(task_id),
                        created_at=now,
                        **values,
                    )
                )

            if snapshot_logs and logs:
                for line in logs:
                    agent_name = _infer_agent_from_log(line)
                    conn.execute(
                        insert(agent_action_logs).values(
                            task_id=str(task_id),
                            agent_name=agent_name,
                            event_type="summary_log",
                            message=line,
                            payload={},
                            created_at=now,
                        )
                    )
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to persist swarm result for '{task_id}': {exc}") from exc


def get_swarm_task(task_id: str, *, engine: Optional[Engine] = None) -> Optional[Dict[str, Any]]:
    """Fetch a swarm task row as a plain dict."""
    eng = engine or create_db_engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(
                select(swarm_tasks).where(swarm_tasks.c.id == str(task_id))
            ).mappings().first()
            return dict(row) if row else None
    except SQLAlchemyError as exc:
        raise PersistenceError(f"failed to load swarm task '{task_id}': {exc}") from exc


def _infer_agent_from_log(line: str) -> str:
    text = line.strip()
    if text.startswith("[") and "]" in text:
        return text[1 : text.index("]")].strip() or "system"
    return "system"


__all__: Sequence[str] = (
    "PersistenceError",
    "agent_action_logs",
    "append_agent_action_log",
    "create_swarm_task",
    "ensure_persistence_schema",
    "get_swarm_task",
    "persist_swarm_result",
    "swarm_tasks",
)
