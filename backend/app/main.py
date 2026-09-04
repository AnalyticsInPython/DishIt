"""DishIt serving API — implements specs/api-contract.md against the
canonical database built by the ingestion and calculation pipeline.

Run:
    uv sync
    uv run uvicorn app.main:app --app-dir backend --reload
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, search
from .geo import haversine_meters

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend"
INDEX = FRONTEND_DIR / "index.html"

# A single conservative cutoff until real mention volume establishes whether
# per-restaurant or per-dish thresholds are warranted.
THRESHOLD = 5
# A dish needs both real volume and a genuine split to earn the badge —
# proposed in specs/api-contract.md's open questions, applied here.
CONTROVERSIAL_MIN_MINORITY_SHARE = 0.35

# Score bands the label reads from. The card prints the score and the label side
# by side, so the label has to be a description of the score or the two contradict
# each other in the same breath.
POSITIVE_MIN_SCORE = 65
MIXED_MIN_SCORE = 40


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    try:
        yield
    finally:
        db.shutdown_db()


app = FastAPI(lifespan=lifespan)

Db = Annotated[db.Database, Depends(db.get_db)]


# --- row → contract-shape builders ----------------------------------------

def restaurant_from_row(row: dict, lat: float | None, lng: float | None) -> dict:
    distance_m = None
    if (
        lat is not None
        and lng is not None
        and row["latitude"] is not None
        and row["longitude"] is not None
    ):
        distance_m = haversine_meters(lat, lng, row["latitude"], row["longitude"])
    return {
        "id": row["id"],
        "name": row["name"],
        "neighborhood": None,
        "cuisine": row["category"],
        "cross_street": row["address"],
        "lat": row["latitude"],
        "lng": row["longitude"],
        "distance_m": distance_m,
        "image": None,
        "hours_today": None,
    }


def sentiment_and_flags(row: dict) -> tuple[dict, int, bool]:
    """Read the sentiment rollup off a DISH_RESTAURANT_JOIN_SQL row.

    The counts arrive on the row itself, joined from dish_sentiment_summary —
    querying the view once per dish meant thousands of round trips per request,
    which a local file forgives and a network-backed database does not.
    """
    positive = row["positive"] or 0
    negative = row["negative"] or 0
    mention_count = row["mention_count"] or 0

    non_neutral = positive + negative
    score = round(100 * positive / non_neutral) if non_neutral else 0
    minority_share = min(positive, negative) / non_neutral if non_neutral else 0
    is_controversial = (
        mention_count >= THRESHOLD and minority_share >= CONTROVERSIAL_MIN_MINORITY_SHARE
    )
    # Derived from the score, not from whether a mixed mention exists. The old
    # rule labelled a dish "mixed" whenever `mixed` was non-zero, and `mixed` is
    # the remainder (mention_count - positive - negative), so a dish with 7
    # positive mentions, 0 negative and 2 mixed read "100% mixed" on the card.
    if mention_count == 0:
        label = "neutral"
    elif non_neutral == 0:
        # Every mention was mixed, so there is no polarity to score. The score is
        # 0 here by the guard above, which the bands would otherwise read as
        # negative — the opposite of what no-polarity means.
        label = "mixed"
    elif score >= POSITIVE_MIN_SCORE:
        label = "positive"
    elif score >= MIXED_MIN_SCORE:
        label = "mixed"
    else:
        label = "negative"

    sentiment = {
        "label": label,
        "score": score,
        "positive": positive,
        "negative": negative,
        # calculate.py maps model-neutral output to canonical "mixed"; the
        # response shape has no mixed count, so neutral is always zero.
        "neutral": 0,
    }
    return sentiment, mention_count, is_controversial


def dish_from_join_row(row: dict, lat: float | None, lng: float | None) -> dict:
    """Build a Dish from one row of DISH_RESTAURANT_JOIN (dish + restaurant + rollup)."""
    sentiment, mention_count, is_controversial = sentiment_and_flags(row)
    restaurant = {
        "id": row["restaurant_id"],
        "name": row["restaurant_name"],
        "neighborhood": None,
        "cuisine": row["category"],
        "cross_street": row["address"],
        "lat": row["latitude"],
        "lng": row["longitude"],
        "distance_m": (
            haversine_meters(lat, lng, row["latitude"], row["longitude"])
            if lat is not None
            and lng is not None
            and row["latitude"] is not None
            and row["longitude"] is not None
            else None
        ),
        "image": None,
        "hours_today": None,
    }
    return {
        "id": row["dish_id"],
        "name": row["name"],
        "restaurant": restaurant,
        "sentiment": sentiment,
        "mention_count": mention_count,
        "is_controversial": is_controversial,
        # Every mention comes from a Google review today, so the public count is
        # exactly the mention count and there is nothing extra to look up.
        "source_mix": {"critic": 0, "public": mention_count},
        "on_current_menu": True,
    }


DISH_RESTAURANT_JOIN_SQL = """
    SELECT
        d.id AS dish_id, d.name,
        r.id AS restaurant_id, r.name AS restaurant_name, r.address, r.category,
        r.latitude, r.longitude,
        COALESCE(s.mention_count, 0) AS mention_count,
        COALESCE(s.positive, 0)      AS positive,
        COALESCE(s.negative, 0)      AS negative
    FROM dishes d
    JOIN restaurants r ON r.id = d.restaurant_id
    LEFT JOIN dish_sentiment_summary s ON s.dish_id = d.id
