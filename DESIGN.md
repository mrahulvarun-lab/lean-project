# Technical Design — Circle Recommendations

> **TL;DR** — A FastAPI + SQLite service that ranks joinable Circles for a
> member and explains each pick in plain language. Relevance leads (topic
> match), health filters out dead Circles, availability is a hard constraint,
> and a de-duplication + diversification pass keeps the list honest and varied.
> All four planted scenarios are covered by tests.

---

## 1. What I built

A read API with one endpoint that matters —
`GET /users/{id}/recommendations` — backed by a normalized SQLite projection of
the seed data and a pure-Python ranking pipeline. Every result carries a
structured `reasons[]` list and a one-sentence `why`, so the *explanation* is a
first-class output, not an afterthought. A CLI (`python -m app.cli --scenarios`)
and a `?debug=true` flag expose the full score breakdown for grading.

## 2. Signals I used, and why

The dataset offers many signals; I spent my relevance budget on the ones that
are **reliable** in this data and **explainable** to a member.

| Signal | Weight | Why |
| --- | --- | --- |
| **Topic match** (`circle.topic ∈ member.interests`) | 0.60 | The single clearest reason a woman joins a Circle. Leads the score because relevance should dominate. |
| **Health / activity** | 0.30 | Combines `feed_posts_30d`, `chat_messages_30d`, recency of `last_activity_at`, and real engagement (reactions+comments+saves) computed from the `activity` feed. A great-sounding Circle nobody posts in is a bad recommendation. |
| **Capacity headroom** | 0.10 | Mild tie-breaker: don't push someone toward a Circle one seat from full over an equally good roomy one. |

**Availability is a hard filter, not a score.** A Circle you *can't join* should
never be *recommended to join*, so I exclude **full** Circles (`member_count ≥
max_members`) and **unlisted** Circles (`access != public`, i.e. not meant to be
discovered) at candidate generation. Activity, by contrast, is a *soft* signal —
a merely-quiet Circle is demoted, not hidden. This two-mechanism split is
exactly what the "full / inactive" scenario asks for.

**Signals I deliberately did _not_ use:**

- **Tags.** In `generate.mjs` a Circle's tags are a *random sample* of topic
  slugs, so matching on them injects noise and produces misleading copy
  ("Team Leads Circle — related to Public speaking"). I use `circle.topic` as
  the relevance key and keep tags as display metadata only. With a curated tag
  vocabulary in production, tags become a legitimate secondary signal.
