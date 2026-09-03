import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def create_canonical_fixture(database: Path) -> None:
    """Create a small canonical pipeline database with menu and review evidence."""
    with sqlite3.connect(database) as connection:
        connection.executescript((ROOT / "data" / "db" / "schema.sql").read_text())
        connection.executemany(
            """
            INSERT INTO restaurants (
                id, place_id, name, address, latitude, longitude, category, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "place-1",
                    "Morningside Pasta",
                    "120 Broadway, New York, NY",
                    40.8075,
                    -73.9626,
                    "Italian restaurant",
                    "2026-09-03T00:00:00Z",
                ),
                (
                    2,
                    "place-2",
                    "Han & Sons",
                    "114th Street, New York, NY",
                    40.805,
                    -73.96,
                    "Korean restaurant",
                    "2026-09-03T00:00:00Z",
                ),
                (
                    3,
                    "place-3",
                    "Riverside Pasta",
                    "110 Riverside Drive, New York, NY",
                    40.801,
                    -73.97,
                    "Italian restaurant",
                    "2026-09-03T00:00:00Z",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO dishes (id, restaurant_id, name, description) VALUES (?, ?, ?, ?)",
            [
                (1, 1, "Cacio e Pepe", "Pecorino and black pepper"),
                (2, 3, "cacio E pepe", "A second menu version"),
                (3, 2, "Bibimbap", "Rice bowl"),
                (4, 1, "Garlic Knots", "Baked garlic knots"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO reviews (id, restaurant_id, external_id, text, url)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "review-1", "Excellent pasta.", "https://google.example/review-1"),
                (2, 1, "review-2", "Pepper was too intense.", None),
                (3, 1, "review-3", "A balanced experience.", "https://google.example/review-3"),
                (4, 3, "review-4", "Wonderful pasta.", "https://google.example/review-4"),
                (5, 2, "review-5", "Delicious bibimbap.", "https://google.example/review-5"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO dish_mentions (dish_id, review_id, sentiment, quote, extracted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "positive", "The pepper sauce was excellent.", "2026-09-03T00:00:00Z"),
                (1, 1, "positive", "I would order it again.", "2026-09-03T00:00:00Z"),
                (1, 2, "negative", "The pepper overwhelmed the pasta.", "2026-09-03T00:00:00Z"),
                (1, 2, "negative", "It was too salty.", "2026-09-03T00:00:00Z"),
                (1, 3, "mixed", "Great cheese but an uneven sauce.", "2026-09-03T00:00:00Z"),
                (2, 4, "positive", "The cacio e pepe was exceptional.", "2026-09-03T00:00:00Z"),
                (2, 4, "positive", "Perfectly cooked pasta.", "2026-09-03T00:00:00Z"),
                (2, 4, "positive", "Rich pecorino flavor.", "2026-09-03T00:00:00Z"),
                (2, 4, "positive", "A neighborhood favorite.", "2026-09-03T00:00:00Z"),
                (2, 4, "positive", "I recommend this dish.", "2026-09-03T00:00:00Z"),
                (3, 5, "positive", "Fresh and flavorful bibimbap.", "2026-09-03T00:00:00Z"),
            ],
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient connected to a fresh canonical-schema database."""
    database = tmp_path / "canonical-dishit.db"
    create_canonical_fixture(database)
    monkeypatch.setenv(db.DATABASE_ENV_VAR, str(database))

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
