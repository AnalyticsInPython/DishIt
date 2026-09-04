# DishIt

Which dishes do people actually talk about at a restaurant — and is the chatter good, bad,
or split? DishIt reads real Google reviews against real scraped menus, matches what
reviewers mention to what the kitchen serves, and scores each dish.

**Live: https://dishit.fly.dev**

Scope is Morningside Heights, Manhattan. The current corpus:

| | |
|---|---|
| restaurants | 82 |
| dishes (menu items) | 3,129 |
| reviews | 2,524 |
| dish mentions found in reviews | 421 |

A dish needs **5 mentions** before DishIt will score it, so most dishes are listed without a
score rather than ranked on one or two opinions. That threshold is why the site shows far
fewer scored dishes than it holds menu items.

## How it fits together

```
data/collect/     Google Places + review scraping, menus read off photos
      ↓           writes restaurants_full.json
data/db/          load_db.py builds the canonical SQLite database
      ↓           data/db/dishit.db  (gitignored — built per-laptop)
data/calculate/   calculate.py finds dish mentions in review text and
      ↓           scores each one positive / negative / mixed, in place
data/db/          push_to_turso.sh publishes the file to Turso (libSQL)
      ↓
backend/          FastAPI reads an embedded replica of that database
      ↓
frontend/         plain JS, served by the same process
```

Two things follow from this shape and are worth knowing before you touch anything:

- **`dishit.db` is gitignored.** It is built, not committed. A fresh clone has no database
  until you either build one or point the backend at the hosted copy — see
  [Running it](#running-it).
- **The backend never writes.** Every table and the `dish_sentiment_summary` view come from
  the pipeline. The API is a pure reader, so fixing bad data means re-running the pipeline
  and re-publishing, not patching rows.

## Repo layout

| path | what it is |
|---|---|
| `backend/app/main.py` | routes, response shaping, product rules (thresholds, sentiment bands) |
| `backend/app/db.py` | the three connection modes |
| `backend/app/search.py` | intent routing — scores a query against restaurant name / dish name / cuisine |
| `backend/app/geo.py` | haversine distance, walk time |
| `frontend/` | plain HTML/CSS/JS. No build step, no bundler, no framework |
| `data/collect/` | collection scripts and their JSON output |
| `data/db/` | `schema.sql`, `load_db.py`, `push_to_turso.sh` |
| `data/calculate/` | dish-mention matching and sentiment scoring |
| `specs/api-contract.md` | the API contract the frontend is built against |
| `Dockerfile`, `fly.toml` | deployment |

## Running it

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Then pick where the data comes from.

**Against the hosted database** — nothing to build, but you need the Turso credentials:

```bash
export DISHIT_DB_MODE=remote
export TURSO_DATABASE_URL="libsql://..."   # turso db show dishit --url
export TURSO_AUTH_TOKEN="..."              # turso db tokens create dishit
uv run uvicorn app.main:app --app-dir backend --port 8000
```

**Against a local file** — the default, and what the tests use. Build the database first
(this takes a while; [data/README.md](data/README.md) has the detail):

```bash
python3 data/db/load_db.py --rebuild
data/calculate/.venv/bin/python data/calculate/calculate.py data/db/dishit.db
uv run uvicorn app.main:app --app-dir backend --port 8000
```

Either way the whole app — API and page — is at http://127.0.0.1:8000. The frontend is
served by the same process, so there is no second server to start and no CORS to configure.

If the database is missing, startup fails with a message naming the path it looked for
rather than serving an empty site.

## Tests and lint

```bash
uv run pytest        # backend/tests, against a fixture database — no real data needed
uv run ruff check    # backend/ only
```

## The API

Read-only JSON over HTTP, `GET` only. OpenAPI is generated from the routes and served at
`/openapi.json`, with Swagger UI at `/docs`.

| endpoint | returns |
|---|---|
| `/api/health` | liveness, for the platform health check |
| `/api/popular` | three lists: most talked about, controversial, top rated |
| `/api/search?q=` | routed results, plus both complete match lists |
| `/api/restaurants` | every restaurant, nearest first, each with its top dish |
| `/api/restaurants/{id}` | one restaurant and its dishes |
| `/api/dishes/{id}` | one dish, its quotes, and the same dish elsewhere |

Response shapes follow **screens, not entities** — `/api/popular` returns the home page's
three tabs, `/api/search` returns what the results page needs to render its toggle. That is
deliberate: one client, one contract, written from the UI's needs. See
[specs/api-contract.md](specs/api-contract.md) for the full shapes and the rules behind
`score`, `label`, and `is_controversial`.

Product rules live in `main.py` as constants, not in the database, which stores raw counts.
Changing the mention threshold or the sentiment bands is a deploy, not a data reload.

## Deployment

One Fly machine in `ewr`, `shared-cpu-1x:256MB`, running `DISHIT_DB_MODE=replica`. The image
carries no database: the machine materialises an embedded libSQL replica from Turso onto a
mounted volume at boot. That volume is a cache, not state — losing it costs one resync.

```bash
fly deploy --ha=false
```

`--ha=false` matters. Fly defaults to two machines, and a volume binds to exactly one, so
the second would fail to start.

The machine sleeps when idle (`auto_stop_machines`) and boots on the next request, so
`fly status` showing `stopped` is normal, not an outage. The first request after a quiet
period pays a cold start.

To publish new data:

```bash
./data/db/push_to_turso.sh          # after load_db.py and calculate.py
```

The replica polls hourly, so a push reaches the site within the hour — or immediately with
`fly apps restart dishit`.

## Further reading

- [data/README.md](data/README.md) — the pipeline in full: building, rebuilding, the schema,
  querying, and publishing to Turso
- [specs/api-contract.md](specs/api-contract.md) — API contract
- [specs/backend-data-questions.md](specs/backend-data-questions.md) — open schema questions
  raised when the real dataset met the original fictional one
- [dish-sentiment-proposal.md](dish-sentiment-proposal.md) — the original product proposal
