#!/usr/bin/env python3
"""Run dish sentiment calculation on data collected by data/collect/collect.py.

Example:
    python data/calculate/calculate.py \
        data/collect/output/restaurants.json \
        data/calculate/output/dishit.sqlite3

This creates the calculator input database and the following manual-review files:
    data/calculate/output/dishit_review.sqlite3
    data/calculate/output/dishit_review.json
    data/calculate/output/dishit_review_by_dish.sqlite3
    data/calculate/output/dishit_review_by_dish.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

if __package__:
    from .dish_sentiment_calculator import (
        REVIEW_OUTPUT_SCHEMA,
        build_manual_review_outputs,
    )
else:
    from dish_sentiment_calculator import REVIEW_OUTPUT_SCHEMA, build_manual_review_outputs


def review_text(review: dict[str, Any]) -> str | None:
    """Get the Serper review text without accepting blank review records."""
    text = review.get("snippet")
    return text.strip() if isinstance(text, str) and text.strip() else None


def menu_names(menu_items: object) -> list[str]:
    """Extract valid dish names from the collector's structured menu output."""
    if not isinstance(menu_items, list):
        return []
    return [
        item["name"].strip()
        for item in menu_items
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item["name"].strip()
    ]


def import_collected_restaurants(
    connection: sqlite3.Connection, restaurants: list[dict[str, Any]]
) -> None:
    """Map data/collect's restaurant JSON into the calculator's SQLite contract."""
    connection.executescript(REVIEW_OUTPUT_SCHEMA)
    with connection:
        for restaurant in restaurants:
            name = restaurant.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Every collected restaurant must have a non-empty name.")
            raw_place = restaurant.get("raw_place")
            raw_place = raw_place if isinstance(raw_place, dict) else {}
            cursor = connection.execute(
                """
                INSERT INTO restaurants (name, neighborhood, cuisine, source_urls)
                VALUES (?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    restaurant.get("address"),
                    raw_place.get("type"),
                    restaurant.get("menu_source_url"),
                ),
            )
            restaurant_id = int(cursor.lastrowid)
            for menu_name in menu_names(restaurant.get("menu_items")):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO dishes (restaurant_id, canonical_name)
                    VALUES (?, ?)
                    """,
                    (restaurant_id, menu_name),
                )

            reviews = restaurant.get("reviews")
            if not isinstance(reviews, list):
                continue
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                text = review_text(review)
                if text is None:
                    continue
                connection.execute(
                    """
                    INSERT INTO sources (
                        restaurant_id, source_type, url, raw_text, fetched_at
                    )
                    VALUES (?, 'google', ?, ?, ?)
                    """,
                    (
                        restaurant_id,
                        review.get("link"),
                        text,
                        review.get("isoDate"),
                    ),
                )


def build_calculation_outputs(
    collection_json: Path, output_database: Path
) -> tuple[Path, Path, Path, Path]:
    """Import one collection output and produce review-first and dish-first exports."""
    if output_database.exists():
        raise FileExistsError(f"{output_database} already exists; choose a new path.")
    payload = json.loads(collection_json.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("Collection JSON must be a list of restaurant objects.")

    output_database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output_database) as connection:
        import_collected_restaurants(connection, payload)

    review_database = output_database.with_name(f"{output_database.stem}_review.sqlite3")
    review_json = output_database.with_name(f"{output_database.stem}_review.json")
    dish_database, dish_json = build_manual_review_outputs(
        output_database, review_database, review_json
    )
    return output_database, review_database, dish_database, dish_json


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert collected restaurants JSON and calculate dish sentiment."
    )
    parser.add_argument("collection_json", type=Path, help="data/collect restaurants JSON.")
    parser.add_argument("output_database", type=Path, help="New calculator SQLite file.")
    args = parser.parse_args()
    database, review_database, dish_database, dish_json = build_calculation_outputs(
        args.collection_json, args.output_database
    )
    print(
        f"Wrote {database}, {review_database}, {dish_database}, and {dish_json}."
    )
