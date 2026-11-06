"""Shared test fixtures.

A single throwaway SQLite database is seeded once from the real data/*.json
fixtures and pointed at via CIRCLES_DB_PATH *before* the app package is
imported, so both the service-level tests and the HTTP tests exercise the same
deterministic data without touching the developer's local circles.db.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

# IMPORTANT: set the DB path before importing anything under `app`, because
# app.config reads CIRCLES_DB_PATH at import time.
_TMPDIR = tempfile.mkdtemp(prefix="circles_test_")
os.environ["CIRCLES_DB_PATH"] = os.path.join(_TMPDIR, "test.db")

import pytest  # noqa: E402

from app.db import connect  # noqa: E402
from app.seed import seed  # noqa: E402
from app.service import RecommendationService  # noqa: E402

# Seed once at collection time.
_seed_conn = connect()
seed(_seed_conn)
_seed_conn.close()


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = connect()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture()
def service(conn: sqlite3.Connection) -> RecommendationService:
    return RecommendationService(conn)


@pytest.fixture()
def client():
    """FastAPI TestClient wired to the seeded test database."""
    from fastapi.testclient import TestClient

    from app.main import app, get_conn

    def _override():
        c = connect()
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _override
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
