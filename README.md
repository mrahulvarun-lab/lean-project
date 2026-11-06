# Lean In Connect — Circle Recommendations API

A small, runnable service that recommends **Circles a member should join**, each
with a short, honest, member-facing explanation of *why*.

Built for the Lean In take-home. Stack: **Python 3.10+ · FastAPI · SQLite**
(zero external services). The recommendation logic is plain, well-tested Python
you can read top-to-bottom in `app/recommender.py`.

---

## Quick start

```bash
# 1. (recommended) a virtual environment
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate

# 2. install
pip install -r requirements.txt

# 3. build the SQLite database from the seed JSON (data/*.json)
python -m app.seed

# 4. run the API
uvicorn app.main:app --reload
```

Then open <http://localhost:8000/docs> for interactive Swagger docs, or:

```bash
curl 'http://localhost:8000/users/7/recommendations?limit=5'
```

> The API also **self-seeds on first start**, so if you skip step 3 the first
> `uvicorn` run will build `circles.db` for you.

### See the four planted scenarios at a glance

```bash
python -m app.cli --scenarios          # cold-start 42, obvious 7, dupes 15, full/inactive 23
python -m app.cli 7 --debug            # one user, with the full per-signal breakdown
```

### Run the tests

```bash
pytest -q
```

125 tests: the four planted scenarios, scoring-component units, HTTP contract,
and a global invariant check across **all 100 users** (never recommend a full,
unlisted, or already-joined Circle; respect the per-topic cap; always explain).

---

## API

| Method & path | Purpose |
| --- | --- |
| `GET /health` | Liveness probe |
| `GET /users/{user_id}` | Member summary (interests, completeness, joined circles) |
| `GET /users/{user_id}/recommendations?limit=5&debug=false` | Ranked Circles + explanations |

Example response (trimmed):

```jsonc
{
  "user_id": 7,
  "strategy": "interest_based",
  "count": 5,
  "recommendations": [
    {
      "circle_id": 5,
      "name": "Women Who Lead",
      "topic": "leadership-skills",
      "format": "hybrid",
      "join_policy": "open",
      "member_count": 24,
      "max_members": 31,
      "score": 0.91,
      "why": "Matches your interest in Leadership skills. Very active — 11 posts in the last 30 days.",
      "reasons": [
        { "code": "topic_match", "detail": "Matches your interest in Leadership skills" },
        { "code": "very_active", "detail": "Very active — 11 posts in the last 30 days" }
      ],
      "similar_circles": []
    }
  ]
}
```

- `strategy` is `interest_based` or `cold_start`.
- `reasons[]` is the structured form of `why` — a UI can render each as a chip.
- `similar_circles[]` lists near-duplicate Circles that were folded into this
  one (so three near-identical Negotiation Circles return as a single entry).
- `?debug=true` adds a `signals` object with the full per-signal score
  breakdown for every result.

---

## How it decides (the short version)

For a member with stated interests, every candidate Circle is scored
`0.60 × topic + 0.30 × health + 0.10 × capacity`, then the list is
de-duplicated and diversified:

1. **Filter** out Circles the member already belongs to, **unlisted** Circles
   (not publicly discoverable), and **full** Circles (you can't join them).
2. **Topic** relevance — does the Circle's topic match a stated interest?
3. **Health** — `feed_posts_30d`, `chat_messages_30d`, how recently it was
   active, and real engagement from the activity feed. This is what sinks an
   *inactive* Circle.
4. **Capacity** — a mild tie-breaker toward Circles with room.
5. **Collapse near-duplicates** — near-identical same-topic Circles fold into
   their strongest representative.
6. **Diversify** — cap how many Circles per topic appear so one topic can't
   swamp the list.

A member with **no interests** (cold start) is ranked purely on community
**health**, nudged toward low-friction *open* Circles, and diversified — i.e.
"here are the most active, welcoming Circles to start with."

Full rationale, the four scenarios, tradeoffs, and what I'd add for production
are in **[DESIGN.md](DESIGN.md)**.

---

## Project layout

```
app/
  schema.sql        SQLite DDL — tables, FKs, indexes (the schema deliverable)
  db.py             connection + schema bootstrap
  seed.py           load data/*.json -> SQLite   (python -m app.seed)
  repository.py     SQL -> domain objects (User, Circle); "reference now" handling
  recommender.py    scoring, near-dup collapse, diversification, explanations
  service.py        orchestration (loads reference data once)
  models.py         Pydantic response contract
  main.py           FastAPI app
  cli.py            terminal view of recommendations   (python -m app.cli)
  config.py         every tunable weight/threshold, in one place
data/               the provided synthetic seed dataset (unchanged) + generator
tests/              scenarios, unit tests, HTTP tests
```

## Notes

- **Python 3.10+** (uses `X | Y` type syntax). Developed on 3.13.
- The database is a disposable projection of `data/*.json`; `python -m app.seed`
  rebuilds it from scratch every time, so it's always reproducible.
- No API keys, no external network calls, no Docker required.
