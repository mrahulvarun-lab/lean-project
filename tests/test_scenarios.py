"""The four planted scenarios from the dataset README.

These are the behavioural acceptance tests: they assert the *product* outcome
the assignment asks for, not implementation details.
"""

from __future__ import annotations

from collections import Counter

import pytest

# Planted circle ids (from data/DATASET.md).
LEADERSHIP_A, PUBLIC_SPEAKING_B = 5, 6
NEGOTIATION_DUPES = {10, 11, 12}
CONFIDENCE_MATCH = 13
FULL_TRAP, INACTIVE_TRAP, HEALTHY_PROMO = 20, 21, 22


def _ids(recs):
    return [r.circle.id for r in recs]


def _reason_codes(rec):
    return {r.code for r in rec.reasons}


# --- Scenario 1: cold start -------------------------------------------------
def test_cold_start_user_42(service):
    user, recs, strategy = service.recommend_for_user(42, limit=5)
    assert strategy == "cold_start"
    assert user.topic_interests == []
    assert len(recs) == 5, "cold-start user should still get a full list"
    for r in recs:
        c = r.circle
        assert c.is_public, f"recommended non-public circle #{c.id}"
        assert not c.is_full, f"recommended full circle #{c.id}"
        assert r.why, "every recommendation needs a member-facing explanation"
    # Diversified: no single topic dominates the newcomer's list.
    topic_counts = Counter(r.circle.topic_slug for r in recs if r.circle.topic_slug)
    assert all(n <= 2 for n in topic_counts.values())
    # Ranked by community health, so the top pick is genuinely active.
    assert recs[0].signals["health"] >= 0.7


# --- Scenario 2: obvious matches --------------------------------------------
def test_obvious_matches_user_7(service):
    user, recs, strategy = service.recommend_for_user(7, limit=5)
    assert strategy == "interest_based"
    ids = _ids(recs)

    # Both planted strong matches surface.
    assert LEADERSHIP_A in ids, "Women Who Lead (#5) should be recommended"
    assert PUBLIC_SPEAKING_B in ids, "Speak Up Circle (#6) should be recommended"

    # Already-joined circle is never recommended.
    assert 4 not in ids

    # The leaders of the list are genuine topic matches (not health backfill).
    top3 = recs[:3]
    assert all("topic_match" in _reason_codes(r) for r in top3)
    for r in top3:
        assert r.circle.topic_slug in {"leadership-skills", "public-speaking"}


# --- Scenario 3: duplicate circles ------------------------------------------
def test_duplicate_circles_user_15(service):
    user, recs, strategy = service.recommend_for_user(15, limit=5)
    ids = _ids(recs)

    shown_negotiation = NEGOTIATION_DUPES & set(ids)
    assert len(shown_negotiation) == 1, (
        f"a good list shouldn't return all near-identical Negotiation circles; "
        f"got {shown_negotiation}"
    )

    # The surviving negotiation circle absorbed the other two as near-duplicates.
    rep = next(r for r in recs if r.circle.id in NEGOTIATION_DUPES)
    collapsed = set(rep.similar_circle_ids) | {rep.circle.id}
    assert NEGOTIATION_DUPES <= collapsed, "the trio should be folded into one entry"

    # The member's other interest (confidence) is still represented.
    assert CONFIDENCE_MATCH in ids


# --- Scenario 4: full / inactive traps --------------------------------------
def test_full_inactive_user_23(service):
    user, recs, strategy = service.recommend_for_user(23, limit=5)
    ids = _ids(recs)

    assert FULL_TRAP not in ids, "full circle (#20) is unjoinable and must not appear"
    assert INACTIVE_TRAP not in ids, "inactive circle (#21) must not rank"
    assert HEALTHY_PROMO in ids, "the healthy alternative (#22) should be recommended"

    # #22 is the top result and is there because it matches the interest.
    assert ids[0] == HEALTHY_PROMO
    assert "topic_match" in _reason_codes(recs[0])


# --- Global invariants ------------------------------------------------------
@pytest.mark.parametrize("user_id", list(range(1, 101)))
def test_every_user_gets_sane_recommendations(service, user_id):
    user, recs, strategy = service.recommend_for_user(user_id, limit=5)
    assert user is not None
    assert recs, f"user {user_id} got an empty list"
    ids = _ids(recs)
    assert len(ids) == len(set(ids)), "no duplicate circles in a list"
    topic_counts = Counter(r.circle.topic_slug for r in recs if r.circle.topic_slug)
    assert all(n <= 2 for n in topic_counts.values()), "per-topic cap respected"
    for r in recs:
        c = r.circle
        assert c.id not in user.member_circle_ids, "never recommend a joined circle"
        assert not c.is_full, "never recommend a full circle"
        assert c.is_public, "never recommend an unlisted circle"
        assert 0.0 <= r.score <= 1.2
        assert r.why
