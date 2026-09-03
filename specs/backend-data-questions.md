# DishIt — open schema/data questions for the backend

Context for whoever picks this up (and their Claude session): DishIt is a 4-person
one-week bootcamp project. The frontend (`jonye-frontend-wireframe` branch) is built
against `specs/api-contract.md` and `frontend/fixtures.json`, both of which assume a
fictional, simplified restaurant shape. Meanwhile `data-collection-pipeline` just landed
a real dataset — 46–110 real UWS restaurants, real Google reviews, real scraped menus
(see `data/db/schema.sql`, `data/db/load_db.py`, `data/README.md`) — that doesn't match
those assumptions in several places. These need answers before the frontend can be
wired to real data. Please update `specs/api-contract.md` with whatever gets decided,
since that's the file the frontend actually builds against.

## 1. Neighborhood / cross-street

`specs/api-contract.md`'s `Restaurant` shape has `neighborhood` and `cross_street`
fields; the real schema (`data/db/schema.sql`) only has `address` and `latitude`/
`longitude`. Neither is derived anywhere yet.

**Decide:** derive neighborhood from a lat/lng bounding-box lookup (Morningside
Heights / Upper West Side / Manhattan Valley), parse something out of `address`, or
drop `neighborhood`/`cross_street` from the contract and change what the frontend
displays instead.

## 2. Distance: per-viewer vs. fixed anchor

The product decision so far (see `specs/api-contract.md`) is: distance is computed
per-request from the viewer's own lat/lng via the haversine formula, divided by an
average walking speed — never stored, always recomputed. But `load_db.py` writes a
`distance_m` column into `restaurants` at collection time, computed once from a single
fixed anchor point (Columbia, 116th & Broadway per `data/README.md`).

**Decide:** is `distance_m` in the real DB meant to be thrown away and recomputed
server-side per-request (matching the original design, and what
`backend/app/geo.py`'s `haversine_meters()` already does), or is the product now
standardizing on one fixed reference point instead of per-viewer distance? If it's
the latter, the frontend's location-switching UI needs to be rethought — right now
switching locations only re-sorts distance for fixture data, not real restaurants,
since real `distance_m` won't change no matter where the viewer says they are.

## 3. Sentiment: does "mixed" replace "neutral," or do both exist?

This is the one most likely to break things silently if left undecided.

- The **old** pipeline (`data/calculate/dish_sentiment_calculator.py`, still what
  `backend/app/db.py` on `jonye-backend-api`/`jacob-backend-api` is built against) has
  an unconstrained `mentions.sentiment TEXT` column, and in practice only ever
  produces `positive` / `negative` / `neutral`.
- The **new** schema (`data/db/schema.sql`, `data-collection-pipeline` branch) has
  `dish_mentions.sentiment CHECK (sentiment IN ('positive','negative','mixed','neutral'))`
  — four values, `mixed` and `neutral` both present as distinct options.
- But that same new schema's `dish_sentiment_summary` VIEW only aggregates
  `positive`, `negative`, and `mixed` counts — **it has no neutral column at all.**
  So the view's own author functionally dropped neutral even though the CHECK
  constraint still allows it.

**Decide:** is a single mention allowed to be tagged "mixed" (e.g. one reviewer both
praised and criticized the same dish in one sentence), and if so, does "mixed" count
toward positive, negative, both, or neither in the aggregate score? Does "neutral"
still exist as a real category, or was it supposed to be replaced by "mixed"
everywhere (in which case the CHECK constraint and the extraction code both need to
agree on that)? Whatever's decided needs to be reflected in `dish_sentiment_summary`
and in the `Dish.sentiment` shape in `specs/api-contract.md` (currently
`{label, score, positive, negative, neutral}` — no `mixed` field at all).

## 4. Cuisine (singular) vs. category + restaurant_types (plural)

`specs/api-contract.md`'s `Restaurant.cuisine` is a single string, and the frontend's
search intent-router (`frontend/app.js`'s `search()`, ported to
`backend/app/search.py`) scores against exactly one cuisine field per restaurant. The
real schema has `restaurants.category` (singular, Google's primary type) *and* a
separate `restaurant_types` join table (a place can be "Chinese restaurant" AND
"Noodle shop").

**Decide:** does the API expose just `category` as the one `cuisine` string (simplest,
matches current contract), or should search/display consider all of a restaurant's
types? If the latter, the search-scoring function needs to check all types, not one
field, in both `frontend/app.js` and `backend/app/search.py` (they're kept in sync
deliberately — same logic, ported line-for-line).

## 5. hours_today has no data source

Earlier decision was to include `hours_today` in restaurant info once available (see
`specs/api-contract.md`). Nothing in the real collection pipeline (`data/collect/`)
gathers live/hours data.

**Decide:** is this feature dead for the demo, or does someone need to add hours
collection to the pipeline? If it's dead, say so — the frontend already handles a
missing `hours_today` gracefully (renders nothing), so no urgency, just want an
explicit answer instead of it quietly never arriving.

## 6. Real menu prices exist now — surface them?

`dishes.price_raw`/`price_min` are real, already-collected data (from the menu
scrape) that the original proposal listed as an out-of-scope "nice to have." Now that
it's free (not a research problem, just a field), worth a explicit yes/no on whether
`specs/api-contract.md`'s `Dish` shape should include price, and if the frontend
should show it on the dish card/modal or restaurant detail view.

## 7. `on_current_menu` can now actually be answered

Both the frontend and `backend/app/main.py` currently hardcode `on_current_menu` to
`null` ("unverified") for every dish, because there was no real menu to check against.
Now that dishes are extracted directly from a real menu scrape
(`data/db/load_db.py`), a dish's presence in the current `dishes` table basically *is*
the on-current-menu signal (modulo how stale the scrape is).

**Decide:** should `on_current_menu` be wired to `true` for any dish that exists in
the real menu table, rather than staying `null` forever?

## 8. The sentiment-extraction pipeline isn't pointed at the real data yet

Not a design question, just a status flag: `jacob-backend-api`'s
`backend/app/db.py` reads from a database produced by
`data/calculate/dish_sentiment_calculator.py` (old spaCy/GLiNER/ABSA pipeline, old
`restaurants/sources/dishes/mentions` schema), which has not been updated to read
William's new real dataset at all. Right now there are two disconnected outputs:
the real 46-restaurant dataset (reviews collected, `dish_mentions` empty — see
`data/README.md`'s row counts) and the old extractor (still expects the old shape).
**Someone needs to either point the old extractor at the new `reviews` table, or
write a new extraction step that reads `reviews`/`dishes` from `data/db/schema.sql`
and writes into `dish_mentions`**, before any of the above questions matter in
practice — there's no real sentiment data to answer them with yet.

## 9. Once real dish-mention volume exists, revisit two guessed constants

Both currently live in `backend/app/main.py` (and `THRESHOLD` is mirrored in
`frontend/app.js`) and were picked before anyone knew real mention-count
distributions:

- `THRESHOLD = 5` — minimum mentions before a dish is scored at all.
- `CONTROVERSIAL_MIN_MINORITY_SHARE = 0.35` — minimum positive/negative split share
  before a dish is flagged "controversial."

**Ask:** once `dish_mentions` has real rows, pull the actual distribution of mentions
per dish and sanity-check whether 5 and 0.35 are reasonable, too strict, or too loose.
