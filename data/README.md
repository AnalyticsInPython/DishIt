# DishIt data pipeline

Two stages: `collect/` gathers restaurants, menus and reviews from the web into JSON;
`db/` loads that JSON into SQLite. A third, optional calculation step extracts
dish-level sentiment from review text into that same SQLite database.

The collected JSON is committed, so **you do not need API keys to build the database.**

## One database, and only one

`data/db/dishit.db` is the only database this pipeline builds. Everything reads and
writes that one file: `load_db.py` creates it, `calculate.py` adds `dish_mentions` to it,
and the backend serves from it (`backend/app/db.py`, overridable with `DISHIT_DATABASE`).

Three things will quietly give you a *second* database, so avoid them:

- **Running `dish_sentiment_calculator.py` as a command.** It is the model engine that
  `calculate.py` imports, and its own CLI is a leftover that works on an older schema:
  `review` writes four side files (including a `_by_dish.sqlite3` whose
  `dish_sentiment_summary` is a *table* with different columns from the view here), and
  `analyze` / `analyze-all` create an empty database if the path is wrong. Always go
  through `calculate.py`.
- **A mistyped path.** Both `sqlite3 <path>` and `--db <path>` create an empty database
  rather than erroring when the file doesn't exist.
- **`archive/`.** Those scripts predate this pipeline and use the old
  `restaurants / sources / dishes / mentions` schema; their sample databases live in
  `archive/fixtures/` and are not pipeline output.

### Order matters: load first, then calculate

`load_db.py` replaces each restaurant's dishes wholesale on every run, and
`dish_mentions.dish_id` cascades on delete — so **re-running `load_db.py` erases every
dish mention.** Run the loader first and the calculation second, and whenever you reload
new data, plan to re-run the calculation after it.

## Build the database

No dependencies, no virtualenv — the loader is Python standard library only:

```bash
python3 data/db/load_db.py
```

Creates `data/db/dishit.db` (gitignored) from `data/collect/output/restaurants_full.json`.

As collected (3 September 2026):

| | JSON | database (default load) |
|---|---|---|
| restaurants | 106 | **82** |
| with menu items | 82 | 82 |
| without menu items | 24 | 0 |
| menu items | 3,129 | 3,129 |
| reviews | 2,944 | 2,524 |
| review photos | 4,736 | 4,438 |

The database is smaller because the loader takes only restaurants that have menus, so
every row has dishes to analyse. The 24 without a menu — and their 420 reviews — are left
out unless you pass `--include-menuless`.

Those counts move whenever anyone collects more, so check the live ones rather than
trusting this table:

## Rebuilding after someone collects new data

When a teammate pushes new data, refresh your copy with:

```bash
git pull
python3 data/db/load_db.py --rebuild
```

`dishit.db` is gitignored, so it is never pulled — everyone builds their own from the
committed JSON. The rebuild takes about a second.

### Why `--rebuild` and not a plain re-run

| | plain re-run | `--rebuild` |
|---|---|---|
| adds new restaurants, dishes, reviews | yes | yes |
| updates changed rows | yes | yes |
| duplicates anything | no | no |
| **removes rows dropped from the JSON** | **no** | yes |
| result | JSON merged *onto* your database | database matches the JSON exactly |

The loader only inserts and updates. Restaurants key on Google's `place_id` and reviews on
its review id, so a plain re-run never duplicates — but it also cannot delete. A restaurant
that has since been removed from the JSON stays in your database indefinitely, and your
copy quietly disagrees with everyone else's.

This is not hypothetical: four restaurants with no reviews were dropped from the dataset.
Anyone who built a database before that and re-runs without `--rebuild` still has them.

So use `--rebuild` whenever you have pulled new data. A plain re-run is only useful when
you are adding to a database you know is already current — topping up after your own
collection run, say.

### Check your database matches

```bash
python3 -c "import json; print(len([r for r in json.load(open('data/collect/output/restaurants_full.json')) if r.get('menu_items')]), 'restaurants with menus in JSON')"
sqlite3 data/db/dishit.db "SELECT COUNT(*) || ' restaurants in database' FROM restaurants;"
```

Those two numbers should agree. If the database is larger, it is carrying stale rows —
run `--rebuild`. (They differ by design if you loaded with `--include-menuless`, which
also brings in restaurants that have reviews but no menu.)

By default the loader takes only restaurants that have menus, so every row in the
database has dishes to analyse. The source JSON also holds restaurants with reviews but
no menu — some places simply never publish one, and delis, bars and campus cafés often
have nothing findable anywhere. Pass `--include-menuless` to load those too; they arrive
with their reviews and no dishes. `--json` and `--db` override the paths.

To see the current counts:

