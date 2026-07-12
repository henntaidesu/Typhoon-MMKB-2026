"""Initialize the MMKB database purely through ORM database objects.

Steps (no .sql files involved):
  1. Ensure the target database exists (created via the maintenance connection).
  2. CREATE EXTENSION postgis + vector, executed through a SQLAlchemy session.
  3. Base.metadata.create_all() builds every table + spatial/vector index.
  4. Reconcile any columns added to the ORM after a table was first created.

This module is importable: the FastAPI app calls ``initialize()`` on startup so
the schema is created automatically on first run. It is also runnable directly:

    conda activate MMKB && python backend/init_db.py
"""
from __future__ import annotations

import sys

from sqlalchemy import create_engine, inspect, text

from config import DB_NAME, DATABASE_URL, MAINTENANCE_URL
import models  # noqa: F401  (import registers all tables on Base.metadata)
from models import Base


def _decode(e: Exception) -> str:
    """PostgreSQL on a zh-CN Windows server emits GBK-encoded error text.
    Recover the readable message instead of leaving mojibake in the logs."""
    try:
        return str(e).encode("latin1", "ignore").decode("gbk", "ignore")
    except Exception:
        return str(e)


def ensure_database(verbose: bool = True) -> None:
    """Create the target database if it does not exist (needs autocommit)."""
    maint = create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT", future=True)
    try:
        with maint.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": DB_NAME}
            ).scalar()
            if exists:
                if verbose:
                    print(f"[db] database '{DB_NAME}' already exists")
            else:
                # DB_NAME is quoted so a mixed-case name like "MMKB" is preserved.
                conn.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
                if verbose:
                    print(f"[db] created database '{DB_NAME}'")
    finally:
        maint.dispose()


def ensure_extensions(engine, verbose: bool = True) -> None:
    """Create PostGIS + pgvector extensions via an ORM connection."""
    with engine.begin() as conn:
        for ext in ("postgis", "vector"):
            try:
                conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
                if verbose:
                    print(f"[db] extension '{ext}' ready")
            except Exception as e:  # pragma: no cover - surfaced to the user
                print(f"[db] FAILED to create extension '{ext}': {_decode(e)}")
                if ext == "vector":
                    print(
                        "     -> pgvector is not installed on the server "
                        f"({engine.url.host}). Deploy it first: run "
                        "pgvector-pg18-win-x64\\deploy_on_server.ps1 on that host."
                    )
                raise


def reconcile_columns(engine, verbose: bool = True) -> None:
    """Add columns that exist in the ORM models but are missing from an
    already-created table. ``create_all`` only creates whole missing tables;
    this handles the 'a new field was added later' case with plain ALTER TABLE.

    Note: this only ADDS columns. It never drops or retypes existing ones —
    destructive schema changes still need a real migration.
    """
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all already built this one in full
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                nullable = "" if col.nullable else " NOT NULL"
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" '
                    f'ADD COLUMN "{col.name}" {coltype}{nullable}'
                ))
                if verbose:
                    print(f"[db] added missing column {table.name}.{col.name} ({coltype})")


def initialize(verbose: bool = True):
    """Idempotent full setup: database + extensions + tables + column reconcile.
    Safe to call on every startup. Returns the engine bound to the target DB."""
    ensure_database(verbose=verbose)
    engine = create_engine(DATABASE_URL, future=True)
    ensure_extensions(engine, verbose=verbose)
    Base.metadata.create_all(engine)
    reconcile_columns(engine, verbose=verbose)
    return engine


def main() -> int:
    engine = initialize(verbose=True)
    # Report what got built.
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name"
            )
        ).scalars().all()
        pg = conn.execute(text("SELECT postgis_full_version()")).scalar()
    print(f"[db] tables: {tables}")
    print(f"[db] postgis: {pg[:60] if pg else pg}")
    print("[db] init complete.")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
