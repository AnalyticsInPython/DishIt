#!/usr/bin/env python3
"""Calculate dish sentiment directly in the canonical DishIt SQLite database.

Example:
    python3 data/calculate/calculate.py data/db/dishit.db
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .dish_sentiment_calculator import (
        AspectSentimentAnalyzer,
        DishReview,
        EntityExtractor,
        analyze_review,
    )
else:
    from dish_sentiment_calculator import (
        AspectSentimentAnalyzer,
        DishReview,
        EntityExtractor,
        analyze_review,
    )


DEFAULT_DATABASE = Path(__file__).resolve().parents[1] / "db" / "dishit.db"


def restaurant_menu_dishes(
    connection: sqlite3.Connection, restaurant_id: int
) -> dict[str, tuple[int, str]]:
    """Map exact case-insensitive menu names to their lowest canonical dish IDs."""
    menu: dict[str, tuple[int, str]] = {}
    for dish_id, name in connection.execute(
        """
        SELECT id, name
        FROM dishes
        WHERE restaurant_id = ?
        ORDER BY id
        """,
        (restaurant_id,),
    ):
        menu.setdefault(str(name).casefold(), (int(dish_id), str(name)))
    return menu


def _mention_rows(
    reviews: list[DishReview], menu: dict[str, tuple[int, str]]
) -> set[tuple[int, str, str]]:
    """Keep only exact menu-item outputs and map final neutral labels to mixed."""
    mentions: set[tuple[int, str, str]] = set()
    for review in reviews:
        matched_dish = menu.get(review.dish.casefold())
        if matched_dish is None:
            continue
        dish_id, _ = matched_dish
        sentiment = "mixed" if review.sentiment == "neutral" else review.sentiment
        for quote in review.contexts:
            mentions.add((dish_id, sentiment, quote))
    return mentions


def _replace_review_mentions(
    connection: sqlite3.Connection,
    review_id: int,
    mentions: set[tuple[int, str, str]],
    extracted_at: str,
) -> int:
    """Synchronize one review's mentions while preserving unchanged rows."""
    desired_keys = {(dish_id, quote) for dish_id, _, quote in mentions}
    existing = connection.execute(
        "SELECT id, dish_id, quote FROM dish_mentions WHERE review_id = ?",
        (review_id,),
    ).fetchall()
    for mention_id, dish_id, quote in existing:
        if (int(dish_id), str(quote)) not in desired_keys:
            connection.execute("DELETE FROM dish_mentions WHERE id = ?", (mention_id,))

    for dish_id, sentiment, quote in mentions:
        connection.execute(
            """
            INSERT INTO dish_mentions (dish_id, review_id, sentiment, quote, extracted_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(dish_id, review_id, quote) DO UPDATE
            SET sentiment = excluded.sentiment
            """,
            (dish_id, review_id, sentiment, quote, extracted_at),
        )
    return len(mentions)


def calculate_reviews(
    connection: sqlite3.Connection,
    max_reviews: int | None = None,
    *,
    nlp=None,
    dish_extractor: EntityExtractor | None = None,
    aspect_sentiment_analyzer: AspectSentimentAnalyzer | None = None,
) -> dict[str, int]:
    """Analyze canonical ``reviews.text`` and write canonical ``dish_mentions``."""
    if max_reviews is not None and max_reviews < 0:
        raise ValueError("max_reviews must be zero or greater.")

    # Covers databases made before the schema gained this index.
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mentions_unique
        ON dish_mentions(dish_id, review_id, quote)
        """
    )
    query = """
        SELECT id, restaurant_id, text
        FROM reviews
        WHERE text IS NOT NULL AND trim(text) != ''
        ORDER BY id
    """
    parameters: tuple[int, ...] = ()
    if max_reviews is not None:
        query += " LIMIT ?"
        parameters = (max_reviews,)
    review_rows = connection.execute(query, parameters).fetchall()

    mentions_written = 0
    analyzed_reviews = 0
    with connection:
        for review_id, restaurant_id, text in review_rows:
            menu = restaurant_menu_dishes(connection, int(restaurant_id))
            # A restaurant without menu dishes cannot yield a canonical mention.
            if not menu:
                _replace_review_mentions(connection, int(review_id), set(), "")
                continue
            results = analyze_review(
                str(text),
                menu_items=(name for _, name in menu.values()),
                nlp=nlp,
                dish_extractor=dish_extractor,
                aspect_sentiment_analyzer=aspect_sentiment_analyzer,
            )
            mentions_written += _replace_review_mentions(
                connection,
                int(review_id),
                _mention_rows(results, menu),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            analyzed_reviews += 1
    return {"reviews": analyzed_reviews, "mentions": mentions_written}


def calculate_database(
    database: Path,
    max_reviews: int | None = None,
) -> dict[str, int]:
    """Open a canonical database and calculate its review dish mentions."""
    if not database.is_file():
        raise FileNotFoundError(f"Canonical SQLite database not found: {database}")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        return calculate_reviews(connection, max_reviews=max_reviews)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate dish_mentions in a canonical DishIt SQLite database."
    )
    parser.add_argument(
        "database",
        type=Path,
        nargs="?",
        default=DEFAULT_DATABASE,
        help=f"Canonical SQLite database (default: {DEFAULT_DATABASE}).",
    )
    parser.add_argument(
        "--max-reviews",
        type=int,
        help="Analyze at most this many non-blank reviews (useful for test runs).",
    )
    args = parser.parse_args()
    try:
        counts = calculate_database(args.database, args.max_reviews)
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))
    print(
        f"Calculated {counts['mentions']} dish mentions from "
        f"{counts['reviews']} reviews in {args.database}."
    )


if __name__ == "__main__":
    main()
