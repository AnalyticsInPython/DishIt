#!/usr/bin/env python3
"""Load the collected restaurant JSON into SQLite. Safe to re-run: restaurants key on
place_id and reviews on Google's review id, so a later run with backfilled menus
updates in place instead of duplicating."""
import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_JSON = HERE.parent / "collect" / "output" / "restaurants_full.json"
DEFAULT_DB = HERE / "dishit.db"

PRICE_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_price(price_raw):
    """First number in the string: '$18.00' -> 18.0, 'M: $2.5, L: $3.25' -> 2.5.
    Deliberately lossy - price_raw keeps the original wording."""
    if not price_raw:
        return None
    match = PRICE_RE.search(str(price_raw))
    return float(match.group()) if match else None


def load(json_path, db_path, menus_only=True, rebuild=False):
    records = json.loads(Path(json_path).read_text())
    if menus_only:
        records = [r for r in records if r.get("menu_items")]

    db_path = Path(db_path)
    if rebuild:
        # inserts and updates alone can't remove a restaurant that has since been
        # dropped from the JSON, so a clean rebuild is the only way to match it exactly
        db_path.unlink(missing_ok=True)
    fresh = not db_path.exists()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((HERE / "schema.sql").read_text())

    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = {"restaurants": 0, "dishes": 0, "reviews": 0, "media": 0, "types": 0}

    for rec in records:
        raw = rec.get("raw_place", {})
        cur = conn.execute(
            """INSERT INTO restaurants (place_id, cid, name, address, latitude, longitude,
                   distance_m, category, website, phone, google_rating, google_rating_count,
                   price_level, menu_source_url, collected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(place_id) DO UPDATE SET
                   name=excluded.name, address=excluded.address, latitude=excluded.latitude,
                   longitude=excluded.longitude, distance_m=excluded.distance_m,
                   category=excluded.category, website=excluded.website, phone=excluded.phone,
                   google_rating=excluded.google_rating,
                   google_rating_count=excluded.google_rating_count,
                   price_level=excluded.price_level,
                   menu_source_url=excluded.menu_source_url,
                   collected_at=excluded.collected_at
               RETURNING id""",
            (
                rec["place_id"], rec.get("cid"), rec["name"], rec.get("address"),
                raw.get("latitude"), raw.get("longitude"), rec.get("distance_m"),
                raw.get("type"), raw.get("website"), raw.get("phoneNumber"),
                raw.get("rating"), raw.get("ratingCount"), raw.get("priceLevel"),
                rec.get("menu_source_url"), collected_at,
            ),
        )
        restaurant_id = cur.fetchone()[0]
        counts["restaurants"] += 1

        for type_name in raw.get("types") or []:
            conn.execute(
                "INSERT OR IGNORE INTO restaurant_types (restaurant_id, type) VALUES (?,?)",
                (restaurant_id, type_name),
            )
            counts["types"] += 1

        # dishes have no stable external id, so replace this restaurant's set wholesale
        conn.execute("DELETE FROM dishes WHERE restaurant_id = ?", (restaurant_id,))
        for item in rec.get("menu_items") or []:
            conn.execute(
                "INSERT INTO dishes (restaurant_id, name, description, price_raw, price_min) VALUES (?,?,?,?,?)",
                (restaurant_id, item.get("name"), item.get("description"),
                 item.get("price"), parse_price(item.get("price"))),
            )
            counts["dishes"] += 1

        for review in rec.get("reviews") or []:
            response = review.get("response") or {}
            cur = conn.execute(
                """INSERT INTO reviews (restaurant_id, external_id, rating, text, published_at,
                       relative_date, likes, url, author_name, owner_response)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(external_id) DO UPDATE SET
                       rating=excluded.rating, text=excluded.text, likes=excluded.likes,
                       owner_response=excluded.owner_response
                   RETURNING id""",
                (
                    restaurant_id, review["id"], review.get("rating"), review.get("snippet"),
                    review.get("isoDate"), review.get("date"), review.get("likes"),
                    review.get("link"), (review.get("user") or {}).get("name"),
                    response.get("snippet"),
                ),
            )
            review_id = cur.fetchone()[0]
            counts["reviews"] += 1

            conn.execute("DELETE FROM review_media WHERE review_id = ?", (review_id,))
            for media in review.get("media") or []:
                if media.get("imageUrl"):
                    conn.execute(
                        "INSERT INTO review_media (review_id, image_url, caption) VALUES (?,?,?)",
                        (review_id, media["imageUrl"], media.get("caption")),
                    )
                    counts["media"] += 1

    conn.commit()
    conn.close()
    return counts, fresh


def main():
    parser = argparse.ArgumentParser(description="Load collected restaurant JSON into SQLite")
    parser.add_argument("--json", default=str(DEFAULT_JSON))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--include-menuless",
        action="store_true",
        help="Also load restaurants that have reviews but no menu items",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete the database first, so restaurants dropped from the JSON disappear too",
    )
    args = parser.parse_args()

    counts, fresh = load(
        args.json, args.db, menus_only=not args.include_menuless, rebuild=args.rebuild
    )
    print(f"{'Created' if fresh else 'Updated'} {args.db}")
    for table, n in counts.items():
        print(f"  {table:<12} {n}")


if __name__ == "__main__":
    main()