```bash
sqlite3 data/db/dishit.db "SELECT 'restaurants', COUNT(*) FROM restaurants
  UNION ALL SELECT 'dishes', COUNT(*) FROM dishes
  UNION ALL SELECT 'reviews', COUNT(*) FROM reviews;"
```

## What's in `restaurants_full.json`

The collector's output and the only input the database needs — about 5.5 MB, committed so
nobody has to spend API credits regenerating it. A JSON array, one object per restaurant:

```json
{
  "name": "Symposium",
  "address": "544 W 113th St, New York, NY 10025",
  "place_id": "ChIJAfmpFDz2wokRGWWAhkafMwA",
  "cid": "14530349065331993",
  "distance_m": 268,
  "menu_source_url": "http://www.symposiumnyc.com/",
  "menu_items": [
    {"name": "NY Greek Salad", "price": "$18.00", "description": "Feta, lettuce, tomato..."}
  ],
  "reviews": [
    {
      "id": "Ci9DQUlRQUNvZENo...",
      "rating": 4,
      "snippet": "The grape leaves were delicious and the baklava was divine...",
      "isoDate": "2026-04-07T23:00:39.784Z",
      "date": "4 months ago",
      "likes": 0,
      "link": "https://www.google.com/maps/reviews/...",
      "user": {"name": "Heidi W.", "link": "...", "thumbnail": "...", "reviews": 112, "photos": 103},
      "media": [{"type": "image", "imageUrl": "https://...", "caption": "Gyro meat plate"}],
      "response": {"date": "4 months ago", "snippet": "Thank you for your review!"}
    }
  ],
  "raw_place": { }
}
```

| Field | What it is |
|---|---|
| `name`, `address` | as Google lists them |
| `place_id`, `cid` | Google's identifiers; `place_id` is the key everything matches on |
| `distance_m` | straight-line metres from 116th & Broadway |
| `menu_source_url` | where this menu was found — a website, a web page, or a photo URL |
| `menu_items` | list of `{name, price, description}`; **`null` for the 24 restaurants without a menu** |
| `reviews` | list of review objects, in Google's own order — roughly recent first, but not sorted; sort on `isoDate` if order matters |
| `raw_place` | the search result exactly as returned, unmodified |

`raw_place` is where the extra restaurant detail lives — `latitude`, `longitude`,
`website`, `phoneNumber`, `rating`, `ratingCount`, `type`, `types`, `openingHours`,
`priceLevel`, `description`, `thumbnailUrl`. It is kept verbatim because the loader pulls
several columns out of it, and because the collector's own field names were guesses at an
undocumented API; keeping the raw response means nothing is lost if a guess was wrong.

Two things to expect when reading it directly:

- **`menu_items` is `null`, not `[]`**, for restaurants without a menu. Check truthiness
  rather than length.
- **`reviews[].snippet` can be absent** when someone left a rating and no words.

## Querying the database

### Command line

`sqlite3` ships with macOS. Paths are relative to the repo root, so either `cd` there
first or use an absolute path — pointing `sqlite3` at a path that doesn't exist silently
creates an empty database rather than erroring, so an unexpectedly empty table usually
means a wrong path.

```bash
sqlite3 data/db/dishit.db
```

Inside the shell: `.tables` lists tables, `.schema dishes` shows one table's definition,
`.headers on` and `.mode column` make output readable, `.quit` exits.

Or run a single query without entering the shell:

```bash
sqlite3 -header -column data/db/dishit.db "SELECT name, price_raw FROM dishes LIMIT 5;"
```

### Python

Standard library, nothing to install:

```python
import sqlite3

conn = sqlite3.connect("data/db/dishit.db")
conn.row_factory = sqlite3.Row          # rows index by column name

for row in conn.execute(
    """SELECT d.name, d.price_raw
       FROM dishes d
       JOIN restaurants r ON r.id = d.restaurant_id
       WHERE r.name = ?""",
    ("Symposium",),
):
    print(row["name"], row["price_raw"])
```

Use `?` placeholders rather than formatting values into the SQL string.

### GUI

In VS Code, the **SQLite Viewer** extension opens `dishit.db` from the file tree.
Standalone: **DB Browser for SQLite** (`brew install --cask db-browser-for-sqlite`) or
**TablePlus**.

### Queries to start from

```sql
-- one restaurant's menu
SELECT d.name, d.price_raw
FROM dishes d JOIN restaurants r ON r.id = d.restaurant_id
WHERE r.name = 'Symposium';

-- what kinds of places are in range
SELECT type, COUNT(*) AS n
FROM restaurant_types GROUP BY type ORDER BY n DESC;

-- best rated, ignoring places with few ratings
SELECT name, google_rating, google_rating_count, distance_m
FROM restaurants
WHERE google_rating_count > 100
ORDER BY google_rating DESC;

-- reviews that name a dish: the raw material for dish_mentions
SELECT r.name, rv.rating, rv.text
FROM reviews rv JOIN restaurants r ON r.id = rv.restaurant_id
WHERE rv.text LIKE '%dumpling%';

-- the leaderboard view (zero counts until dish_mentions is filled)
SELECT * FROM dish_sentiment_summary ORDER BY mention_count DESC LIMIT 20;
```

