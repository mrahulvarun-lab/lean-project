"""The recommendation engine.

Pipeline (see DESIGN.md for the why):

    candidates = all circles
      └─ filter:   drop ones the member is already in, unlisted, or full
      └─ score:    0.60*topic + 0.30*health + 0.10*capacity   (interest-based)
                   or  health-only + open-bonus                (cold start)
      └─ collapse: fold near-duplicate same-topic Circles into their best one
      └─ diversify: greedily pick, capping Circles-per-topic, to avoid a list
                    dominated by one topic
      └─ explain:  attach a short, honest, member-facing "why"

Everything that affects ranking is a pure function of (user, circles, config),
which makes the four planted scenarios straightforward to unit-test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import DEFAULT_CONFIG, RecommenderConfig
from .repository import Circle, User, parse_ts


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class Reason:
    """A single structured explanation the UI can render as a chip."""

    code: str      # e.g. "topic_match", "active", "spots_left"
    detail: str    # human-readable fragment


@dataclass
class Recommendation:
    circle: Circle
    score: float
    strategy: str                 # "interest_based" | "cold_start"
    reasons: list[Reason] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    similar_circle_ids: list[int] = field(default_factory=list)
    why: str = ""


# ---------------------------------------------------------------------------
# Scoring components (each returns a value in [0, 1])
# ---------------------------------------------------------------------------
def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def topic_score(user: User, circle: Circle, cfg: RecommenderConfig) -> tuple[float, str | None, str]:
    """Relevance of a circle to a member's stated interests.

    Returns (score, matched_topic_slug, match_kind). We treat a circle's primary
    ``topic`` as the one reliable relevance key and deliberately do NOT use
    ``tags`` for relevance: in this fixture a circle's tags are a random sample
    of topic slugs (see data/generate.mjs), so matching on them injects noise
    and yields misleading "related to X" copy. Tags are kept as display metadata
    only. With a curated tag vocabulary in production this would become a
    secondary signal. (See DESIGN.md.)
    """
    interests = set(user.topic_interests)
    if not interests:
        return 0.0, None, "none"
    if circle.topic_slug and circle.topic_slug in interests:
        return cfg.topic_direct_match, circle.topic_slug, "direct"
    return 0.0, None, "none"


def health_score(circle: Circle, now, cfg: RecommenderConfig) -> tuple[float, dict]:
    """How alive a circle is: posting, chat, recency, and engagement.

    This is the signal that demotes the planted *inactive* trap (#21): no posts
    in 30 days + last activity ~7 months ago drives every sub-score to ~0.
    """
    norm_posts = _clamp01(circle.feed_posts_30d / cfg.posts_cap)
    norm_chat = _clamp01(circle.chat_messages_30d / cfg.chat_cap)

    last = parse_ts(circle.last_activity_at)
    if last is None:
        recency = 0.0
        days_since = None
    else:
        days_since = max(0.0, (now - last).total_seconds() / 86400.0)
        recency = _clamp01(1.0 - days_since / cfg.recency_window_days)

    if circle.recent_engagement_posts > 0:
        avg_eng = circle.recent_engagement_total / circle.recent_engagement_posts
    else:
        avg_eng = 0.0
    norm_eng = _clamp01(avg_eng / cfg.engagement_cap)

    health = (
        cfg.h_posts * norm_posts
        + cfg.h_chat * norm_chat
        + cfg.h_recency * recency
        + cfg.h_engagement * norm_eng
    )
    breakdown = {
        "norm_posts": round(norm_posts, 3),
        "norm_chat": round(norm_chat, 3),
        "recency": round(recency, 3),
        "norm_engagement": round(norm_eng, 3),
        "days_since_activity": round(days_since, 1) if days_since is not None else None,
        "avg_engagement_per_post": round(avg_eng, 2),
    }
    return _clamp01(health), breakdown


def capacity_score(circle: Circle) -> tuple[float, int | None]:
    """Headroom to join. Uncapped circles always have room (1.0).

    A mild tie-breaker — a Circle one seat from full is slightly less inviting
    than an equally good one with space. Full circles are filtered earlier.
    """
    if circle.max_members is None:
        return 1.0, None
    seats_left = circle.max_members - circle.member_count
    headroom = _clamp01(seats_left / circle.max_members)
    return headroom, max(0, seats_left)


# ---------------------------------------------------------------------------
# Near-duplicate detection
# ---------------------------------------------------------------------------
_NAME_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "to", "at", "in", "on", "with",
    "circle", "circles", "collective", "room", "lab", "guild", "table",
    "club", "group", "network", "coalition", "women", "womens", "united",
    "project", "program",
}


def _name_tokens(name: str) -> set[str]:
    """Salient, stemmed tokens from a circle name (stopwords removed)."""
    raw = re.findall(r"[a-z0-9]+", name.lower())
    toks = set()
    for t in raw:
        if t in _NAME_STOPWORDS or len(t) < 3:
            continue
        toks.add(t[:5])  # crude stem: 5-char prefix (negotiation/negotiate -> "negot")
    return toks


def are_near_duplicates(a: Circle, b: Circle, cfg: RecommenderConfig) -> bool:
    """Two circles are near-identical if they share the same topic AND look alike.

    In this dataset same-topic circles share an identical auto-generated
    description, so description text can't separate a true duplicate (the three
    Negotiation circles) from two legitimately distinct same-topic circles.
    We instead key on topic + name-token / tag overlap, which collapses the
    Negotiation trio while keeping, e.g., "The Elevation Circle" and
    "Women Who Lead" (same topic, unrelated names) as distinct options.
    """
    if not cfg.near_duplicate_name_overlap:
        return False
    if not a.topic_slug or a.topic_slug != b.topic_slug:
        return False
    if _name_tokens(a.name) & _name_tokens(b.name):
        return True
    # Strong tag overlap is a secondary signal of "same thing".
    ta, tb = set(a.tags), set(b.tags)
    if ta and tb:
        jaccard = len(ta & tb) / len(ta | tb)
        if jaccard >= 0.6:
            return True
    return False


def collapse_near_duplicates(
    scored: list[Recommendation], cfg: RecommenderConfig
) -> list[Recommendation]:
    """Fold near-duplicate circles into the highest-scoring representative.

    The survivors carry the collapsed ids in ``similar_circle_ids`` so the
    product can say "most active of 3 similar Negotiation circles" and link the
    alternatives. Input is assumed sorted by score desc.
    """
    kept: list[Recommendation] = []
    for rec in scored:
        dup_of = None
        for k in kept:
            if are_near_duplicates(rec.circle, k.circle, cfg):
                dup_of = k
                break
        if dup_of is None:
            kept.append(rec)
        else:
            dup_of.similar_circle_ids.append(rec.circle.id)
    return kept


# ---------------------------------------------------------------------------
# Diversification
# ---------------------------------------------------------------------------
def diversify(
    recs: list[Recommendation], limit: int, cfg: RecommenderConfig
) -> list[Recommendation]:
    """Greedy selection capping how many circles share a topic.

    Keeps the list from being swamped by one topic (the member sees a spread),
    while still honouring score order. Circles with no topic are never capped
    against each other.
    """
    selected: list[Recommendation] = []
    per_topic: dict[str, int] = {}
    deferred: list[Recommendation] = []

    for rec in recs:  # already score-sorted
        topic = rec.circle.topic_slug
        if topic is None or per_topic.get(topic, 0) < cfg.max_per_topic:
            selected.append(rec)
            if topic is not None:
                per_topic[topic] = per_topic.get(topic, 0) + 1
        else:
            deferred.append(rec)
        if len(selected) >= limit:
            return selected

    # If capping left us short of `limit`, backfill with the best deferred ones
    # rather than return a thin list.
    for rec in deferred:
        if len(selected) >= limit:
            break
        selected.append(rec)
    return selected


# ---------------------------------------------------------------------------
# Explanations
# ---------------------------------------------------------------------------
def _health_phrase(circle: Circle, hb: dict) -> Reason | None:
    posts = circle.feed_posts_30d
    days = hb.get("days_since_activity")
    if hb["norm_posts"] >= 0.5 and (days is not None and days <= 7):
        return Reason("very_active", f"Very active — {posts} posts in the last 30 days")
    if posts > 0 and (days is not None and days <= 30):
        return Reason("active", f"Active recently — {posts} posts in the last 30 days")
    if days is not None and days <= 14:
        return Reason("recent", "Active this week")
    return None


def build_explanation(
    rec: Recommendation,
    user: User,
    labels: dict[str, str],
    matched_topic: str | None,
    match_kind: str,
    hb: dict,
    seats_left: int | None,
    cfg: RecommenderConfig,
) -> None:
    """Attach structured reasons + a prose ``why`` to a recommendation."""
    reasons: list[Reason] = []
    circle = rec.circle
    is_backfill = rec.strategy == "interest_based" and match_kind != "direct"

    # 1) Relevance (why this circle, for this member)
    if match_kind == "direct" and matched_topic:
        reasons.append(Reason("topic_match", f"Matches your interest in {labels.get(matched_topic, matched_topic)}"))
    elif rec.strategy == "cold_start":
        reasons.append(Reason("popular", "One of the most active circles in the community right now"))
    elif is_backfill:
        # No interest overlap — surfaced on community health. Say so honestly
        # rather than inventing a topical reason from a noisy tag.
        reasons.append(Reason("popular", "A popular, active circle beyond your stated interests"))

    # 2) Activity / health
    hp = _health_phrase(circle, hb)
    if hp:
        reasons.append(hp)

    # 3) Near-duplicate note (uses the circle's OWN topic, always correct)
    if rec.similar_circle_ids and circle.topic_slug:
        n = len(rec.similar_circle_ids) + 1
        label = labels.get(circle.topic_slug, circle.topic_slug)
        reasons.append(Reason("top_of_similar", f"Our top match among {n} similar {label} circles"))

    # 4) Availability nudges (informational, not scored against #22-style picks)
    if seats_left is not None and seats_left <= 3:
        reasons.append(Reason("spots_left", f"Only {seats_left} spot{'s' if seats_left != 1 else ''} left"))
    if circle.join_policy == "request":
        reasons.append(Reason("request_to_join", "Request to join — leaders approve new members"))

    # 5) Cold-start welcome / format colour
    if rec.strategy == "cold_start" and circle.join_policy == "open":
        reasons.append(Reason("open_to_join", "Open to join — a welcoming first circle"))

    rec.reasons = reasons
    # Prose: stitch the first few clauses into one short, warm sentence.
    fragments = [r.detail for r in reasons][:3]
    rec.why = ". ".join(fragments) + ("." if fragments else "")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def recommend(
    user: User,
    circles: list[Circle],
    now,
    labels: dict[str, str],
    limit: int | None = None,
    cfg: RecommenderConfig = DEFAULT_CONFIG,
) -> tuple[list[Recommendation], str]:
    """Rank circles for a user. Returns (recommendations, strategy)."""
    limit = min(limit or cfg.default_limit, cfg.max_limit)
    cold_start = len(user.topic_interests) == 0
    strategy = "cold_start" if cold_start else "interest_based"

    scored: list[Recommendation] = []
    for circle in circles:
        # --- hard filters (availability / discoverability / membership) ----
        if circle.id in user.member_circle_ids:
            continue
        if cfg.exclude_unlisted and not circle.is_public:
            continue
        if cfg.exclude_full and circle.is_full:
            continue

        health, hb = health_score(circle, now, cfg)
        cap, seats_left = capacity_score(circle)

        if cold_start:
            # No interest signal: rank by community health, nudge toward
            # low-friction open circles so the first join is easy.
            score = health + (cfg.cold_start_open_bonus if circle.join_policy == "open" else 0.0)
            matched_topic, match_kind, tscore = None, "none", 0.0
        else:
            tscore, matched_topic, match_kind = topic_score(user, circle, cfg)
            # Only surface circles that are actually relevant OR strong enough
            # on health to be a sensible "you might also like". A zero-topic
            # circle can still appear as backfill, but relevance leads.
            score = cfg.w_topic * tscore + cfg.w_health * health + cfg.w_capacity * cap

        rec = Recommendation(
            circle=circle,
            score=round(score, 4),
            strategy=strategy,
            signals={
                "topic_score": round(tscore, 3),
                "match_kind": match_kind,
                "matched_topic": matched_topic,
                "health": round(health, 3),
                "health_breakdown": hb,
                "capacity": round(cap, 3),
                "seats_left": seats_left,
                "member_count": circle.member_count,
                "max_members": circle.max_members,
                "feed_posts_30d": circle.feed_posts_30d,
                "chat_messages_30d": circle.chat_messages_30d,
                "join_policy": circle.join_policy,
                "format": circle.format,
            },
        )
        # Stash for the explanation pass without recomputing.
        rec.signals["_matched_topic"] = matched_topic
        rec.signals["_match_kind"] = match_kind
        rec.signals["_seats_left"] = seats_left
        scored.append(rec)

    # For interest-based lists, drop pure-noise candidates (no topic relevance
    # AND weak health) so we don't pad a relevant list with dead off-topic
    # circles. Cold start keeps everything (health-ranked).
    if not cold_start:
        relevant = [r for r in scored if r.signals["topic_score"] > 0]
        backfill = [r for r in scored if r.signals["topic_score"] == 0 and r.signals["health"] >= 0.45]
        scored = relevant + backfill

    scored.sort(key=lambda r: r.score, reverse=True)
    scored = collapse_near_duplicates(scored, cfg)
    selected = diversify(scored, limit, cfg)

    for rec in selected:
        build_explanation(
            rec,
            user,
            labels,
            rec.signals.pop("_matched_topic", None),
            rec.signals.pop("_match_kind", "none"),
            rec.signals["health_breakdown"],
            rec.signals.pop("_seats_left", None),
            cfg,
        )
    return selected, strategy
