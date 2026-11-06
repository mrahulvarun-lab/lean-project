"""Orchestration: tie the repository and the engine together for the API/CLI."""

from __future__ import annotations

import sqlite3

from .config import DEFAULT_CONFIG, RecommenderConfig
from .recommender import Recommendation, recommend
from .repository import (
    User,
    get_all_circles,
    get_user,
    reference_now,
    topic_labels,
)


class RecommendationService:
    """Loads reference data once per connection and serves ranked circles.

    The circle catalogue and topic labels are small and read-mostly, so we
    cache them on the service instance; a write path would invalidate them.
    """

    def __init__(self, conn: sqlite3.Connection, cfg: RecommenderConfig = DEFAULT_CONFIG):
        self.conn = conn
        self.cfg = cfg
        self._circles = None
        self._labels = None
        self._now = None

    def _ensure_loaded(self):
        if self._circles is None:
            self._circles = get_all_circles(self.conn, self.cfg.recency_window_days)
            self._labels = topic_labels(self.conn)
            self._now = reference_now(self.conn)

    def get_user(self, user_id: int) -> User | None:
        return get_user(self.conn, user_id)

    def recommend_for_user(
        self, user_id: int, limit: int | None = None
    ) -> tuple[User | None, list[Recommendation], str]:
        self._ensure_loaded()
        user = get_user(self.conn, user_id)
        if user is None:
            return None, [], ""
        recs, strategy = recommend(
            user, self._circles, self._now, self._labels, limit=limit, cfg=self.cfg
        )
        return user, recs, strategy
