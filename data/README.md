# DishIt data pipeline

Two stages: `collect/` gathers restaurants, menus and reviews from the web into JSON;
`db/` loads that JSON into SQLite.

The collected JSON is committed, so **you do not need API keys to build the database.**

## Build the database

No dependencies, no virtualenv — the loader is Python standard library only:

```bash
python3 data/db/load_db.py
```

Creates `data/db/dishit.db` (gitignored) from `data/collect/output/restaurants_full.json`:

| Table | Rows | What it holds |
|---|---|---|
| `restaurants` | 46 | one row per place, keyed on Google `place_id` |
| `restaurant_types` | 145 | each place's Google categories |
| `dishes` | 1,435 | menu items |
| `reviews` | 1,172 | Google reviews |
| `review_media` | 2,195 | reviewer photo URLs and captions |
| `dish_mentions` | 0 | filled by the dish-extraction step, not the loader |

Plus a `dish_sentiment_summary` view — the per-dish leaderboard, empty until
`dish_mentions` is populated.

Re-running is safe: restaurants key on `place_id` and reviews on Google's review id,
so a later run updates in place rather than duplicating.

Flags: `--include-menuless` loads all 110 restaurants instead of only the 46 that have
menus; `--json` and `--db` override the paths.

### Notes on the data

- `price_raw` keeps the menu's own wording (`"M: $2.5, L: $3.25"`, `"+1"`). `price_min`
  is the first number found in it — useful for sorting, but not always the real price
  (`"+1"` is a surcharge, and parses to `1.0`). 193 dishes have no price.
- 104 reviews are ratings with no text, so `reviews.text` is NULL for those.
- Dish names repeat within a restaurant (same dish across sections, size variants), so
  `(restaurant_id, name)` is deliberately not unique.

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
