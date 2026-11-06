"""Command-line view of recommendations — handy for manual grading.

    python -m app.cli 7           # recommendations for user 7
    python -m app.cli 42 --limit 6 --debug
    python -m app.cli --scenarios # the four planted scenarios, side by side
"""

from __future__ import annotations

import argparse
import json

from .config import DEFAULT_CONFIG
from .db import connect, database_is_populated
from .seed import seed
from .service import RecommendationService

SCENARIOS = {
    42: "Cold start — no interests, empty profile",
    7: "Obvious matches — leadership-skills + public-speaking",
    15: "Duplicate circles — three near-identical Negotiation circles",
    23: "Full / inactive traps — getting-promoted",
}


def _ensure_db():
    if not database_is_populated():
        conn = connect()
        try:
            seed(conn)
        finally:
            conn.close()


def _print_user(service: RecommendationService, user_id: int, limit: int, debug: bool):
    user, recs, strategy = service.recommend_for_user(user_id, limit=limit)
    if user is None:
        print(f"User {user_id} not found")
        return
    name = f"{user.first_name or ''} {user.last_name or ''}".strip() or f"user {user_id}"
    header = SCENARIOS.get(user_id, "")
    print("=" * 78)
    print(f"USER {user_id} · {name} · strategy={strategy}")
    if header:
        print(f"  scenario: {header}")
    print(f"  interests: {user.topic_interests or '(none)'}  "
          f"completeness: {user.profile_completeness}  "
          f"already in: {sorted(user.member_circle_ids) or '(none)'}")
    print("-" * 78)
    if not recs:
        print("  (no recommendations)")
    for i, rec in enumerate(recs, 1):
        c = rec.circle
        cap = f"{c.member_count}/{c.max_members if c.max_members is not None else '∞'}"
        sim = f"  [+{len(rec.similar_circle_ids)} similar: {rec.similar_circle_ids}]" if rec.similar_circle_ids else ""
        print(f"  {i}. #{c.id:<2} {c.name:<26} score={rec.score:<6} topic={c.topic_slug or '—'}  members={cap}{sim}")
        print(f"       why: {rec.why}")
        if debug:
            print(f"       signals: {json.dumps(rec.signals, default=str)}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Circle recommendations (CLI)")
    parser.add_argument("user_id", nargs="?", type=int, help="user id to rank circles for")
    parser.add_argument("--limit", type=int, default=DEFAULT_CONFIG.default_limit)
    parser.add_argument("--debug", action="store_true", help="show per-signal breakdown")
    parser.add_argument("--scenarios", action="store_true", help="run the four planted scenarios")
    args = parser.parse_args()

    _ensure_db()
    conn = connect()
    try:
        service = RecommendationService(conn)
        if args.scenarios or args.user_id is None:
            for uid in SCENARIOS:
                _print_user(service, uid, args.limit, args.debug)
        else:
            _print_user(service, args.user_id, args.limit, args.debug)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
