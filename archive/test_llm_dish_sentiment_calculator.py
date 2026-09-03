"""Archived tests for the Anthropic-backed dish sentiment calculator.

Run:
    python3 -m unittest -v
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from llm_dish_sentiment_calculator import analyze_review, parse_response


class FakeClient:
    class messages:
        last_request: dict[str, object] | None = None

        @classmethod
        def create(cls, **kwargs: object) -> object:
            cls.last_request = kwargs
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text="""{
                            "dishes": [
                                {
                                    "dish": "Crispy Duck",
                                    "sentiment": "positive",
                                    "confidence": 0.97,
                                    "mentions": 1,
                                    "evidence": ["The crispy duck was wonderfully juicy."]
                                },
                                {
                                    "dish": "Truffle Fries",
                                    "sentiment": "negative",
                                    "confidence": 0.94,
                                    "mentions": 1,
                                    "evidence": ["The truffle fries were cold and greasy."]
                                }
                            ]
                        }"""
                    )
                ]
            )


class LlmDishSentimentTests(unittest.TestCase):
    def test_extracts_dish_sentiment_from_review(self) -> None:
        client = FakeClient()
        reviews = analyze_review(
            "The crispy duck was wonderfully juicy. "
            "The truffle fries were cold and greasy.",
            client=client,
        )

        self.assertEqual([review.dish for review in reviews], ["crispy duck", "truffle fries"])
        self.assertEqual(reviews[0].sentiment, "positive")
        self.assertEqual(reviews[1].sentiment, "negative")
        self.assertEqual(reviews[0].evidence, ("The crispy duck was wonderfully juicy.",))

    def test_requests_deterministic_json_output(self) -> None:
        client = FakeClient()
        analyze_review("The crispy duck was wonderfully juicy.", client=client)

        request = client.messages.last_request
        self.assertIsNotNone(request)
        self.assertEqual(request["temperature"], 0)
        self.assertIn("JSON only", request["system"])

    def test_rejects_malformed_llm_json(self) -> None:
        response = SimpleNamespace(content=[SimpleNamespace(text='{"not_dishes": []}')])

        with self.assertRaisesRegex(ValueError, "dishes list"):
            parse_response(response)

    def test_rejects_empty_review(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            analyze_review("   ", client=FakeClient())


if __name__ == "__main__":
    unittest.main(verbosity=2)