"""


def all_dishes(database: db.Database, lat: float | None, lng: float | None) -> list[dict]:
    return [
        dish_from_join_row(row, lat, lng) for row in database.rows(DISH_RESTAURANT_JOIN_SQL)
    ]


def all_restaurants(database: db.Database, lat: float | None, lng: float | None) -> list[dict]:
    return [
        restaurant_from_row(row, lat, lng)
        for row in database.rows("SELECT * FROM restaurants")
    ]


def top_n(dishes: Iterable[dict], key, n: int = 6) -> list[dict]:
    return sorted(dishes, key=key, reverse=True)[:n]


def _tie_break_distance(row: dict, lat: float | None, lng: float | None) -> float:
    """Distance used only to break ties within search routing, never shown
    to the user — 0 when either side's coordinates are missing, rather
    than raising, since routing shouldn't fail over a missing lat/lng."""
    if lat is None or lng is None or row["latitude"] is None or row["longitude"] is None:
        return 0.0
    return haversine_meters(lat, lng, row["latitude"], row["longitude"])


# --- endpoints --------------------------------------------------------------

@app.get("/api/health")
def get_health(database: Db):
    """Liveness for the platform health check, which runs every 30s forever.

    Deliberately not /api/popular, which builds all 3,129 dishes and sorts them
    twice on every hit. SELECT 1 is close to free but still proves the connection
    is alive, which a static {"ok": true} would not: a replica that failed to open
    should fail the check and take the machine down, so it reboots and resyncs.
    """
    database.one("SELECT 1")
    return {"ok": True}


@app.get("/api/popular")
def get_popular(
    database: Db,
    lat: float | None = None,
    lng: float | None = None,
):
    dishes = [d for d in all_dishes(database, lat, lng) if d["mention_count"] >= THRESHOLD]
    return {
        "talked_about": top_n(dishes, key=lambda d: d["mention_count"]),
        "controversial": top_n(
            (d for d in dishes if d["is_controversial"]), key=lambda d: d["mention_count"]
        ),
        "top_rated": top_n(dishes, key=lambda d: d["sentiment"]["score"]),
    }


@app.get("/api/search")
def get_search(
    database: Db,
    q: str = Query(..., min_length=1),
    lat: float | None = None,
    lng: float | None = None,
):
    dish_rows = database.rows(DISH_RESTAURANT_JOIN_SQL)
    restaurant_rows = database.rows("SELECT * FROM restaurants")

    search_dishes = [
        {"id": r["dish_id"], "name": r["name"], "mention_count": r["mention_count"] or 0}
        for r in dish_rows
    ]
    search_restaurants = [
        {
            "id": r["id"],
            "name": r["name"],
            "cuisine": r["category"],
            "distance_m": _tie_break_distance(r, lat, lng),
        }
        for r in restaurant_rows
    ]

    routed = search.route(q, search_restaurants, search_dishes)

    dish_by_id = {row["dish_id"]: row for row in dish_rows}
    rest_by_id = {row["id"]: row for row in restaurant_rows}

    routed_dishes = [
        dish_from_join_row(dish_by_id[d["id"]], lat, lng) for d in routed["dishes"]
    ]
    routed_rests = [
        restaurant_from_row(rest_by_id[r["id"]], lat, lng) for r in routed["restaurants"]
    ]

    if routed["result_type"] == "dishes":
        primary = routed_dishes
        secondary = {"type": "restaurants", "items": routed_rests[:4]} if routed_rests else None
    else:
        primary = routed_rests
        secondary = {"type": "dishes", "items": routed_dishes[:4]} if routed_dishes else None

    return {
        "query": routed["query"],
        "result_type": routed["result_type"],
        "matched_on": routed["matched_on"],
        "primary": primary,
        # Both complete lists. result_type is only a guess about which to lead
        # with, and the results page has a manual Dishes/Restaurants toggle that
        # needs the full other list — secondary is capped at 4 and can't serve it.
        "all": {"dishes": routed_dishes, "restaurants": routed_rests},
        "secondary": secondary,
    }


