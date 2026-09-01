# DishIt API contract

Draft — proposed by the frontend, needs backend sign-off.

The frontend is built against `frontend/fixtures.json`, which matches these shapes exactly. When these endpoints exist, the four functions in the `DATA ACCESS` block of `frontend/app.js` get swapped for `fetch()` calls and nothing else in the frontend changes. **If a shape here doesn't suit the database, change it here first** — then both sides move together instead of discovering the mismatch on Day 4.

All responses are JSON. All endpoints are namespaced under `/api/`.

## Objects

### Restaurant

```json
{
  "id": "r1",
  "name": "Osteria Novanta",
  "neighborhood": "Manhattan Valley",
  "cuisine": "Italian",
  "cross_street": "108th & Amsterdam",
  "lat": 40.8009,
  "lng": -73.9648,
  "distance_m": 420,
  "image": null
}
```

`distance_m` is computed per request from the caller's `lat`/`lng`, not stored.
`image` is nullable — the frontend draws a generated placeholder when it's null, so shipping without photography is fine.

### Dish

```json
{
  "id": "d1",
  "name": "Cacio e Pepe",
  "restaurant_id": "r1",
  "sentiment": {
    "label": "positive",
    "score": 82,
    "positive": 19,
    "negative": 3,
    "neutral": 1
  },
  "mention_count": 23,
  "is_controversial": false,
  "source_mix": { "critic": 4, "public": 19 },
  "on_current_menu": true
}
```

- `label` is one of `positive` / `negative` / `mixed`.
- `score` is the integer percentage of non-neutral mentions that are positive, 0–100.
- `positive + negative + neutral` should equal `mention_count`. The frontend draws the sentiment bar from these three counts, not from `score`.
- **`on_current_menu` is nullable, and `null` is meaningful**: it means *we never checked*, which is different from `false` (*we checked and it's gone*). This keeps menu-matching an optional enrichment rather than a blocking dependency — if menu scraping never lands, every dish ships `null` and the UI simply doesn't claim anything.

### Quote

```json
{
  "text": "The pepper actually bites.",
  "source_type": "reddit",
  "source_label": "r/UpperWestSide",
  "source_url": "https://...",
  "sentiment": "positive"
}
```

`source_type` is one of `critic` / `reddit` / `google`. `source_label` is what the UI prints, so it should be human-readable.

## Endpoints

### `GET /api/popular?lat=&lng=`

Powers the landing-page carousel. Three separate lists, since all three tabs render on one page load.

```json
{
  "talked_about":  [Dish],
  "controversial": [Dish],
  "top_rated":     [Dish]
}
```

- `talked_about` — sorted by `mention_count` desc
- `controversial` — only `is_controversial`, sorted by `mention_count` desc
- `top_rated` — sorted by `sentiment.score` desc

All three exclude dishes below the mention threshold. Six per list is enough.

### `GET /api/search?q=&lat=&lng=`

```json
{
  "query": "cacio e pepe",
  "result_type": "dishes",
  "matched_on": "dish_name",
  "primary": [Dish],
  "secondary": { "type": "restaurants", "items": [Restaurant] }
}
```

`result_type` is `dishes` or `restaurants` and tells the frontend which card type leads. `matched_on` is `dish_name` / `restaurant_name` / `cuisine` / `none` and is shown to the user so the routing is legible rather than mysterious. `secondary` is nullable.

**Intent routing.** Three buckets are scored independently — restaurant name, dish name, cuisine — and the strongest match decides which type leads. The reference implementation is in `search()` in `frontend/app.js`; the scoring is:

| Match | Score |
|---|---|
| exact | 100 |
| prefix | 80 |
| substring | 60 |
| token overlap | 40 × (matched ÷ query tokens) |

Cuisine is tested first and needs ≥ 80 to win, because a bare cuisine term like `Italian` matches no name field at all and would otherwise fall through to a weak partial match on something unrelated. Ties go to restaurants. If the winning bucket has no results, it falls back to the other type rather than returning empty.

This will misroute sometimes — `Joe's Pizza` is genuinely ambiguous — so the UI carries a manual Dishes/Restaurants toggle. **The route is a good guess, not a commitment**, which means the endpoint should return matches of both types whenever both exist.

### `GET /api/restaurants/{id}`

```json
{ "restaurant": Restaurant, "dishes": [Dish] }
```

Dishes sorted by `mention_count` desc. **Include dishes below the threshold** — the UI renders them with a "thin data" treatment rather than hiding them, which is more honest than silently dropping them and reads better to a grader.

### `GET /api/dishes/{id}`

```json
{
  "dish": Dish,
  "quotes": [Quote],
  "also_at": [Dish]
}
```

`also_at` is **the same dish at other restaurants**, sorted by `sentiment.score` desc, and includes the dish itself so the UI can highlight the current one in the list. This is what makes dish-name canonicalization visible as a feature rather than invisible plumbing — it needs dish names deduplicated across restaurants, not just within one.

Two or three quotes is enough; prefer a mix of sentiments and a mix of source types over the top-scoring ones.

## Open questions for the backend

1. **Is the cross-restaurant dish query planned?** `/api/search` and `also_at` both need dishes matched by canonical name *across* restaurants. If dish canonicalization is currently scoped per-restaurant, this needs raising now — it's the one place the frontend needs something the original proposal didn't specify.
2. **What is the mention threshold, and is it configurable?** The frontend reads it from the payload (`_threshold` in fixtures) rather than hardcoding 5, so it can be tuned once real mention counts are known.
3. **How is `is_controversial` computed?** Currently treated as a backend-supplied boolean. Proposed: above the mention threshold *and* the minority sentiment is at least ~35% of non-neutral mentions.
