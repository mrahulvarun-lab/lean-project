"""Tunable configuration for the recommendation engine.

Every knob the ranking depends on lives here (not scattered through the code)
so the behaviour is easy to reason about, defend in review, and A/B test later.
The defaults were chosen by inspecting the seed data against the four planted
scenarios — see DESIGN.md for the rationale behind each weight.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
SCHEMA_PATH = APP_DIR / "schema.sql"

# DB location is overridable so tests can use a throwaway file / :memory:.
DB_PATH = os.environ.get("CIRCLES_DB_PATH", str(PROJECT_ROOT / "circles.db"))


@dataclass(frozen=True)
class RecommenderConfig:
    """Weights and thresholds for scoring and diversification.

    Scores are kept in [0, 1] per component and combined with weights that sum
    to 1.0, so a final score is interpretable as a 0..1 "fit" number.
    """

    # --- blend weights (interest-based ranking) ---------------------------
    # Relevance dominates: a member joins a Circle because of its topic first.
    w_topic: float = 0.60
    # Activity/health is the quality gate that separates lively from dead.
    w_health: float = 0.30
    # Capacity headroom is a mild tie-breaker (don't push someone toward a
    # Circle that's one seat from full over an equally-good roomy one).
    w_capacity: float = 0.10

    # --- topic relevance ---------------------------------------------------
    # circle.topic ∈ member interests. Tags are intentionally NOT used for
    # relevance (random in the fixture — see DESIGN.md / recommender.topic_score).
    topic_direct_match: float = 1.0

    # --- health sub-weights (sum to 1.0) ----------------------------------
    h_posts: float = 0.35             # feed_posts_30d — is anyone posting?
    h_chat: float = 0.25              # chat_messages_30d — backchannel liveliness
    h_recency: float = 0.25           # how long since the last activity
    h_engagement: float = 0.15        # reactions/comments/saves per recent post

    # Normalization caps: the value at which a signal is considered "maxed".
    posts_cap: float = 12.0           # ~top of the active band in the data
    chat_cap: float = 150.0
    engagement_cap: float = 15.0      # avg (reactions+comments+saves) per post
    recency_window_days: float = 30.0 # linear decay to 0 over this window

    # --- availability (hard filters) --------------------------------------
    # A Circle you cannot join should never be *recommended to join*.
    exclude_full: bool = True         # member_count >= max_members (capped)
    exclude_unlisted: bool = True     # access != 'public' — not discoverable

    # --- diversification ---------------------------------------------------
    max_per_topic: int = 2            # at most N Circles sharing one topic in a list
    near_duplicate_name_overlap: bool = True  # collapse same-topic look-alikes

    # --- output ------------------------------------------------------------
    default_limit: int = 5
    max_limit: int = 25

    # --- cold start --------------------------------------------------------
    # With no interests we fall back to community health; nudge toward
    # low-friction open Circles so a newcomer's first join is easy.
    cold_start_open_bonus: float = 0.05


DEFAULT_CONFIG = RecommenderConfig()