@app.get("/api/restaurants")
def get_restaurants(
    database: Db,
    lat: float | None = None,
    lng: float | None = None,
):
    """Every restaurant, nearest first, each with its best-scoring dish.

    The home page's "Restaurants nearby" section needs the whole list, and its
    cards print a top dish — neither of which /api/restaurants/{id} can serve.
    """
    restaurant_rows = database.rows("SELECT * FROM restaurants")
    dish_rows = database.rows(DISH_RESTAURANT_JOIN_SQL)

    # Best dish per restaurant, on the same threshold the cards use to decide
    # whether a score is trustworthy enough to show at all.
    best: dict[int, dict] = {}
    for row in dish_rows:
        sentiment, mention_count, _ = sentiment_and_flags(row)
        if mention_count < THRESHOLD:
            continue
        current = best.get(row["restaurant_id"])
        if current is None or sentiment["score"] > current["score"]:
            best[row["restaurant_id"]] = {"name": row["name"], "score": sentiment["score"]}

    restaurants = []
    for row in restaurant_rows:
        restaurant = restaurant_from_row(row, lat, lng)
        restaurant["top_dish"] = best.get(row["id"])
        restaurants.append(restaurant)

    # Nearest first; restaurants with no coordinates, or no caller location to
    # measure from, sort last rather than blowing up the comparison.
    restaurants.sort(key=lambda r: (r["distance_m"] is None, r["distance_m"] or 0))
    return {"restaurants": restaurants}


@app.get("/api/restaurants/{restaurant_id}")
def get_restaurant(
    database: Db,
    restaurant_id: int,
    lat: float | None = None,
    lng: float | None = None,
):
    row = database.one("SELECT * FROM restaurants WHERE id = ?", (restaurant_id,))
    if row is None:
        raise HTTPException(404, "No restaurant with that id.")
    dish_rows = database.rows(f"{DISH_RESTAURANT_JOIN_SQL} WHERE r.id = ?", (restaurant_id,))
    dishes = [dish_from_join_row(d, lat, lng) for d in dish_rows]
    dishes.sort(key=lambda d: d["mention_count"], reverse=True)
    return {"restaurant": restaurant_from_row(row, lat, lng), "dishes": dishes}


@app.get("/api/dishes/{dish_id}")
def get_dish(
    database: Db,
    dish_id: int,
    lat: float | None = None,
    lng: float | None = None,
):
    row = database.one(f"{DISH_RESTAURANT_JOIN_SQL} WHERE d.id = ?", (dish_id,))
    if row is None:
        raise HTTPException(404, "No dish with that id.")
    dish = dish_from_join_row(row, lat, lng)

    quote_rows = database.rows(
        """
        SELECT m.quote AS text, m.sentiment, r.url AS source_url
        FROM dish_mentions m
        JOIN reviews r ON r.id = m.review_id
        WHERE m.dish_id = ?
        ORDER BY
            CASE m.sentiment
                WHEN 'negative' THEN 1
                WHEN 'mixed' THEN 2
                WHEN 'positive' THEN 3
                ELSE 4
            END,
            m.id
        LIMIT 3
        """,
        (dish_id,),
    )
    quotes = [
        {
            "text": q["text"],
            "source_type": "google",
            "source_label": "Google",
            "source_url": q["source_url"],
            "sentiment": q["sentiment"],
        }
        for q in quote_rows
    ]

    also_at_rows = database.rows(
        f"{DISH_RESTAURANT_JOIN_SQL} WHERE d.name = ? COLLATE NOCASE",
        (row["name"],),
    )
    also_at = [dish_from_join_row(r, lat, lng) for r in also_at_rows]
    also_at.sort(key=lambda d: d["sentiment"]["score"], reverse=True)

    return {"dish": dish, "quotes": quotes, "also_at": also_at}


# --- static frontend ---------------------------------------------------------

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/{path:path}")
def spa(path: str) -> FileResponse:
    candidate = FRONTEND_DIR / path
    if path and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(INDEX)
