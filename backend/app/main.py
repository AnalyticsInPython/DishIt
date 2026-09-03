"""DishIt serving API — implements specs/api-contract.md against the
canonical SQLite database built by the ingestion and calculation pipeline.

Run:
    uv sync
    uv run uvicorn app.main:app --app-dir backend --reload
"""

from __future__ import annotations

import sqlite3
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(lifespan=lifespan)

DbConnection = Annotated[sqlite3.Connection, Depends(db.get_db)]


# --- row → contract-shape builders ----------------------------------------

def restaurant_from_row(row: sqlite3.Row, lat: float | None, lng: float | None) -> dict:
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


def sentiment_and_flags(connection: sqlite3.Connection, dish_id: int) -> tuple[dict, int, bool]:
    summary = connection.execute(
        """
        SELECT mention_count, positive, negative, mixed
        FROM dish_sentiment_summary
        WHERE dish_id = ?
        """,
        (dish_id,),
    ).fetchone()
    if summary is None:
        empty = {"label": "neutral", "score": 0, "positive": 0, "negative": 0, "neutral": 0}
        return empty, 0, False

    positive, negative, mixed = (
        summary["positive"] or 0,
        summary["negative"] or 0,
        summary["mixed"] or 0,
    )
    non_neutral = positive + negative
    score = round(100 * positive / non_neutral) if non_neutral else 0
    mention_count = summary["mention_count"]
    minority_share = min(positive, negative) / non_neutral if non_neutral else 0
    is_controversial = (
        mention_count >= THRESHOLD and minority_share >= CONTROVERSIAL_MIN_MINORITY_SHARE
    )
    if mention_count == 0:
        label = "neutral"
    elif mixed or (positive and negative):
        label = "mixed"
    elif positive:
        label = "positive"
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


def source_mix(connection: sqlite3.Connection, dish_id: int) -> dict:
    row = connection.execute(
        """
        SELECT
            COUNT(m.id) AS public
        FROM dish_mentions m
        WHERE m.dish_id = ?
        """,
        (dish_id,),
    ).fetchone()
    return {"critic": 0, "public": row["public"] or 0}


def dish_from_join_row(
    connection: sqlite3.Connection, row: sqlite3.Row, lat: float | None, lng: float | None
) -> dict:
    """Build a Dish from one row of DISH_RESTAURANT_JOIN (dish + its restaurant columns)."""
    sentiment, mention_count, is_controversial = sentiment_and_flags(connection, row["dish_id"])
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
        "source_mix": source_mix(connection, row["dish_id"]),
        "on_current_menu": True,
    }


DISH_RESTAURANT_JOIN_SQL = """
    SELECT
        d.id AS dish_id, d.name,
        r.id AS restaurant_id, r.name AS restaurant_name, r.address, r.category,
        r.latitude, r.longitude
    FROM dishes d JOIN restaurants r ON r.id = d.restaurant_id
"""


def all_dishes(connection: sqlite3.Connection, lat: float | None, lng: float | None) -> list[dict]:
    rows = connection.execute(DISH_RESTAURANT_JOIN_SQL).fetchall()
    return [dish_from_join_row(connection, row, lat, lng) for row in rows]


def all_restaurants(
    connection: sqlite3.Connection, lat: float | None, lng: float | None
) -> list[dict]:
    rows = connection.execute("SELECT * FROM restaurants").fetchall()
    return [restaurant_from_row(row, lat, lng) for row in rows]


def top_n(dishes: Iterable[dict], key, n: int = 6) -> list[dict]:
    return sorted(dishes, key=key, reverse=True)[:n]


def _tie_break_distance(row: sqlite3.Row, lat: float | None, lng: float | None) -> float:
    """Distance used only to break ties within search routing, never shown
    to the user — 0 when either side's coordinates are missing, rather
    than raising, since routing shouldn't fail over a missing lat/lng."""
    if lat is None or lng is None or row["latitude"] is None or row["longitude"] is None:
        return 0.0
    return haversine_meters(lat, lng, row["latitude"], row["longitude"])


# --- endpoints --------------------------------------------------------------

@app.get("/api/popular")
def get_popular(
    connection: DbConnection,
    lat: float | None = None,
    lng: float | None = None,
):
    dishes = [d for d in all_dishes(connection, lat, lng) if d["mention_count"] >= THRESHOLD]
    return {
        "talked_about": top_n(dishes, key=lambda d: d["mention_count"]),
        "controversial": top_n(
            (d for d in dishes if d["is_controversial"]), key=lambda d: d["mention_count"]
        ),
        "top_rated": top_n(dishes, key=lambda d: d["sentiment"]["score"]),
    }


@app.get("/api/search")
def get_search(
    connection: DbConnection,
    q: str = Query(..., min_length=1),
    lat: float | None = None,
    lng: float | None = None,
):
    dish_rows = connection.execute(DISH_RESTAURANT_JOIN_SQL).fetchall()
    restaurant_rows = connection.execute("SELECT * FROM restaurants").fetchall()

    search_dishes = [
        {
            "id": r["dish_id"],
            "name": r["name"],
            "mention_count": sentiment_and_flags(connection, r["dish_id"])[1],
        }
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
        dish_from_join_row(connection, dish_by_id[d["id"]], lat, lng) for d in routed["dishes"]
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
        "secondary": secondary,
    }


@app.get("/api/restaurants/{restaurant_id}")
def get_restaurant(
    connection: DbConnection,
    restaurant_id: int,
    lat: float | None = None,
    lng: float | None = None,
):
    row = connection.execute("SELECT * FROM restaurants WHERE id = ?", (restaurant_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No restaurant with that id.")
    dish_rows = connection.execute(
        f"{DISH_RESTAURANT_JOIN_SQL} WHERE r.id = ?", (restaurant_id,)
    ).fetchall()
    dishes = [dish_from_join_row(connection, d, lat, lng) for d in dish_rows]
    dishes.sort(key=lambda d: d["mention_count"], reverse=True)
    return {"restaurant": restaurant_from_row(row, lat, lng), "dishes": dishes}


@app.get("/api/dishes/{dish_id}")
def get_dish(
    connection: DbConnection,
    dish_id: int,
    lat: float | None = None,
    lng: float | None = None,
):
    row = connection.execute(f"{DISH_RESTAURANT_JOIN_SQL} WHERE d.id = ?", (dish_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No dish with that id.")
    dish = dish_from_join_row(connection, row, lat, lng)

    quote_rows = connection.execute(
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
    ).fetchall()
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

    also_at_rows = connection.execute(
        f"{DISH_RESTAURANT_JOIN_SQL} WHERE d.name = ? COLLATE NOCASE",
        (row["name"],),
    ).fetchall()
    also_at = [dish_from_join_row(connection, r, lat, lng) for r in also_at_rows]
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
