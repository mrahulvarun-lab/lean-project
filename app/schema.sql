-- ---------------------------------------------------------------------------
-- Lean In Connect — Circle recommendations: relational schema (SQLite)
--
-- Design goals
--   * Normalize the many-to-many signals (topic interests, tags, memberships)
--     so the recommender can answer "who/what matches this topic?" with an
--     indexed join instead of scanning JSON blobs.
--   * Keep the per-Circle activity aggregates the product already materializes
--     (member_count, feed_posts_30d, chat_messages_30d, last_activity_at) on
--     the circles row for O(1) reads, while still storing the raw activity feed
--     so the engine can compute engagement itself and the aggregates can be
--     rebuilt.
--   * Every foreign-key lookup the recommender performs on the hot path has a
--     supporting index (see the rationale next to each one).
--
-- Portability note: written for SQLite (zero-setup, ships in the stdlib). The
-- same model maps directly onto Postgres — swap INTEGER PRIMARY KEY for
-- BIGSERIAL/IDENTITY, TEXT timestamps for TIMESTAMPTZ, and the CHECK enums for
-- native ENUM types or domains.
-- ---------------------------------------------------------------------------

PRAGMA foreign_keys = ON;

-- Topic taxonomy -------------------------------------------------------------
CREATE TABLE topics (
    id     INTEGER PRIMARY KEY,
    slug   TEXT NOT NULL UNIQUE,          -- kebab-case, the join key everywhere
    label  TEXT NOT NULL,                 -- display name for member-facing copy
    domain TEXT NOT NULL DEFAULT 'content'
);

-- Members --------------------------------------------------------------------
CREATE TABLE users (
    id                   INTEGER PRIMARY KEY,
    first_name           TEXT,
    last_name            TEXT,
    has_photo            INTEGER NOT NULL DEFAULT 0,   -- 0/1 boolean
    bio                  TEXT,
    job_title            TEXT,
    company              TEXT,
    industry             TEXT,
    location             TEXT,
    joined_at            TEXT NOT NULL,                -- ISO-8601 UTC
    profile_completeness REAL NOT NULL DEFAULT 0       -- 0..1, precomputed
);

-- A member's stated topic interests (normalized many-to-many).
-- topic_slug is stored as the slug (not topics.id) because the seed data and
-- the API speak in slugs; FK still enforces referential integrity.
CREATE TABLE user_topic_interests (
    user_id    INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    topic_slug TEXT    NOT NULL REFERENCES topics(slug) ON DELETE CASCADE,
    PRIMARY KEY (user_id, topic_slug)
);

-- Circles --------------------------------------------------------------------
CREATE TABLE circles (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    slug              TEXT NOT NULL UNIQUE,
    description       TEXT,                              -- nullable
    topic_slug        TEXT REFERENCES topics(slug),      -- nullable: some Circles have no topic
    cover_image_url   TEXT,
    format            TEXT NOT NULL CHECK (format      IN ('in_person','virtual','hybrid')),
    access            TEXT NOT NULL CHECK (access      IN ('public','unlisted')),
    join_policy       TEXT NOT NULL CHECK (join_policy IN ('open','request')),
    max_members       INTEGER,                           -- null = uncapped
    -- Denormalized activity aggregates (maintained by the product; rebuildable
    -- from `activity` + `memberships`). Kept on-row for O(1) ranking reads.
    member_count      INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    last_activity_at  TEXT,
    feed_posts_30d    INTEGER NOT NULL DEFAULT 0,
    chat_messages_30d INTEGER NOT NULL DEFAULT 0
);

-- Free-form topic-ish tags on a Circle (normalized).
CREATE TABLE circle_tags (
    circle_id INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    tag       TEXT    NOT NULL,
    PRIMARY KEY (circle_id, tag)
);

-- Who belongs to which Circle. A leader is a member with role='leader'.
CREATE TABLE memberships (
    user_id   INTEGER NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    circle_id INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    role      TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member','leader')),
    joined_at TEXT NOT NULL,
    PRIMARY KEY (user_id, circle_id)
);

-- Recent Circle feed posts with engagement counts.
CREATE TABLE activity (
    id             INTEGER PRIMARY KEY,
    circle_id      INTEGER NOT NULL REFERENCES circles(id) ON DELETE CASCADE,
    author_user_id INTEGER NOT NULL REFERENCES users(id),
    body           TEXT NOT NULL,
    reaction_count INTEGER NOT NULL DEFAULT 0,
    comment_count  INTEGER NOT NULL DEFAULT 0,
    save_count     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

-- Indexes --------------------------------------------------------------------
-- Candidate generation by topic: "Circles for topics this member likes."
CREATE INDEX idx_circles_topic        ON circles(topic_slug);
-- Discoverability filter: we only recommend publicly listed Circles.
CREATE INDEX idx_circles_access       ON circles(access);
-- Reverse lookup "which members like topic X" (collaborative-filtering ready).
CREATE INDEX idx_uti_topic            ON user_topic_interests(topic_slug);
-- Tag-based relevance + reverse "which Circles carry tag X".
CREATE INDEX idx_circle_tags_tag      ON circle_tags(tag);
-- "Circles this member already belongs to" (dedupe) — hot path, every request.
CREATE INDEX idx_memberships_user     ON memberships(user_id);
-- "Members/leaders of this Circle" (leader profiles, member_count rebuild).
CREATE INDEX idx_memberships_circle   ON memberships(circle_id);
-- Engagement/recency windows computed from the raw feed.
CREATE INDEX idx_activity_circle_time ON activity(circle_id, created_at);
