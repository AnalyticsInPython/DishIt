"""Integration tests for the data collection-to-calculation handoff."""

import sqlite3
import unittest

from data.calculate.calculate import import_collected_restaurants


class CollectionIntegrationTests(unittest.TestCase):
    def test_imports_collector_output_with_menu_and_review_fields(self) -> None:
        restaurants = [
            {
                "name": "Baylander",
                "address": "West Harlem Piers",
                "menu_source_url": "https://example.com/menu",
                "menu_items": [
                    {"name": "Fish Tacos", "price": "$14", "description": None},
                    {"name": "Lobster Roll", "price": "$20", "description": None},
                ],
                "reviews": [
                    {
                        "snippet": "The fish tacos were delicious.",
                        "link": "https://example.com/review",
                        "isoDate": "2026-09-03T00:00:00Z",
                    },
                    {"snippet": "   "},
                ],
                "raw_place": {"type": "restaurant"},
            }
        ]
        connection = sqlite3.connect(":memory:")

        import_collected_restaurants(connection, restaurants)

        self.assertEqual(
            connection.execute(
                "SELECT name, neighborhood, cuisine, source_urls FROM restaurants"
            ).fetchall(),
            [
                (
                    "Baylander",
                    "West Harlem Piers",
                    "restaurant",
                    "https://example.com/menu",
                )
            ],
        )
        self.assertEqual(
            connection.execute("SELECT canonical_name FROM dishes ORDER BY id").fetchall(),
            [("Fish Tacos",), ("Lobster Roll",)],
        )
        self.assertEqual(
            connection.execute(
                "SELECT source_type, url, raw_text, fetched_at FROM sources"
            ).fetchall(),
            [
                (
                    "google",
                    "https://example.com/review",
                    "The fish tacos were delicious.",
                    "2026-09-03T00:00:00Z",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
