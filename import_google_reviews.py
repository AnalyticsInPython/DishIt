"""Convert Google-review JSON exports to the SQLite tables used by DishIt.

Example:
    python import_google_reviews.py \
        "Data Files for Testing/Baylander_Reviews.json" \
        "Data Files for Testing/Baylander_Reviews.sqlite3" \
        --restaurant-name Baylander
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    neighborhood TEXT,
    cuisine TEXT,
    source_urls TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
    source_type TEXT NOT NULL,
    url TEXT,
    raw_text TEXT NOT NULL,
    fetched_at TEXT,
    external_review_id TEXT NOT NULL UNIQUE,
    rating INTEGER
);

CREATE TABLE IF NOT EXISTS dishes (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
    canonical_name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (restaurant_id, canonical_name)
);

CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY,
    dish_id INTEGER NOT NULL REFERENCES dishes(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    sentiment TEXT NOT NULL,
    quote TEXT NOT NULL,
    extracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def load_reviews(json_path: Path) -> list[dict[str, Any]]:
    """Load and validate the review array from a Google review JSON export."""
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    reviews = payload.get("reviews") if isinstance(payload, dict) else None
    if not isinstance(reviews, list):
        raise ValueError("The JSON file must contain a top-level reviews array.")
    if not all(isinstance(review, dict) for review in reviews):
        raise ValueError("Every reviews entry must be a JSON object.")
    return reviews


def import_reviews(
    connection: sqlite3.Connection,
    reviews: list[dict[str, Any]],
    restaurant_name: str,
) -> int:
    """Store Google-review fields using the calculator's SQLite field names."""
    if not restaurant_name.strip():
        raise ValueError("restaurant_name cannot be empty.")
    connection.executescript(SCHEMA)
    with connection:
        cursor = connection.execute(
            "INSERT INTO restaurants (name) VALUES (?)", (restaurant_name.strip(),)
        )
        restaurant_id = int(cursor.lastrowid)
        for review in reviews:
            review_id = review.get("id")
            review_text = review.get("snippet")
            if not isinstance(review_id, str) or not review_id:
                raise ValueError("Each review must have a non-empty id.")
            if not isinstance(review_text, str) or not review_text.strip():
                raise ValueError(f"Review {review_id} must have non-empty snippet text.")
            connection.execute(
                """
                INSERT INTO sources (
                    restaurant_id, source_type, url, raw_text, fetched_at,
                    external_review_id, rating
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    restaurant_id,
                    "google",
                    review.get("link"),
                    review_text.strip(),
                    review.get("isoDate"),
                    review_id,
                    review.get("rating"),
                ),
            )
    return restaurant_id


def import_review_file(
    json_path: Path, database_path: Path, restaurant_name: str
) -> int:
    """Create a database from one Google-review JSON export."""
    if database_path.exists():
        raise FileExistsError(
            f"{database_path} already exists; choose a new output path."
        )
    with sqlite3.connect(database_path) as connection:
        return import_reviews(connection, load_reviews(json_path), restaurant_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Import a Google-review JSON export into DishIt SQLite tables."
    )
    parser.add_argument("json_file", type=Path, help="Input Google-review JSON file.")
    parser.add_argument("database", type=Path, help="New output SQLite database path.")
    parser.add_argument("--restaurant-name", required=True, help="Restaurant name.")
    arguments = parser.parse_args()
    restaurant_id = import_review_file(
        arguments.json_file, arguments.database, arguments.restaurant_name
    )
    print(f"Imported {arguments.json_file} as restaurant id {restaurant_id}.")
