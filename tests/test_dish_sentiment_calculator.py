"""Unit and SQLite integration tests for the local model calculator."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import spacy

from data.calculate.dish_sentiment_calculator import (
    analyze_all_sources,
    analyze_review,
    analyze_source,
    build_manual_review_outputs,
)


class DishSentimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.nlp = spacy.blank("en")
        cls.nlp.add_pipe("sentencizer")

    @staticmethod
    def dish_extractor(text: str) -> list[dict[str, object]]:
        dishes = (
            "mushroom risotto",
            "fish tacos",
            "panna cotta",
            "steak",
            "pasta",
            "dumplings",
        )
        return [
            {
                "text": dish,
                "label": "specific dish or menu item",
                "start": text.lower().index(dish),
                "end": text.lower().index(dish) + len(dish),
            }
            for dish in dishes
            if dish in text.lower()
        ]

    @staticmethod
    def aspect_sentiment_analyzer(
        sentence: str, aspect: str
    ) -> list[dict[str, object]]:
        sentiment_by_aspect = {
            "mushroom risotto": "positive",
            "fish tacos": "negative",
            "panna cotta": "negative",
            "steak": "positive",
            "pasta": "negative",
            "dumplings": "neutral",
            "sea urchin toast": "positive",
            "crispy duck": "positive",
        }
        return [{"label": sentiment_by_aspect[aspect.lower()], "score": 0.99}]

    def analyze(
        self, text: str, menu_items: tuple[str, ...] = ()
    ) -> dict[str, object]:
        results = analyze_review(
            text,
            menu_items=menu_items,
            nlp=self.nlp,
            dish_extractor=self.dish_extractor,
            aspect_sentiment_analyzer=self.aspect_sentiment_analyzer,
        )
        return {result.dish: result for result in results}

    def test_extracts_positive_dish_from_review_only(self) -> None:
        results = self.analyze(
            "The mushroom risotto was creamy, flavorful, and excellent."
        )

        risotto = results["mushroom risotto"]
        self.assertEqual(risotto.sentiment, "positive")
        self.assertGreater(risotto.score, 0)

    def test_extracts_negative_dish_from_review_only(self) -> None:
        results = self.analyze("Skip the fish tacos: they were cold and bland.")

        tacos = results["fish tacos"]
        self.assertEqual(tacos.sentiment, "negative")
        self.assertLess(tacos.score, 0)

    def test_absa_assigns_different_sentiment_per_dish(self) -> None:
        results = self.analyze("The steak was tender, but the pasta was bland.")

        self.assertEqual(results["steak"].sentiment, "positive")
        self.assertEqual(results["pasta"].sentiment, "negative")
        self.assertEqual(
            results["steak"].contexts,
            ("The steak was tender, but the pasta was bland.",),
        )

    def test_optional_menu_items_supplement_review_extraction(self) -> None:
        results = self.analyze(
            "The sea urchin toast was excellent.",
            menu_items=("sea urchin toast",),
        )

        self.assertEqual(results["sea urchin toast"].sentiment, "positive")

    def test_menu_item_replaces_an_overlapping_generic_extraction(self) -> None:
        review = "The crispy duck was excellent."

        def generic_extractor(_: str) -> list[dict[str, object]]:
            start = review.index("duck")
            return [
                {
                    "text": "duck",
                    "label": "specific dish or menu item",
                    "start": start,
                    "end": start + 4,
                }
            ]

        results = analyze_review(
            review,
            menu_items=("crispy duck",),
            nlp=self.nlp,
            dish_extractor=generic_extractor,
            aspect_sentiment_analyzer=self.aspect_sentiment_analyzer,
        )

        self.assertEqual([result.dish for result in results], ["crispy duck"])
        self.assertEqual(results[0].sentiment, "positive")

    def test_marks_dish_neutral_without_a_sentiment_opinion(self) -> None:
        results = self.analyze("We ordered the dumplings for the table.")

        self.assertEqual(results["dumplings"].sentiment, "neutral")
        self.assertEqual(results["dumplings"].score, 0)

    def test_analyze_source_uses_stored_menu_and_replaces_mentions(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE restaurants (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY,
                restaurant_id INTEGER NOT NULL,
                raw_text TEXT NOT NULL
            );
            CREATE TABLE dishes (
                id INTEGER PRIMARY KEY,
                restaurant_id INTEGER NOT NULL,
                canonical_name TEXT NOT NULL COLLATE NOCASE,
                UNIQUE (restaurant_id, canonical_name)
            );
            CREATE TABLE mentions (
                id INTEGER PRIMARY KEY,
                dish_id INTEGER NOT NULL,
                source_id INTEGER NOT NULL,
                sentiment TEXT NOT NULL,
                confidence REAL NOT NULL,
                quote TEXT NOT NULL,
                extracted_at TEXT
            );
            INSERT INTO restaurants (id, name) VALUES (1, 'Test');
            INSERT INTO sources (id, restaurant_id, raw_text)
            VALUES (1, 1, 'The crispy duck was excellent.');
            INSERT INTO dishes (id, restaurant_id, canonical_name)
            VALUES (1, 1, 'crispy duck');
            INSERT INTO mentions (dish_id, source_id, sentiment, confidence, quote)
            VALUES (1, 1, 'neutral', 0.5, 'outdated result');
            """
        )
        review = "The crispy duck was excellent."

        def generic_extractor(_: str) -> list[dict[str, object]]:
            start = review.index("duck")
            return [
                {
                    "text": "duck",
                    "label": "specific dish or menu item",
                    "start": start,
                    "end": start + 4,
                }
            ]

        reviews = analyze_source(
            connection,
            1,
            nlp=self.nlp,
            dish_extractor=generic_extractor,
            aspect_sentiment_analyzer=self.aspect_sentiment_analyzer,
        )

        self.assertEqual([review.dish for review in reviews], ["crispy duck"])
        mentions = connection.execute(
            """
            SELECT d.canonical_name, m.sentiment, m.quote
            FROM mentions AS m
            JOIN dishes AS d ON d.id = m.dish_id
            WHERE m.source_id = 1
            """
        ).fetchall()
        self.assertEqual(
            mentions,
            [("crispy duck", "positive", "The crispy duck was excellent.")],
        )

    def test_analyze_all_sources_persists_each_source(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY, restaurant_id INTEGER NOT NULL, raw_text TEXT NOT NULL
            );
            CREATE TABLE dishes (
                id INTEGER PRIMARY KEY, restaurant_id INTEGER NOT NULL,
                canonical_name TEXT NOT NULL COLLATE NOCASE,
                UNIQUE (restaurant_id, canonical_name)
            );
            CREATE TABLE mentions (
                id INTEGER PRIMARY KEY, dish_id INTEGER NOT NULL, source_id INTEGER NOT NULL,
                sentiment TEXT NOT NULL, confidence REAL NOT NULL,
                quote TEXT NOT NULL, extracted_at TEXT
            );
            INSERT INTO sources (id, restaurant_id, raw_text)
            VALUES
                (1, 1, 'The steak was tender.'),
                (2, 1, 'The pasta was bland.');
            """
        )

        results = analyze_all_sources(
            connection,
            nlp=self.nlp,
            dish_extractor=self.dish_extractor,
            aspect_sentiment_analyzer=self.aspect_sentiment_analyzer,
        )

        self.assertEqual(set(results), {1, 2})
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM mentions").fetchone()[0], 2
        )

    def test_manual_review_exports_are_clean_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_database = Path(directory) / "input.sqlite3"
            output_database = Path(directory) / "review.sqlite3"
            output_json = Path(directory) / "review.json"
            dish_database = Path(directory) / "review_by_dish.sqlite3"
            dish_json = Path(directory) / "review_by_dish.json"
            with sqlite3.connect(input_database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE restaurants (
                        id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                        neighborhood TEXT, cuisine TEXT, source_urls TEXT
                    );
                    CREATE TABLE sources (
                        id INTEGER PRIMARY KEY, restaurant_id INTEGER NOT NULL,
                        source_type TEXT, url TEXT, raw_text TEXT NOT NULL, fetched_at TEXT
                    );
                    CREATE TABLE dishes (
                        id INTEGER PRIMARY KEY, restaurant_id INTEGER NOT NULL,
                        canonical_name TEXT NOT NULL COLLATE NOCASE,
                        UNIQUE (restaurant_id, canonical_name)
                    );
                    CREATE TABLE mentions (
                        id INTEGER PRIMARY KEY, dish_id INTEGER NOT NULL, source_id INTEGER NOT NULL,
                        sentiment TEXT NOT NULL, confidence REAL NOT NULL,
                        quote TEXT NOT NULL, extracted_at TEXT
                    );
                    INSERT INTO restaurants (id, name) VALUES (1, 'Test');
                    INSERT INTO sources (id, restaurant_id, raw_text)
                    VALUES (1, 1, 'The crispy duck was excellent.');
                    INSERT INTO dishes (id, restaurant_id, canonical_name)
                    VALUES (1, 1, 'crispy duck');
                    """
                )

            def generic_extractor(_: str) -> list[dict[str, object]]:
                return [
                    {
                        "label": "specific dish or menu item",
                        "start": 11,
                        "end": 15,
                    }
                ]

            written_dish_database, written_dish_json = build_manual_review_outputs(
                input_database,
                output_database,
                output_json,
                dish_database,
                dish_json,
                nlp=self.nlp,
                dish_extractor=generic_extractor,
                aspect_sentiment_analyzer=self.aspect_sentiment_analyzer,
            )

            self.assertTrue(output_json.exists())
            self.assertEqual(written_dish_database, dish_database)
            self.assertEqual(written_dish_json, dish_json)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["reviews"][0]["source_id"], 1)
            self.assertEqual(
                payload["reviews"][0]["dish_sentiment"][0]["dish"], "crispy duck"
            )
            self.assertEqual(payload["reviews"][0]["dish_sentiment"][0]["dish_id"], 1)
            dish_payload = json.loads(dish_json.read_text(encoding="utf-8"))
            self.assertEqual(dish_payload["dishes"][0]["dish_id"], 1)
            self.assertEqual(
                dish_payload["dishes"][0]["sentiment_summary"]["overall_sentiment"],
                "positive",
            )
            with sqlite3.connect(output_database) as connection:
                self.assertEqual(
                    connection.execute("SELECT canonical_name FROM dishes").fetchall(),
                    [("crispy duck",)],
                )
                self.assertEqual(
                    connection.execute("SELECT sentiment FROM mentions").fetchall(),
                    [("positive",)],
                )
            with sqlite3.connect(dish_database) as connection:
                self.assertEqual(
                    connection.execute(
                        """
                        SELECT dish_id, overall_sentiment, positive_mentions
                        FROM dish_sentiment_summary
                        """
                    ).fetchall(),
                    [(1, "positive", 1)],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
