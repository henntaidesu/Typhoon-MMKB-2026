"""Shared knowledge-base probe for the DB-backed tests.

Several test modules assert properties of the *loaded* KB. Those can only run
where the database is reachable, so each is guarded by `requires_kb`. Probing
once here keeps the three modules from opening (and leaking) their own
connections just to decide whether to skip.
"""
from __future__ import annotations

import atexit
import os
import sys
import unittest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _probe():
    try:
        from db import engine
        with engine.connect() as c:
            c.exec_driver_sql("select 1")
        # Pooled connections outlive the check and are torn down by the garbage
        # collector at exit, which psycopg reports as a ResourceWarning. Closing
        # the pool explicitly keeps the suite's output clean.
        atexit.register(engine.dispose)
        return engine
    except Exception:  # noqa: BLE001 — an unreachable KB is a skip, not an error
        return None


ENGINE = _probe()

requires_kb = unittest.skipIf(ENGINE is None, "knowledge base not reachable")
