"""Basic executable tests for dish_sentiment_calculator.py.

Run after installing requirements and the spaCy English model:
    python3 -m unittest -v
"""

import unittest

from dish_sentiment_calculator import analyze_review, load_nlp


class DishSentimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.nlp = load_nlp()

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
                "label": "dish",
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
            return [{"text": "duck", "label": "dish", "start": start, "end": start + 4}]

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
