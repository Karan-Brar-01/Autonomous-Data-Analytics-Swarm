"""Supabase / PostgreSQL schema inspection and column profiling via SQLAlchemy."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator, Optional, Sequence

from pydantic import BaseModel, Field
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select
from sqlalchemy.engine import Engine, Inspector
from sqlalchemy.exc import SQLAlchemyError

from config import Settings, get_settings

_NUMERIC_TYPE_HINTS: frozenset[str] = frozenset(
    {
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "DECIMAL",
        "NUMERIC",
        "REAL",
        "DOUBLE PRECISION",
        "SMALLSERIAL",
        "SERIAL",
        "BIGSERIAL",
        "FLOAT",
        "DOUBLE",
        "INT2",
        "INT4",
        "INT8",
        "FLOAT4",
        "FLOAT8",
        "MONEY",
    }
)


class DatabaseInspectorError(RuntimeError):
    """Raised when database inspection or profiling fails."""


class ColumnInfo(BaseModel):
    """One column from a table schema."""

    name: str
    data_type: str
    nullable: bool
    default: Optional[str] = None
    is_primary_key: bool = False


class TableSchema(BaseModel):
    """Structured table schema snapshot."""

    table_name: str
    schema_name: str
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)


class MissingValueStat(BaseModel):
    """Null / missing-value ratio for a single column."""

    column_name: str
    total_rows: int
    null_count: int
    null_ratio: float


class ColumnSummary(BaseModel):
    """Statistical summary for a single column."""

    column_name: str
    data_type: str
    total_rows: int
    non_null_count: int
    null_count: int
    null_ratio: float
    distinct_count: Optional[int] = None
    mean: Optional[float] = None
    stddev: Optional[float] = None
    min_value: Optional[str] = None
    max_value: Optional[str] = None


class TableProfile(BaseModel):
    """Combined schema + missingness + statistical profile for a table."""

    table_schema: TableSchema
    row_count: int
    missing_values: list[MissingValueStat] = Field(default_factory=list)
    column_summaries: list[ColumnSummary] = Field(default_factory=list)


def create_db_engine(
    database_url: Optional[str] = None,
    *,
    settings: Optional[Settings] = None,
    pool_pre_ping: bool = True,
) -> Engine:
    """Create a SQLAlchemy engine for Supabase / PostgreSQL."""
    cfg = settings or get_settings()
    url = database_url or cfg.database_url
    if not url or not str(url).strip():
        raise DatabaseInspectorError("DATABASE_URL is empty; set it in the environment or .env")

    try:
        return create_engine(
            url,
            pool_pre_ping=pool_pre_ping,
            future=True,
        )
    except SQLAlchemyError as exc:
        raise DatabaseInspectorError(f"failed to create database engine: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — invalid URL formats, etc.
        raise DatabaseInspectorError(f"failed to create database engine: {exc}") from exc


@contextmanager
def engine_scope(
    database_url: Optional[str] = None,
    *,
    settings: Optional[Settings] = None,
) -> Generator[Engine, None, None]:
    """Yield an engine and dispose it on exit."""
    engine = create_db_engine(database_url, settings=settings)
    try:
        yield engine
    finally:
        engine.dispose()


def list_tables(
    engine: Engine,
    *,
    schema: Optional[str] = None,
) -> list[str]:
    """Return user table names in the given schema (default: public / inspector default)."""
    try:
        inspector = inspect(engine)
        schema_name = schema or _default_schema(inspector)
        return sorted(inspector.get_table_names(schema=schema_name))
    except SQLAlchemyError as exc:
        raise DatabaseInspectorError(f"failed to list tables: {exc}") from exc


def inspect_table_schema(
    engine: Engine,
    table_name: str,
    *,
    schema: Optional[str] = None,
) -> TableSchema:
    """Inspect column types, nullability, primary key, and index names for a table."""
    _validate_identifier(table_name, what="table_name")
    if schema is not None:
        _validate_identifier(schema, what="schema")

    try:
        inspector = inspect(engine)
        schema_name = schema or _default_schema(inspector)
        if table_name not in inspector.get_table_names(schema=schema_name):
            raise DatabaseInspectorError(
                f"table '{schema_name}.{table_name}' was not found"
            )

        pk = inspector.get_pk_constraint(table_name, schema=schema_name) or {}
        pk_cols = list(pk.get("constrained_columns") or [])
        pk_set = set(pk_cols)

        columns: list[ColumnInfo] = []
        for col in inspector.get_columns(table_name, schema=schema_name):
            columns.append(
                ColumnInfo(
                    name=str(col["name"]),
                    data_type=_stringify_type(col.get("type")),
                    nullable=bool(col.get("nullable", True)),
                    default=_stringify_default(col.get("default")),
                    is_primary_key=str(col["name"]) in pk_set,
                )
            )

        indexes = [
            str(idx.get("name"))
            for idx in inspector.get_indexes(table_name, schema=schema_name)
            if idx.get("name")
        ]

        return TableSchema(
            table_name=table_name,
            schema_name=schema_name,
            columns=columns,
            primary_key=pk_cols,
            indexes=indexes,
        )
    except DatabaseInspectorError:
        raise
    except SQLAlchemyError as exc:
        raise DatabaseInspectorError(
            f"failed to inspect schema for '{table_name}': {exc}"
        ) from exc


def profile_missing_values(
    engine: Engine,
    table_name: str,
    *,
    schema: Optional[str] = None,
    columns: Optional[Sequence[str]] = None,
) -> list[MissingValueStat]:
    """Compute null counts and ratios for each column (or a subset)."""
    table_schema = inspect_table_schema(engine, table_name, schema=schema)
    target_cols = _resolve_columns(table_schema, columns)
    row_count = _count_rows(engine, table_schema)

    if row_count == 0:
        return [
            MissingValueStat(
                column_name=col.name,
                total_rows=0,
                null_count=0,
                null_ratio=0.0,
            )
            for col in target_cols
        ]

    table = _reflect_table(engine, table_schema)
    stats: list[MissingValueStat] = []

    try:
        with engine.connect() as conn:
            for col_info in target_cols:
                column = table.c[col_info.name]
                null_count = conn.execute(
                    select(func.count()).select_from(table).where(column.is_(None))
                ).scalar_one()
                null_count_i = int(null_count or 0)
                stats.append(
                    MissingValueStat(
                        column_name=col_info.name,
                        total_rows=row_count,
                        null_count=null_count_i,
                        null_ratio=round(null_count_i / row_count, 6),
                    )
                )
    except SQLAlchemyError as exc:
        raise DatabaseInspectorError(
            f"failed to profile missing values for '{table_name}': {exc}"
        ) from exc

    return stats


def column_statistical_summaries(
    engine: Engine,
    table_name: str,
    *,
    schema: Optional[str] = None,
    columns: Optional[Sequence[str]] = None,
) -> list[ColumnSummary]:
    """
    Extract per-column summaries.

    Numeric columns include mean / stddev / min / max.
    All columns include null ratios and approximate distinct counts.
    """
    table_schema = inspect_table_schema(engine, table_name, schema=schema)
    target_cols = _resolve_columns(table_schema, columns)
    row_count = _count_rows(engine, table_schema)
    table = _reflect_table(engine, table_schema)
    summaries: list[ColumnSummary] = []

    try:
        with engine.connect() as conn:
            for col_info in target_cols:
                column = table.c[col_info.name]
                null_count = int(
                    conn.execute(
                        select(func.count()).select_from(table).where(column.is_(None))
                    ).scalar_one()
                    or 0
                )
                non_null = max(row_count - null_count, 0)
                null_ratio = round(null_count / row_count, 6) if row_count else 0.0

                distinct_count = int(
                    conn.execute(
                        select(func.count(func.distinct(column))).select_from(table)
                    ).scalar_one()
                    or 0
                )

                mean: Optional[float] = None
                stddev: Optional[float] = None
                min_value: Optional[str] = None
                max_value: Optional[str] = None

                if non_null > 0:
                    min_raw, max_raw = conn.execute(
                        select(func.min(column), func.max(column)).select_from(table)
                    ).one()
                    min_value = None if min_raw is None else str(min_raw)
                    max_value = None if max_raw is None else str(max_raw)

                    if _is_numeric_type(col_info.data_type):
                        mean_raw, std_raw = conn.execute(
                            select(
                                func.avg(column),
                                func.stddev_samp(column),
                            ).select_from(table)
                        ).one()
                        mean = float(mean_raw) if mean_raw is not None else None
                        stddev = float(std_raw) if std_raw is not None else None

                summaries.append(
                    ColumnSummary(
                        column_name=col_info.name,
                        data_type=col_info.data_type,
                        total_rows=row_count,
                        non_null_count=non_null,
                        null_count=null_count,
                        null_ratio=null_ratio,
                        distinct_count=distinct_count,
                        mean=mean,
                        stddev=stddev,
                        min_value=min_value,
                        max_value=max_value,
                    )
                )
    except SQLAlchemyError as exc:
        raise DatabaseInspectorError(
            f"failed to compute statistical summaries for '{table_name}': {exc}"
        ) from exc

    return summaries


def profile_table(
    engine: Engine,
    table_name: str,
    *,
    schema: Optional[str] = None,
) -> TableProfile:
    """Full table profile: schema, missingness, and column statistics."""
    table_schema = inspect_table_schema(engine, table_name, schema=schema)
    row_count = _count_rows(engine, table_schema)
    missing = profile_missing_values(engine, table_name, schema=table_schema.schema_name)
    summaries = column_statistical_summaries(
        engine, table_name, schema=table_schema.schema_name
    )
    return TableProfile(
        table_schema=table_schema,
        row_count=row_count,
        missing_values=missing,
        column_summaries=summaries,
    )


def _default_schema(inspector: Inspector) -> str:
    default = inspector.default_schema_name
    return default or "public"


def _validate_identifier(value: str, *, what: str) -> None:
    """Allow only SQL-safe identifiers (alphanumeric + underscore, non-digit start)."""
    if (
        not value
        or value[0].isdigit()
        or not all(ch.isalnum() or ch == "_" for ch in value)
    ):
        raise DatabaseInspectorError(f"invalid {what}: {value!r}")


def _stringify_type(sa_type: Any) -> str:
    if sa_type is None:
        return "UNKNOWN"
    try:
        return str(sa_type).upper()
    except Exception:  # noqa: BLE001
        return type(sa_type).__name__.upper()


def _stringify_default(default: Any) -> Optional[str]:
    if default is None:
        return None
    return str(default)


def _is_numeric_type(data_type: str) -> bool:
    normalized = data_type.upper()
    base = normalized.split("(", 1)[0].strip()
    if base in _NUMERIC_TYPE_HINTS:
        return True
    return any(token in normalized for token in ("INT", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL"))


def _resolve_columns(
    table_schema: TableSchema,
    columns: Optional[Sequence[str]],
) -> list[ColumnInfo]:
    if not columns:
        return list(table_schema.columns)
    wanted = {c.strip() for c in columns if c and c.strip()}
    resolved = [c for c in table_schema.columns if c.name in wanted]
    missing = wanted - {c.name for c in resolved}
    if missing:
        raise DatabaseInspectorError(
            f"unknown columns for '{table_schema.table_name}': {sorted(missing)}"
        )
    return resolved


def _reflect_table(engine: Engine, table_schema: TableSchema) -> Table:
    metadata = MetaData()
    try:
        return Table(
            table_schema.table_name,
            metadata,
            schema=table_schema.schema_name,
            autoload_with=engine,
        )
    except SQLAlchemyError as exc:
        raise DatabaseInspectorError(
            f"failed to reflect table '{table_schema.schema_name}.{table_schema.table_name}': {exc}"
        ) from exc


def _count_rows(engine: Engine, table_schema: TableSchema) -> int:
    # Identifiers already validated; quote via SQLAlchemy Table reflection for safety.
    table = _reflect_table(engine, table_schema)
    try:
        with engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(table)).scalar_one() or 0)
    except SQLAlchemyError as exc:
        raise DatabaseInspectorError(
            f"failed to count rows for '{table_schema.table_name}': {exc}"
        ) from exc


__all__: Sequence[str] = (
    "ColumnInfo",
    "ColumnSummary",
    "DatabaseInspectorError",
    "MissingValueStat",
    "TableProfile",
    "TableSchema",
    "column_statistical_summaries",
    "create_db_engine",
    "engine_scope",
    "inspect_table_schema",
    "list_tables",
    "profile_missing_values",
    "profile_table",
)