That fourth query is a crude stand-in for what the extraction step will do properly —
`LIKE` catches the word but can't tell praise from complaint, which is what
`dish_mentions.sentiment` is for.

## Calculate dish sentiment

Unlike the loader, this step needs a virtualenv. torch and spaCy publish no wheels for
Python 3.14, and `pyproject.toml` asks for 3.12 anyway, so pin the interpreter:

```bash
uv venv --python 3.12 data/calculate/.venv
data/calculate/.venv/bin/python -m pip install -r data/calculate/requirements.txt
data/calculate/.venv/bin/python -m spacy download en_core_web_sm
data/calculate/.venv/bin/python data/calculate/calculate.py data/db/dishit.db
```

The venv is gitignored. Installing pulls down about 1 GB (torch and friends), and the
first run fetches two more models from HuggingFace — `urchade/gliner_base` to spot dish
names and `yangheng/deberta-v3-base-absa-v1.1` to score them, roughly 870 MB together,
cached in `~/.cache/huggingface` for later runs.

A full pass over ~2,500 reviews takes roughly 45 minutes on an Apple-silicon laptop. It
runs as a single transaction, so let it finish — an interrupted run leaves the database
untouched and starts over from the beginning.

The calculation reads non-blank `reviews.text` and each review's restaurant menu
from `dishes.name`, then writes `dish_mentions` **to the same `data/db/dishit.db`
database**. It never creates dishes: an extracted item is recorded only when it exactly
matches an existing menu dish for that restaurant, case-insensitively. If duplicate
menu names exist, the lowest dish ID is used. Re-running the command is idempotent.
The model's `neutral` result is written as `mixed`, so stored mentions align with the
current `dish_sentiment_summary` view's positive/negative/mixed aggregation.

Menu names shorter than `MIN_MENU_NAME_LENGTH` (4 characters, `calculate.py`) are skipped.
Reading a menu off a photo occasionally clips a name to a stray letter or two — Blue
Bottle's `TEA` came through as `A` — and a one-letter name matches the article in
practically every review. That single row produced 71 of 496 mentions and topped the
leaderboard before the guard existed. The cost is small: only 14 dishes in the whole
menu are that short, and legitimate ones (`Ful`, `314`) contributed 4 mentions between
them.

For a short test run, limit the number of non-blank reviews:

```bash
data/calculate/.venv/bin/python data/calculate/calculate.py data/db/dishit.db --max-reviews 5
```

## Schema

### `restaurants`
One row per place, keyed on Google's `place_id`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | primary key |
| `place_id` | TEXT | **unique**, not null — Google's stable id, what re-runs match on |
| `cid` | TEXT | Google customer id |
| `name` | TEXT | not null |
| `address` | TEXT | |
| `latitude`, `longitude` | REAL | |
| `distance_m` | INTEGER | metres from 116th & Broadway |
| `category` | TEXT | primary Google category, e.g. `Greek restaurant` |
| `website` | TEXT | |
| `phone` | TEXT | |
| `google_rating` | REAL | e.g. `4.3` |
| `google_rating_count` | INTEGER | how many ratings that average covers |
| `price_level` | TEXT | Google's band, e.g. `$20–30` |
| `menu_source_url` | TEXT | where this menu was found |
| `collected_at` | TEXT | not null — UTC ISO timestamp of the load |

### `restaurant_types`
A place carries several Google categories (Junzi is both `Chinese restaurant` and
`Noodle shop`), kept as rows so they can be filtered by index.

| Column | Type | Notes |
|---|---|---|
| `restaurant_id` | INTEGER | primary key with `type`, → `restaurants(id)` |
| `type` | TEXT | primary key with `restaurant_id` |

### `dishes`
Menu items. No unique constraint on `(restaurant_id, name)` — the same dish legitimately
appears twice on a menu, across sections or as size variants.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | primary key |
| `restaurant_id` | INTEGER | not null, → `restaurants(id)` |
| `name` | TEXT | not null |
| `description` | TEXT | |
| `price_raw` | TEXT | the menu's own wording, e.g. `"M: $2.5, L: $3.25"` |
| `price_min` | REAL | first number found in `price_raw`; nullable |

