"""Integration tests for canonical database calculation without model downloads."""

import sqlite3
import unittest
from pathlib import Path

import spacy

from data.calculate.calculate import (
    _mention_rows,
    calculate_reviews,
    restaurant_menu_dishes,
)
from data.calculate.dish_sentiment_calculator import DishReview


class CanonicalCalculationTests(unittest.TestCase):
    @staticmethod
    def dish_extractor(text: str) -> list[dict[str, object]]:
        return [
            {
                "text": dish,
                "label": "specific dish or menu item",
                "start": text.lower().index(dish),
                "end": text.lower().index(dish) + len(dish),
            }
            for dish in ("crispy duck", "made up entree")
            if dish in text.lower()
        ]

    @staticmethod
    def aspect_sentiment_analyzer(
        _sentence: str, aspect: str
    ) -> list[dict[str, object]]:
        return [{
            "label": "positive" if aspect.lower() == "crispy duck" else "negative",
            "score": 0.99,
        }]

    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.nlp = spacy.blank("en")
        self.nlp.add_pipe("sentencizer")
        schema = (
            Path(__file__).resolve().parents[2] / "db" / "schema.sql"
        ).read_text(encoding="utf-8")
        self.connection.executescript(schema)
        self.connection.executescript(
            """
            INSERT INTO restaurants (id, place_id, name, collected_at)
            VALUES
                (1, 'one', 'One', '2026-01-01T00:00:00Z'),
                (2, 'two', 'Two', '2026-01-01T00:00:00Z');
            INSERT INTO dishes (id, restaurant_id, name)
            VALUES
                (3, 1, 'Crispy Duck'),
                (8, 1, 'crispy duck'),
                (9, 2, 'Crispy Duck');
            INSERT INTO reviews (id, restaurant_id, external_id, text)
            VALUES
                (4, 1, 'review-1',
                 'The crispy duck was excellent. The made up entree was awful.'),
                (5, 1, 'review-2', 'Second review.'),
                (6, 2, 'review-3', NULL);
            """
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_writes_only_exact_menu_matches_with_lowest_duplicate_id(self) -> None:
        counts = calculate_reviews(
            self.connection,
            max_reviews=1,
            nlp=self.nlp,
            dish_extractor=self.dish_extractor,
            aspect_sentiment_analyzer=self.aspect_sentiment_analyzer,
        )

        self.assertEqual(
            counts,
            {"reviews": 1, "mentions": 1},
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT dish_id, review_id, sentiment, quote FROM dish_mentions"
            ).fetchall(),
            [
                (
                    3,
                    4,
                    "positive",
                    "The crispy duck was excellent.",
                )
            ],
        )

    def test_rerun_is_idempotent_and_limit_skips_later_reviews(self) -> None:
        calculate_reviews(
            self.connection,
            max_reviews=1,
            nlp=self.nlp,
            dish_extractor=self.dish_extractor,
            aspect_sentiment_analyzer=self.aspect_sentiment_analyzer,
        )
        original_id = self.connection.execute(
            "SELECT id FROM dish_mentions"
        ).fetchone()[0]
        counts = calculate_reviews(
            self.connection,
            max_reviews=1,
            nlp=self.nlp,
            dish_extractor=self.dish_extractor,
            aspect_sentiment_analyzer=self.aspect_sentiment_analyzer,
        )

        self.assertEqual(counts, {"reviews": 1, "mentions": 1})
        self.assertEqual(
            self.connection.execute(
                "SELECT id, COUNT(*) FROM dish_mentions"
            ).fetchone(),
            (original_id, 1),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT id FROM dish_mentions WHERE review_id = 5"
            ).fetchall(),
            [],
        )

    def test_skips_menu_names_too_short_to_be_a_dish(self) -> None:
        # A photographed "TEA" was once read as "A", matching the article everywhere.
        self.connection.execute(
            "INSERT INTO dishes (id, restaurant_id, name) VALUES (10, 1, 'A')"
        )

        self.assertNotIn("a", restaurant_menu_dishes(self.connection, 1))
        self.assertIn("crispy duck", restaurant_menu_dishes(self.connection, 1))

    def test_maps_neutral_model_results_to_mixed_for_canonical_storage(self) -> None:
        mentions = _mention_rows(
            [
                DishReview(
                    dish="crispy duck",
                    sentiment="neutral",
                    score=0.0,
                    mentions=1,
                    contexts=("The crispy duck was served.",),
                )
            ],
            {"crispy duck": (3, "Crispy Duck")},
        )

        self.assertEqual(
            mentions, {(3, "mixed", "The crispy duck was served.")}
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
