"""SQLite access for the serving API.

The working database is never committed — it's rebuilt on first run from
two inputs, both of which *are* committed:

  1. The seed export from the ingestion pipeline (currently Baylander only,
     with real extracted mentions/sentiment/quotes from real Google reviews).
  2. The fictional restaurants already living in frontend/fixtures.json,
     converted into the same restaurants/sources/dishes/mentions shape.
     Reusing that file rather than hand-writing a second fake dataset
     keeps the wireframe and this API showing identical demo content.

Schema note: the seed export's `restaurants` table has no lat/lng,
cross_street, or hours_today columns yet — those are added here as an
ALTER TABLE migration rather than editing the ingestion scripts that own
REVIEW_OUTPUT_SCHEMA, so this file layers cleanly on top of their export
instead of forking it.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_DB = ROOT / "Data Files for Testing" / "Baylander_Manual_Review_by_dish.sqlite3"
FIXTURES_JSON = ROOT / "frontend" / "fixtures.json"
WORKING_DB = ROOT / "backend" / "data" / "dishit.db"

# Baylander has no real coordinates in the export (it's a boat bar in
# Greenpoint, outside the UWS scope) — approximate, real-world value, so
# distance math has something to work with rather than crashing on null.
BAYLANDER_LAT, BAYLANDER_LNG = 40.7306, -73.9576

MIGRATION_COLUMNS = {
    "lat": "REAL",
    "lng": "REAL",
    "cross_street": "TEXT",
    "hours_today": "TEXT",
}


def init_db() -> None:
    """Create the working database from the seed export on first run."""
    seed_db = Path(os.environ.get("DISHIT_SEED_DATABASE", DEFAULT_SEED_DB))
    if not seed_db.is_file():
        raise FileNotFoundError(
            f"Dish sentiment database not found: {seed_db}. "
            "Run data/calculate/calculate.py first, then set DISHIT_SEED_DATABASE "
            "to its *_review_by_dish.sqlite3 output."
        )
    WORKING_DB.parent.mkdir(parents=True, exist_ok=True)
    is_new = not WORKING_DB.exists()
    if is_new:
        shutil.copyfile(seed_db, WORKING_DB)

    with sqlite3.connect(WORKING_DB) as connection:
        _migrate_restaurants_table(connection)
        if is_new:
            _backfill_baylander_location(connection)
            _seed_dummy_restaurants(connection)


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency yielding one request-scoped connection."""
    connection = sqlite3.connect(WORKING_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def _migrate_restaurants_table(connection: sqlite3.Connection) -> None:
    existing = {row[1] for row in connection.execute("PRAGMA table_info(restaurants)")}
    for column, sql_type in MIGRATION_COLUMNS.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE restaurants ADD COLUMN {column} {sql_type}")
    connection.commit()


def _backfill_baylander_location(connection: sqlite3.Connection) -> None:
    connection.execute(
        "UPDATE restaurants SET lat = ?, lng = ? WHERE lat IS NULL",
        (BAYLANDER_LAT, BAYLANDER_LNG),
    )
    connection.commit()


def _seed_dummy_restaurants(connection: sqlite3.Connection) -> None:
    """Insert the fictional restaurants/dishes/mentions from fixtures.json.

    Fixtures store aggregate sentiment counts and a handful of hand-written
    quotes per dish, not one row per underlying review — so this expands
    each dish's positive/negative/neutral counts into that many synthetic
    mention rows, reusing the real hand-written quote text where fixtures
    provide one and a generic placeholder for the remainder. Each mention
    gets its own one-row source (source_type alternating critic/public to
    match the dish's source_mix split), which is a simplification of the
    real one-review-can-mention-several-dishes relationship, acceptable
    for placeholder data that exists to exercise the API, not to analyze.
    """
    fixtures = json.loads(FIXTURES_JSON.read_text(encoding="utf-8"))

    for restaurant in fixtures["restaurants"]:
        cursor = connection.execute(
            """
            INSERT INTO restaurants (
                name, neighborhood, cuisine, cross_street, lat, lng, hours_today
            )
            VALUES (:name, :neighborhood, :cuisine, :cross_street, :lat, :lng, :hours_today)
            """,
            restaurant,
        )
        restaurant_id = cursor.lastrowid
        dishes = [d for d in fixtures["dishes"] if d["restaurant_id"] == restaurant["id"]]
        quotes_by_dish = fixtures.get("quotes", {})

        for dish in dishes:
            dish_cursor = connection.execute(
                "INSERT INTO dishes (restaurant_id, canonical_name) VALUES (?, ?)",
                (restaurant_id, dish["name"]),
            )
            dish_id = dish_cursor.lastrowid
            quotes = quotes_by_dish.get(dish["id"], [])
            _seed_dish_mentions(connection, restaurant_id, dish_id, dish, quotes)

    connection.commit()


def _seed_dish_mentions(
    connection: sqlite3.Connection,
    restaurant_id: int,
    dish_id: int,
    dish: dict,
    quotes: list[dict],
) -> None:
    sentiment = dish["sentiment"]
    counts = {
        "positive": sentiment["positive"],
        "negative": sentiment["negative"],
        "neutral": sentiment["neutral"],
    }
    real_quotes_by_sentiment: dict[str, list[dict]] = {
        "positive": [], "negative": [], "neutral": [],
    }
    for quote in quotes:
        real_quotes_by_sentiment.setdefault(quote["sentiment"], []).append(quote)

    critic_budget = dish["source_mix"]["critic"]

    for sentiment_label, count in counts.items():
        available = real_quotes_by_sentiment.get(sentiment_label, [])
        for i in range(count):
            if i < len(available):
                quote_text = available[i]["text"]
                url = available[i].get("source_url")
            else:
                dish_name = dish["name"]
                quote_text = f"Placeholder {sentiment_label} mention for {dish_name} (synthetic)."
                url = None
            source_type = "critic" if critic_budget > 0 else "google"
            critic_budget -= 1
            source_cursor = connection.execute(
                """
                INSERT INTO sources (restaurant_id, source_type, url, raw_text, fetched_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (restaurant_id, source_type, url, quote_text),
            )
            connection.execute(
                """
                INSERT INTO mentions (dish_id, source_id, sentiment, confidence, quote)
                VALUES (?, ?, ?, ?, ?)
                """,
                (dish_id, source_cursor.lastrowid, sentiment_label, 0.9, quote_text),
            )

    connection.execute(
        """
        INSERT INTO dish_sentiment_summary (
            dish_id, overall_sentiment, mention_count,
            positive_mentions, negative_mentions, neutral_mentions, average_confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dish_id,
            sentiment["label"],
            dish["mention_count"],
            counts["positive"],
            counts["negative"],
            counts["neutral"],
            0.9,
        ),
    )
