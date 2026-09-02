import sqlite3
import unittest

from import_google_reviews import import_reviews


class GoogleReviewImportTests(unittest.TestCase):
    def test_imports_google_fields_into_calculator_schema(self) -> None:
        connection = sqlite3.connect(":memory:")
        restaurant_id = import_reviews(
            connection,
            [
                {
                    "id": "google-review-id",
                    "snippet": "The calamari was delicious.",
                    "link": "https://example.com/review",
                    "isoDate": "2026-07-20T00:44:17.428Z",
                    "rating": 5,
                }
            ],
            "Baylander",
        )

        self.assertEqual(restaurant_id, 1)
        self.assertEqual(
            connection.execute(
                """
                SELECT r.name, s.source_type, s.url, s.raw_text, s.fetched_at,
                       s.external_review_id, s.rating
                FROM sources AS s
                JOIN restaurants AS r ON r.id = s.restaurant_id
                """
            ).fetchone(),
            (
                "Baylander",
                "google",
                "https://example.com/review",
                "The calamari was delicious.",
                "2026-07-20T00:44:17.428Z",
                "google-review-id",
                5,
            ),
        )
