"""Data-access layer: turn SQLite rows into plain dataclasses the engine uses.

Keeping all SQL in one place means the recommender reasons about domain objects
(``User``, ``Circle``) rather than cursors, and the queries that matter for
performance are visible and reviewable in a single file.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------
@dataclass
class User:
    id: int
    first_name: str | None
    last_name: str | None
    has_photo: bool
    bio: str | None
    job_title: str | None
    company: str | None
    industry: str | None
    location: str | None
    joined_at: str
    profile_completeness: float
    topic_interests: list[str] = field(default_factory=list)
    member_circle_ids: set[int] = field(default_factory=set)


@dataclass
class Circle:
    id: int
    name: str
    slug: str
    description: str | None
    topic_slug: str | None
    cover_image_url: str | None
    format: str
    access: str
    join_policy: str
    max_members: int | None
    member_count: int
    created_at: str
    last_activity_at: str | None
    feed_posts_30d: int
    chat_messages_30d: int
    tags: list[str] = field(default_factory=list)
    # Engagement aggregated from the raw activity feed (last 30d vs reference now).
    recent_engagement_total: int = 0
    recent_engagement_posts: int = 0

    @property
    def is_full(self) -> bool:
        return self.max_members is not None and self.member_count >= self.max_members

    @property
    def is_public(self) -> bool:
        return self.access == "public"


# ---------------------------------------------------------------------------
# Time handling
# ---------------------------------------------------------------------------
def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def reference_now(conn: sqlite3.Connection) -> datetime:
    """The dataset's notion of "now".

    The fixture is anchored to a fixed date (the most recent activity is ~Sep
    2026), so measuring recency against the real wall clock would make every
    Circle look stale. We instead define "now" as the latest timestamp present
    in the data (max of last_activity_at and activity.created_at). This keeps
    recency meaningful and the output stable regardless of when it's run.
    """
    row = conn.execute(
        """
        SELECT MAX(ts) AS now FROM (
            SELECT MAX(last_activity_at) AS ts FROM circles
            UNION ALL
            SELECT MAX(created_at)       AS ts FROM activity
        )
        """
    ).fetchone()
    ts = parse_ts(row["now"]) if row and row["now"] else None
    return ts or datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    interests = [
        r["topic_slug"]
        for r in conn.execute(
            "SELECT topic_slug FROM user_topic_interests WHERE user_id = ?", (user_id,)
        )
    ]
    member_ids = {
        r["circle_id"]
        for r in conn.execute(
            "SELECT circle_id FROM memberships WHERE user_id = ?", (user_id,)
        )
    }
    return User(
        id=row["id"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        has_photo=bool(row["has_photo"]),
        bio=row["bio"],
        job_title=row["job_title"],
        company=row["company"],
        industry=row["industry"],
        location=row["location"],
        joined_at=row["joined_at"],
        profile_completeness=row["profile_completeness"],
        topic_interests=interests,
        member_circle_ids=member_ids,
    )


def _engagement_by_circle(
    conn: sqlite3.Connection, since_iso: str
) -> dict[int, tuple[int, int]]:
    """Total engagement and post count per circle since ``since_iso``.

    Computed from the raw activity feed (not a stored aggregate) so the engine
    uses first-party signal and the result reflects the actual posts.
    """
    rows = conn.execute(
        """
        SELECT circle_id,
               SUM(reaction_count + comment_count + save_count) AS eng,
               COUNT(*) AS posts
        FROM activity
        WHERE created_at >= ?
        GROUP BY circle_id
        """,
        (since_iso,),
    ).fetchall()
    return {r["circle_id"]: (r["eng"] or 0, r["posts"] or 0) for r in rows}


def get_all_circles(conn: sqlite3.Connection, window_days: float = 30.0) -> list[Circle]:
    """All circles with tags and recent-engagement aggregates attached."""
    now = reference_now(conn)
    since = now.timestamp() - window_days * 86400
    since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    engagement = _engagement_by_circle(conn, since_iso)

    tags_by_circle: dict[int, list[str]] = {}
    for r in conn.execute("SELECT circle_id, tag FROM circle_tags"):
        tags_by_circle.setdefault(r["circle_id"], []).append(r["tag"])

    circles: list[Circle] = []
    for row in conn.execute("SELECT * FROM circles"):
        eng_total, eng_posts = engagement.get(row["id"], (0, 0))
        circles.append(
            Circle(
                id=row["id"],
                name=row["name"],
                slug=row["slug"],
                description=row["description"],
                topic_slug=row["topic_slug"],
                cover_image_url=row["cover_image_url"],
                format=row["format"],
                access=row["access"],
                join_policy=row["join_policy"],
                max_members=row["max_members"],
                member_count=row["member_count"],
                created_at=row["created_at"],
                last_activity_at=row["last_activity_at"],
                feed_posts_30d=row["feed_posts_30d"],
                chat_messages_30d=row["chat_messages_30d"],
                tags=tags_by_circle.get(row["id"], []),
                recent_engagement_total=eng_total,
                recent_engagement_posts=eng_posts,
            )
        )
    return circles


def topic_labels(conn: sqlite3.Connection) -> dict[str, str]:
    """slug -> display label, for member-facing copy."""
    return {r["slug"]: r["label"] for r in conn.execute("SELECT slug, label FROM topics")}


def get_circle_leaders(conn: sqlite3.Connection, circle_id: int) -> list[sqlite3.Row]:
    """Leader profiles for a circle (role='leader'), joined to users."""
    return conn.execute(
        """
        SELECT u.*
        FROM memberships m
        JOIN users u ON u.id = m.user_id
        WHERE m.circle_id = ? AND m.role = 'leader'
        """,
        (circle_id,),
    ).fetchall()
