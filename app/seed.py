"""Load the synthetic seed data (data/*.json) into SQLite.

Run directly:  ``python -m app.seed``

The script is deterministic and idempotent: it rebuilds the schema and reloads
every row on each run, so the DB is always a faithful projection of the JSON
fixtures. Keeping the loader separate from the JSON shapes means the API never
has to mirror the fixture layout — it reads the normalized tables instead.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import DATA_DIR, DB_PATH
from .db import connect, init_schema


def _load_json(name: str) -> list[dict]:
    path = Path(DATA_DIR) / name
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def seed(conn: sqlite3.Connection, data_dir: Path | None = None) -> dict[str, int]:
    """Populate all tables from the JSON fixtures. Returns per-table row counts."""
    global DATA_DIR  # noqa: PLW0603 - allow test override via arg
    if data_dir is not None:
        base = Path(data_dir)
    else:
        base = Path(DATA_DIR)

    def load(name: str) -> list[dict]:
        with (base / name).open(encoding="utf-8") as fh:
            return json.load(fh)

    topics = load("topics.json")
    users = load("users.json")
    circles = load("circles.json")
    memberships = load("memberships.json")
    activity = load("activity.json")

    init_schema(conn)
    cur = conn.cursor()

    # topics -----------------------------------------------------------------
    cur.executemany(
        "INSERT INTO topics (id, slug, label, domain) VALUES (?, ?, ?, ?)",
        [(t["id"], t["slug"], t["label"], t.get("domain", "content")) for t in topics],
    )

    # users + their interests -----------------------------------------------
    cur.executemany(
        """INSERT INTO users
               (id, first_name, last_name, has_photo, bio, job_title, company,
                industry, location, joined_at, profile_completeness)
           VALUES (:id, :first_name, :last_name, :has_photo, :bio, :job_title,
                   :company, :industry, :location, :joined_at, :profile_completeness)""",
        [
            {
                "id": u["id"],
                "first_name": u.get("first_name"),
                "last_name": u.get("last_name"),
                "has_photo": 1 if u.get("has_photo") else 0,
                "bio": u.get("bio"),
                "job_title": u.get("job_title"),
                "company": u.get("company"),
                "industry": u.get("industry"),
                "location": u.get("location"),
                "joined_at": u["joined_at"],
                "profile_completeness": u.get("profile_completeness", 0),
            }
            for u in users
        ],
    )
    interest_rows = [
        (u["id"], slug)
        for u in users
        for slug in (u.get("topic_interests") or [])
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO user_topic_interests (user_id, topic_slug) VALUES (?, ?)",
        interest_rows,
    )

    # circles + tags ---------------------------------------------------------
    cur.executemany(
        """INSERT INTO circles
               (id, name, slug, description, topic_slug, cover_image_url, format,
                access, join_policy, max_members, member_count, created_at,
                last_activity_at, feed_posts_30d, chat_messages_30d)
           VALUES (:id, :name, :slug, :description, :topic, :cover_image_url, :format,
                   :access, :join_policy, :max_members, :member_count, :created_at,
                   :last_activity_at, :feed_posts_30d, :chat_messages_30d)""",
        [
            {
                "id": c["id"],
                "name": c["name"],
                "slug": c["slug"],
                "description": c.get("description"),
                "topic": c.get("topic"),
                "cover_image_url": c.get("cover_image_url"),
                "format": c["format"],
                "access": c["access"],
                "join_policy": c["join_policy"],
                "max_members": c.get("max_members"),
                "member_count": c.get("member_count", 0),
                "created_at": c["created_at"],
                "last_activity_at": c.get("last_activity_at"),
                "feed_posts_30d": c.get("feed_posts_30d", 0),
                "chat_messages_30d": c.get("chat_messages_30d", 0),
            }
            for c in circles
        ],
    )
    tag_rows = [
        (c["id"], tag)
        for c in circles
        for tag in (c.get("tags") or [])
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO circle_tags (circle_id, tag) VALUES (?, ?)",
        tag_rows,
    )

    # memberships ------------------------------------------------------------
    cur.executemany(
        """INSERT OR IGNORE INTO memberships (user_id, circle_id, role, joined_at)
           VALUES (?, ?, ?, ?)""",
        [(m["user_id"], m["circle_id"], m.get("role", "member"), m["joined_at"]) for m in memberships],
    )

    # activity ---------------------------------------------------------------
    cur.executemany(
        """INSERT INTO activity
               (id, circle_id, author_user_id, body, reaction_count,
                comment_count, save_count, created_at)
           VALUES (:id, :circle_id, :author_user_id, :body, :reaction_count,
                   :comment_count, :save_count, :created_at)""",
        [
            {
                "id": a["id"],
                "circle_id": a["circle_id"],
                "author_user_id": a["author_user_id"],
                "body": a.get("body", ""),
                "reaction_count": a.get("reaction_count", 0),
                "comment_count": a.get("comment_count", 0),
                "save_count": a.get("save_count", 0),
                "created_at": a["created_at"],
            }
            for a in activity
        ],
    )

    conn.commit()

    return {
        "topics": len(topics),
        "users": len(users),
        "user_topic_interests": len(interest_rows),
        "circles": len(circles),
        "circle_tags": len(tag_rows),
        "memberships": len(memberships),
        "activity": len(activity),
    }


def main() -> None:
    conn = connect()
    try:
        counts = seed(conn)
    finally:
        conn.close()
    print(f"Seeded {DB_PATH}")
    for table, n in counts.items():
        print(f"  {table:24s} {n:>5d} rows")


if __name__ == "__main__":
    main()