### `reviews`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | primary key |
| `restaurant_id` | INTEGER | not null, → `restaurants(id)` |
| `external_id` | TEXT | **unique**, not null — Google's review id, makes re-imports idempotent |
| `rating` | INTEGER | 1–5 |
| `text` | TEXT | review body; NULL when the reviewer left only a rating |
| `published_at` | TEXT | ISO timestamp |
| `relative_date` | TEXT | as Google shows it, e.g. `4 months ago` |
| `likes` | INTEGER | |
| `url` | TEXT | link to the review |
| `author_name` | TEXT | |
| `owner_response` | TEXT | the restaurant's reply, when there is one |

### `review_media`
Photos attached to reviews. Captions are what tier 3 of the menu lookup searches.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | primary key |
| `review_id` | INTEGER | not null, → `reviews(id)` |
| `image_url` | TEXT | not null |
| `caption` | TEXT | e.g. `Noodle soups and steamed pork dumplings` |

### `dish_mentions`
Empty after loading — this is what the dish-extraction and sentiment step writes.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | primary key |
| `dish_id` | INTEGER | not null, → `dishes(id)` |
| `review_id` | INTEGER | not null, → `reviews(id)` |
| `sentiment` | TEXT | one of `positive`, `negative`, `mixed`, `neutral` |
| `quote` | TEXT | the supporting excerpt |
| `extracted_at` | TEXT | |

### `dish_sentiment_summary` (view)
The per-dish leaderboard from the proposal: `dish_id`, `dish_name`, `restaurant_name`,
`mention_count`, `positive`, `negative`, `mixed`. Every dish appears with zero counts
until `dish_mentions` is filled — real zeros, not NULL, so the columns are safe to add up.

`positive + negative + mixed` always equals `mention_count`. `mixed` is the remainder
rather than a count of `sentiment = 'mixed'`, because `neutral` and `mixed` mean the same
thing here: anything not clearly positive or negative is mixed. That holds the sum
together even if a `neutral` row reaches the table, which the CHECK constraint allows
though `calculate.py` never writes one.

The view is dropped and recreated by `schema.sql`, not created with `IF NOT EXISTS`.
Otherwise a database built from an older schema would keep its stale definition forever,
since `IF NOT EXISTS` leaves an existing view alone. Views hold no data, so this is free.

All foreign keys cascade on delete. Indexes cover `dishes(restaurant_id)`, `dishes(name)`,
`reviews(restaurant_id)`, `reviews(rating)`, `review_media(review_id)`,
`restaurant_types(type)`, and both `dish_mentions` foreign keys.

### Notes on the data

- `price_raw` keeps the menu's own wording (`"M: $2.5, L: $3.25"`, `"+1"`). `price_min`
  is the first number found in it — sort on it, but display `price_raw`, because the
  parse is lossy (`"+1"` is a surcharge, not a price, and becomes `1.0`). Plenty of
  dishes have no price at all.
- Some reviews are a rating with no words, so `reviews.text` is NULL for those. Filter
  them out before any text analysis.
- Dish names repeat within a restaurant (same dish across sections, size variants), so
  `(restaurant_id, name)` is deliberately not unique.
- Menu completeness varies. Extraction is a language model reading a page or a photo, and
  re-running the same restaurant can yield a different number of items — treat menus as a
  good sample of what a place serves, not an exhaustive inventory.

## Re-collect the data (needs API keys)

Only necessary to gather *new* data — skip this if you just want the database.

```bash
cd data/collect
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in SERPER_API_KEY and GROQ_API_KEY
./.venv/bin/python collect.py --limit 5
```

Finds restaurants within 1km of Columbia (116th & Broadway), then for each one pulls
Google reviews and hunts for a menu through four tiers, text before images:

1. the restaurant's own website
2. menu pages found via web search
3. Google Maps review photos captioned as a menu
4. open web image search

Tiers 1–2 read real menu text; tiers 3–4 read a photo with a vision model.

### Watch the API budgets

- **Serper is credit-metered.** The restaurant search sweep costs ~156 credits, so it is
  cached to `output/_places_cache.json` and reused. Pass `--refresh-places` only when
  you actually want to re-discover restaurants.
- **Groq's free tier caps each model at 200k tokens/day.** The vision models hit that
  limit during a full run, at which point only tiers 1–2 keep working and the rest of
  the restaurants get saved with reviews but no menu.

### Useful flags

| Flag | Purpose |
|---|---|
| `--limit N` | process only N restaurants (`0` = all) |
| `--resume` | skip restaurants already in the output file |
| `--list-only` | just enumerate what's in range, no menus or reviews |
| `--max-review-pages N` | review pages per restaurant, ~20 reviews each (default 3) |
| `--refresh-places` | re-run the search sweep instead of using the cache |

Runs checkpoint after every restaurant, so an interrupted run loses at most one and
`--resume` picks it back up.