- **`join_policy=request` as a penalty.** The healthy answer for the
  full/inactive scenario (#22 Level Up Collective) is itself request-to-join, so
  penalizing friction would hurt a correct pick. I surface it as an
  informational reason ("Request to join — leaders approve new members") instead.
- **Member profile → Circle matching** (industry/location affinity). The cold
  user has no profile and Circles carry no industry/location column, so it's low
  ROI here. See §7.

**One correctness detail worth calling out:** the fixture is anchored to a fixed
date (latest activity ≈ 2026-09-21), so measuring recency against the real wall
clock would make *every* Circle look stale. I define "now" as the **latest
timestamp present in the data** (`repository.reference_now`), which keeps
recency meaningful and output stable whenever it's run.

## 3. How the four planted scenarios resolve

| Scenario | Member | Outcome |
| --- | --- | --- |
| **Cold start** | 42 | No interests → `cold_start` strategy: ranked by community **health**, nudged toward **open** Circles, diversified by topic. Returns a full, welcoming starter list. |
| **Obvious matches** | 7 | `leadership-skills` + `public-speaking` → **#5 Women Who Lead** and **#6 Speak Up Circle** both surface at the top (alongside #1, an equally strong leadership Circle). |
| **Duplicate Circles** | 15 | The three near-identical Negotiation Circles (#10/#11/#12) **collapse into one** entry; the other two ride along as `similar_circles`. Her second interest (Confidence) is still represented. |
| **Full / inactive** | 23 | **#20 (full)** and **#21 (inactive + unlisted)** are both filtered out; **#22 Level Up Collective** is the #1 result. |

These are encoded as acceptance tests in `tests/test_scenarios.py`, plus a
parametrized invariant check over all 100 users.

## 4. De-duplication & diversification

Two passes turn a raw score ranking into a list a member would trust:

1. **Near-duplicate collapse.** Same-topic Circles whose *names* share a salient
   token (or whose tag sets strongly overlap) fold into their highest-scoring
   representative. Because the fixture gives every same-topic Circle an identical
   auto-generated description, I key on name/tag similarity rather than
   description text — which correctly separates a true duplicate ("Negotiation
   Circle" / "The Negotiation Table") from two distinct Circles that merely share
   a topic ("The Elevation Circle" / "Women Who Lead").
2. **Topic diversification.** A greedy selection caps Circles-per-topic
   (default 2) so one interest can't swamp the list, backfilling only if capping
   would otherwise return a thin list.

## 5. Tradeoffs for the time budget

- **Linear weighted score, not a learned model.** Transparent, debuggable, and
  defensible — and there's no interaction/outcome data to train on yet.
- **Weights hand-tuned against the data**, all in `config.py`. They're
  interpretable, not fit — I inspected the planted Circles and chose values that
  make relevance dominate while letting health break ties. Easy to A/B later.
- **SQLite + raw SQL**, no ORM. The schema deliverable stays crisp and the
  queries that matter are visible in `repository.py`.
- **Batch scoring in Python.** At 40 Circles this is instantly fast and far
  clearer than SQL ranking; §7 covers what changes at scale.

---

## 6. Database schema

Full DDL in [`app/schema.sql`](app/schema.sql). Written for SQLite; maps
directly onto Postgres (swap autoincrement, `TIMESTAMPTZ`, native enums).

**Tables & relationships**

- `topics` — the taxonomy; `slug` is the join key used everywhere.
- `users` — member profile fields + precomputed `profile_completeness`.
- `user_topic_interests` *(user_id, topic_slug)* — normalized M:N of stated
  interests. Modelled as its own table (not a JSON column) so "who likes topic
  X?" is an indexed lookup — the basis of collaborative filtering later.
- `circles` — profile, policy (`format`/`access`/`join_policy`/`max_members`),
  and the **denormalized activity aggregates** (`member_count`,
  `feed_posts_30d`, `chat_messages_30d`, `last_activity_at`). Kept on-row for
  O(1) ranking reads; rebuildable from `activity` + `memberships`.
- `circle_tags` *(circle_id, tag)* — normalized tags.
- `memberships` *(user_id, circle_id, role)* — M:N; a leader is a member with
  `role='leader'`. Composite PK dedupes a person↔Circle pair.
- `activity` — the raw feed, kept so engagement is computed from first-party
  data and the aggregates above can be recomputed.

**Indexing decisions** (each supports a specific hot-path query):

| Index | Serves |
| --- | --- |
| `circles(topic_slug)` | candidate generation by topic |
| `circles(access)` | the "public only" discoverability filter |
| `memberships(user_id)` | "Circles this member already belongs to" — every request |
| `memberships(circle_id)` | a Circle's members/leaders; `member_count` rebuild |
| `user_topic_interests(topic_slug)` | reverse "members who like topic X" (CF-ready) |
| `circle_tags(tag)` | tag lookups |
| `activity(circle_id, created_at)` | 30-day engagement/recency windows |

## 7. What I'd add for production scale

- **Precompute & cache.** Nightly (or streaming) materialization of per-Circle
  health scores and a feature store; cache per-member candidate sets. Serve
  from cache, re-rank on the fly.
- **Candidate generation at scale.** With millions of Circles, replace the
  full scan with an indexed retrieval step (topic/tag inverted index, plus
  **embedding** similarity on name/description/bio) that returns a few hundred
  candidates before scoring.
- **Collaborative filtering.** "Members like you also joined…" from the
  `memberships` graph — strong signal for cold-start users who have *some*
  profile but no interests, and for discovery beyond stated topics.
- **Richer cold start.** Use industry/location/job-title affinity (derive a
  Circle's member mix via `memberships ⋈ users`) and onboarding prompts to
  collect interests early.
- **A feedback loop.** Log impressions, joins, and *stays* (did she post after
  joining?) and learn weights from outcomes — moving from a hand-tuned score to
  a ranked model, measured by join-through and retention, not clicks.
- **Fairness / mission guardrails.** Avoid rich-get-richer dynamics where only
  the biggest Circles ever get recommended; intentionally surface healthy small
  and newly-formed Circles so the long tail of leaders gets members, too.
- **Hardening.** Pagination/`seen` exclusion, auth, rate limits, request
  tracing, and moving aggregate maintenance to triggers/jobs so writes keep the
  denormalized columns correct.

## 8. Working with AI

I used an AI assistant to scaffold the project, draft boilerplate (schema, the
FastAPI/Pydantic layer, test harness), and rubber-duck the diversification
design. I owned the decisions that matter and verified them against the data:
reading `generate.mjs` to understand how the fixture is built (which is how I
caught the random-tags and fixed-anchor issues), inspecting the planted Circles
to set the weights, and writing the scenario tests that pin the required
behaviour. Every ranking choice in §2 is one I can explain and defend.
