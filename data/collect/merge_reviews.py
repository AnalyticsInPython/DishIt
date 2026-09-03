#!/usr/bin/env python3
"""Fold reviews from one collection file into another.

Earlier runs pulled 10 pages of reviews per restaurant; later ones pulled 3 to save
credits, so the older file holds reviews the newer one never fetched. Reviews carry
Google's own id, so merging deduplicates exactly rather than by guesswork.

Restaurants missing from the target are added whole; restaurants already there keep
their own menu and gain only the reviews they lack.
"""
import argparse
import json
from pathlib import Path


def merge(source_path, target_path, dry_run=False):
    source = json.loads(Path(source_path).read_text())
    target = json.loads(Path(target_path).read_text())
    by_place_id = {r["place_id"]: r for r in target}

    added_reviews = added_restaurants = 0
    for src in source:
        dst = by_place_id.get(src["place_id"])
        if dst is None:
            target.append(src)
            by_place_id[src["place_id"]] = src
            added_restaurants += 1
            added_reviews += len(src.get("reviews") or [])
            print(f"  + {src['name']} (new restaurant, {len(src.get('reviews') or [])} reviews)")
            continue

        seen = {rv.get("id") for rv in dst.get("reviews") or []}
        new = [rv for rv in src.get("reviews") or [] if rv.get("id") not in seen]
        if new:
            dst.setdefault("reviews", []).extend(new)
            added_reviews += len(new)
            print(f"  + {src['name']}: {len(new)} reviews ({len(dst['reviews'])} total)")

        # an older file may hold a menu for a restaurant that later failed to find one
        if not dst.get("menu_items") and src.get("menu_items"):
            dst["menu_items"] = src["menu_items"]
            dst["menu_source_url"] = src.get("menu_source_url")
            print(f"  + {src['name']}: menu of {len(src['menu_items'])} items")

    if not dry_run:
        tmp = Path(target_path).with_suffix(".tmp")
        tmp.write_text(json.dumps(target, indent=2))
        tmp.replace(target_path)

    return added_restaurants, added_reviews, len(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="output/restaurants.json")
    parser.add_argument("--target", default="output/restaurants_full.json")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")
    args = parser.parse_args()

    restaurants, reviews, total = merge(args.source, args.target, args.dry_run)
    verb = "Would add" if args.dry_run else "Added"
    print(f"\n{verb} {reviews} review(s) and {restaurants} restaurant(s); {total} restaurants in target.")


if __name__ == "__main__":
    main()
