"""FastAPI application exposing Circle recommendations.

Endpoints
    GET /health                              liveness
    GET /users/{user_id}                     member summary (debugging)
    GET /users/{user_id}/recommendations     ranked circles + explanations

Run locally:
    python -m app.seed                 # build circles.db from data/*.json
    uvicorn app.main:app --reload
Then:
    curl 'http://localhost:8000/users/7/recommendations?limit=5'

The app self-seeds on startup if the DB is missing, so a fresh clone works
with a single `uvicorn` command too.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query

from . import __version__
from .config import DB_PATH, DEFAULT_CONFIG
from .db import connect, database_is_populated
from .models import (
    RecommendationOut,
    RecommendationsResponse,
    ReasonOut,
    SimilarCircleOut,
    UserSummary,
)
from .recommender import Recommendation
from .seed import seed
from .service import RecommendationService


def _ensure_db() -> None:
    """Seed the database on first run so the service is turnkey."""
    if not database_is_populated(DB_PATH):
        conn = connect()
        try:
            seed(conn)
        finally:
            conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_db()
    yield


app = FastAPI(
    title="Lean In Connect — Circle Recommendations",
    version=__version__,
    summary="Ranked, explainable Circle recommendations for a member.",
    lifespan=lifespan,
)


def get_conn() -> Iterator[sqlite3.Connection]:
    """Per-request read-only SQLite connection.

    A fresh connection per request keeps us clear of SQLite's thread affinity
    (FastAPI runs sync endpoints in a worker-thread pool) and is cheap for a
    local file DB. A production Postgres deployment would use a pooled
    connection here instead.
    """
    conn = connect()
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def _to_out(rec: Recommendation, name_of: dict[int, str]) -> RecommendationOut:
    c = rec.circle
    return RecommendationOut(
        circle_id=c.id,
        name=c.name,
        slug=c.slug,
        topic=c.topic_slug,
        format=c.format,
        join_policy=c.join_policy,
        member_count=c.member_count,
        max_members=c.max_members,
        score=rec.score,
        why=rec.why,
        reasons=[ReasonOut(code=r.code, detail=r.detail) for r in rec.reasons],
        similar_circles=[
            SimilarCircleOut(circle_id=cid, name=name_of.get(cid, "")) for cid in rec.similar_circle_ids
        ],
        signals=rec.signals,
    )


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/users/{user_id}", response_model=UserSummary, tags=["users"])
def get_user_summary(user_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> UserSummary:
    service = RecommendationService(conn, DEFAULT_CONFIG)
    user = service.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    return UserSummary(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        topic_interests=user.topic_interests,
        profile_completeness=user.profile_completeness,
        member_circle_ids=sorted(user.member_circle_ids),
    )


@app.get(
    "/users/{user_id}/recommendations",
    response_model=RecommendationsResponse,
    tags=["recommendations"],
)
def get_recommendations(
    user_id: int,
    limit: int = Query(
        default=DEFAULT_CONFIG.default_limit,
        ge=1,
        le=DEFAULT_CONFIG.max_limit,
        description="How many circles to return",
    ),
    debug: bool = Query(
        default=False, description="Include the per-signal scoring breakdown"
    ),
    conn: sqlite3.Connection = Depends(get_conn),
) -> RecommendationsResponse:
    service = RecommendationService(conn, DEFAULT_CONFIG)
    user, recs, strategy = service.recommend_for_user(user_id, limit=limit)
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    name_of = {c.id: c.name for c in service._circles}
    out = []
    for rec in recs:
        item = _to_out(rec, name_of)
        if not debug:
            item.signals = {}
        out.append(item)

    return RecommendationsResponse(
        user_id=user_id,
        strategy=strategy,
        count=len(out),
        recommendations=out,
    )
