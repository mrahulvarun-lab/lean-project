"""Unit tests for the scoring components and diversification logic.

These exercise the pure functions directly with hand-built objects, so a
regression in (say) the health formula fails here with a precise message rather
than surfacing as a mysterious ordering change in a scenario test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import DEFAULT_CONFIG as CFG
from app.recommender import (
    Recommendation,
    are_near_duplicates,
    capacity_score,
    diversify,
    health_score,
    topic_score,
)
from app.repository import Circle, User

NOW = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)


def mk_circle(cid=1, topic="negotiation", name="Negotiation Circle", **over) -> Circle:
    base = dict(
        id=cid,
        name=name,
        slug=name.lower().replace(" ", "-"),
        description=f"A circle about {topic}.",
        topic_slug=topic,
        cover_image_url=None,
        format="virtual",
        access="public",
        join_policy="open",
        max_members=20,
        member_count=10,
        created_at="2026-06-01T00:00:00Z",
        last_activity_at=(NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        feed_posts_30d=10,
        chat_messages_30d=120,
        tags=[topic],
        recent_engagement_total=150,
        recent_engagement_posts=10,
    )
    base.update(over)
    return Circle(**base)


def mk_user(uid=1, interests=("negotiation",)) -> User:
    return User(
        id=uid, first_name="T", last_name="U", has_photo=True, bio=None,
        job_title=None, company=None, industry=None, location=None,
        joined_at="2026-01-01T00:00:00Z", profile_completeness=0.5,
        topic_interests=list(interests), member_circle_ids=set(),
    )


# --- health -----------------------------------------------------------------
def test_health_active_circle_is_high():
    h, _ = health_score(mk_circle(), NOW, CFG)
    assert h >= 0.8


def test_health_inactive_circle_is_near_zero():
    inactive = mk_circle(
        feed_posts_30d=0,
        chat_messages_30d=2,
        last_activity_at=(NOW - timedelta(days=220)).isoformat().replace("+00:00", "Z"),
        recent_engagement_total=0,
        recent_engagement_posts=0,
    )
    h, breakdown = health_score(inactive, NOW, CFG)
    assert h < 0.05
    assert breakdown["recency"] == 0.0


def test_health_recency_decays_over_window():
    mid = mk_circle(
        last_activity_at=(NOW - timedelta(days=15)).isoformat().replace("+00:00", "Z")
    )
    _, b = health_score(mid, NOW, CFG)
    assert 0.4 < b["recency"] < 0.6  # ~halfway through a 30-day window


# --- topic ------------------------------------------------------------------
def test_topic_direct_match():
    s, slug, kind = topic_score(mk_user(interests=("negotiation",)), mk_circle(), CFG)
    assert (s, kind) == (CFG.topic_direct_match, "direct")
    assert slug == "negotiation"


def test_topic_tags_are_not_used_for_relevance():
    # circle topic is 'networking' but it carries a 'negotiation' tag; the user
    # only likes 'negotiation'. Tags must NOT create a match (noisy signal).
    circle = mk_circle(topic="networking", tags=["negotiation", "confidence"])
    s, slug, kind = topic_score(mk_user(interests=("negotiation",)), circle, CFG)
    assert (s, slug, kind) == (0.0, None, "none")


def test_topic_no_interests_scores_zero():
    s, slug, kind = topic_score(mk_user(interests=()), mk_circle(), CFG)
    assert s == 0.0 and kind == "none"


# --- capacity ---------------------------------------------------------------
def test_capacity_uncapped_is_full_headroom():
    score, seats = capacity_score(mk_circle(max_members=None))
    assert score == 1.0 and seats is None


def test_capacity_nearly_full_is_low():
    score, seats = capacity_score(mk_circle(max_members=20, member_count=19))
    assert seats == 1 and score < 0.1


# --- near-duplicate detection -----------------------------------------------
def test_near_duplicates_same_topic_shared_name_token():
    a = mk_circle(1, name="Negotiation Circle")
    b = mk_circle(2, name="The Negotiation Table")
    assert are_near_duplicates(a, b, CFG)


def test_distinct_same_topic_names_are_not_duplicates():
    a = mk_circle(1, topic="leadership-skills", name="The Elevation Circle", tags=["leadership-skills"])
    b = mk_circle(2, topic="leadership-skills", name="Women Who Lead", tags=["resilience"])
    assert not are_near_duplicates(a, b, CFG)


def test_different_topics_never_duplicates():
    a = mk_circle(1, topic="negotiation", name="Negotiation Circle")
    b = mk_circle(2, topic="confidence", name="Negotiation Circle")  # same name, diff topic
    assert not are_near_duplicates(a, b, CFG)


# --- diversification --------------------------------------------------------
def _rec(cid, topic, score):
    return Recommendation(circle=mk_circle(cid, topic=topic), score=score, strategy="interest_based")


def test_diversify_caps_per_topic():
    # Enough diverse candidates exist to fill the list, so the per-topic cap
    # holds: the 3rd negotiation circle is dropped in favour of other topics.
    recs = [
        _rec(1, "negotiation", 0.90),
        _rec(2, "negotiation", 0.80),
        _rec(3, "negotiation", 0.70),  # 3rd of same topic -> deferred, not needed
        _rec(4, "confidence", 0.60),
        _rec(5, "confidence", 0.55),
        _rec(6, "leadership-skills", 0.50),
    ]
    out = diversify(recs, 5, CFG)
    topics = [r.circle.topic_slug for r in out]
    assert topics.count("negotiation") == CFG.max_per_topic  # exactly 2, not 3
    assert 3 not in [r.circle.id for r in out]


def test_diversify_backfills_when_too_few_diverse_candidates():
    # If capping would leave the list short of `limit`, we backfill past the cap
    # rather than return a thin list. (In the full pipeline, near-duplicate
    # collapse removes true look-alikes before this stage.)
    recs = [_rec(i, "negotiation", 1.0 - i * 0.1) for i in range(5)]
    out = diversify(recs, 4, CFG)
    assert len(out) == 4  # all four returned despite the per-topic cap
