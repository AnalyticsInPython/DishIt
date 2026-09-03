#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from geo import grid_points, haversine_distance_m
from groq_client import GroqCallError
from menu_text import extract_menu_items_from_text
from menu_vision import extract_menu_items
from serper_client import SerperClient, normalize_place_id
from website_menu import fetch_page_text, find_website_menu_text

# Columbia University, Broadway & 116th St, New York, NY 10027
ORIGIN_LAT = 40.8075
ORIGIN_LNG = -73.9626
RADIUS_M = 1000
# One "restaurants" query misses anything Google files under a different category,
# so the radius gets swept with several category terms.
PLACES_QUERIES = ("restaurants", "cafe", "pizza", "deli", "bakery", "bar")
MENU_IMAGE_CANDIDATES = 3
MENU_PAGE_CANDIDATES = 3
PLACES_CACHE = "_places_cache.json"

OUTPUT_DIR = Path(__file__).parent / "output"


def find_nearby_restaurants(client, lat, lng, radius_m, spacing_m, pages):
    anchors = grid_points(lat, lng, radius_m, spacing_m)
    total_searches = len(anchors) * len(PLACES_QUERIES)
    print(f"Sweeping {len(anchors)} anchor point(s) x {len(PLACES_QUERIES)} categories ({total_searches} searches)...")

    by_id = {}
    done = 0
    for anchor_lat, anchor_lng in anchors:
        for query in PLACES_QUERIES:
            done += 1
            try:
                places = client.search_places(query, anchor_lat, anchor_lng, max_pages=pages)
            except RuntimeError as e:
                # one bad anchor/category shouldn't discard the rest of the sweep
                print(f"    search {done}/{total_searches} ({query}) failed: {e}")
                continue
            for place in places:
                pid = place.get("placeId") or place.get("cid") or place.get("title")
                if pid and pid not in by_id:
                    by_id[pid] = place
        print(f"    {done}/{total_searches} searches done, {len(by_id)} unique place(s) so far")

    nearby = []
    for place in by_id.values():
        place_lat, place_lng = place.get("latitude"), place.get("longitude")
        if place_lat is None or place_lng is None:
            continue
        distance_m = haversine_distance_m(lat, lng, place_lat, place_lng)
        if distance_m <= radius_m:
            place["_distance_m"] = round(distance_m)
            nearby.append(place)
    nearby.sort(key=lambda p: p["_distance_m"])
    return nearby


MENU_CAPTION_KEYWORDS = ("menu", "price list", "prices")


def menu_candidates_from_reviews(reviews):
    """Google Maps review photos captioned as a menu by the reviewer - these are real,
    place-specific photos, so they're tried before falling back to an open web search."""
    candidates = []
    for review in reviews:
        for media in review.get("media") or []:
            image_url = media.get("imageUrl")
            caption = (media.get("caption") or "").lower()
            if image_url and any(k in caption for k in MENU_CAPTION_KEYWORDS):
                candidates.append(image_url)
    return candidates


def _write_json(path, payload):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    tmp.replace(path)


def _try_image_candidates(candidates, source):
    for i, image_url in enumerate(candidates):
        if i:
            time.sleep(1)
        try:
            result = extract_menu_items(image_url)
        except GroqCallError as e:
            print(f"    vision error on candidate image: {e}")
            continue
        if result.get("is_menu") and result.get("items"):
            print(f"    menu source: {source}")
            return image_url, result["items"]
    return None, None


def _try_text_menu(page_text, source_url, source):
    if not page_text:
        return None, None
    try:
        result = extract_menu_items_from_text(page_text)
    except GroqCallError as e:
        print(f"    text error on {source}: {e}")
        return None, None
    if result.get("is_menu") and result.get("items"):
        print(f"    menu source: {source}")
        return source_url, result["items"]
    return None, None


def find_menu(client, restaurant_name, address, website, reviews):
    """Text sources come first: they read the real menu rather than OCR-ing a photo,
    and they run on Groq's text model, which has its own much larger daily budget than
    the vision model."""
    # Tier 1: the restaurant's own website
    menu_source_url, items = _try_text_menu(find_website_menu_text(website), website, "restaurant website")
    if items:
        return menu_source_url, items

    # Tier 2: menu pages found via web search (Toast, Yelp, Seamless, aggregators)
    for hit in client.search_web(f"{restaurant_name} {address} menu", num=MENU_PAGE_CANDIDATES):
        url = hit.get("link")
        menu_source_url, items = _try_text_menu(fetch_page_text(url), url, "web page")
        if items:
            return menu_source_url, items

    # Tier 3: Google Maps review photos captioned as a menu
    review_candidates = menu_candidates_from_reviews(reviews)[:MENU_IMAGE_CANDIDATES]
    menu_source_url, items = _try_image_candidates(review_candidates, "review photo")
    if items:
        return menu_source_url, items

    # Tier 4: open web image search, last resort
    images = client.search_images(f"{restaurant_name} {address} menu", num=MENU_IMAGE_CANDIDATES)
    search_candidates = [img.get("imageUrl") or img.get("image") or img.get("link") for img in images]
    return _try_image_candidates([c for c in search_candidates if c], "web image search")


def main():
    parser = argparse.ArgumentParser(
        description="Collect DishIt restaurant/menu/review data near Columbia University"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max restaurants to fully process (menu + reviews); 0 = all in range",
    )
    parser.add_argument(
        "--max-review-pages",
        type=int,
        default=3,
        help="Review pages per restaurant (~20 reviews/page); each page is a Serper credit",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip restaurants already present in the output file and append to it",
    )
    parser.add_argument(
        "--grid-spacing",
        type=int,
        default=500,
        help="Metres between search anchor points; smaller = more thorough but more Serper credits (0 = single anchor)",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=2,
        help="Result pages to pull per anchor/category search",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only enumerate the restaurants in range, skipping menu/review collection",
    )
    parser.add_argument(
        "--refresh-places",
        action="store_true",
        help="Re-run the enumeration sweep instead of reusing the cached worklist (~156 credits)",
    )
    parser.add_argument("--out", type=str, default=str(OUTPUT_DIR / "restaurants.json"))
    args = parser.parse_args()

    load_dotenv()
    missing = [v for v in ("SERPER_API_KEY", "GROQ_API_KEY") if not os.environ.get(v)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)} - set them in data/collect/.env")

    client = SerperClient()

    # the sweep costs ~156 credits, so buy it once and reuse it on every later run
    cache_path = OUTPUT_DIR / PLACES_CACHE
    if cache_path.exists() and not args.refresh_places:
        with open(cache_path) as f:
            candidates = json.load(f)
        print(f"Loaded {len(candidates)} restaurant(s) from {cache_path.name} (--refresh-places to re-sweep).")
    else:
        print(f"Searching for restaurants within {RADIUS_M}m of Columbia University...")
        candidates = find_nearby_restaurants(
            client, ORIGIN_LAT, ORIGIN_LNG, RADIUS_M, args.grid_spacing, args.search_pages
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(cache_path, candidates)
        print(f"\nFound {len(candidates)} restaurant(s) within {RADIUS_M}m; cached to {cache_path.name}.")

    if args.list_only:
        for place in candidates:
            print(f"  {place['_distance_m']:>5}m  {place.get('title')}  [{place.get('type')}]")
        return

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results = []
    if args.resume and out_path.exists():
        with open(out_path) as f:
            results = json.load(f)
        done_ids = {r.get("place_id") for r in results}
        print(f"Resuming: {len(done_ids)} restaurant(s) already collected.")
        candidates = [c for c in candidates if c.get("placeId") not in done_ids]

    if args.limit:
        candidates = candidates[: args.limit]
    print(f"Processing {len(candidates)} restaurant(s).")

    menuless = 0
    started = time.time()
    for i, place in enumerate(candidates, 1):
        name = place.get("title", "Unknown")
        address = place.get("address", "")
        elapsed = time.time() - started
        rate = f"{elapsed / (i - 1) / 60:.1f} min/restaurant" if i > 1 else "-"
        print(f"[{i}/{len(candidates)}] {name} - {place['_distance_m']}m away ({rate})")

        place_id = normalize_place_id(place.get("placeId"))
        cid = place.get("cid")
        try:
            reviews = client.get_reviews(place_id=place_id, cid=cid, max_pages=args.max_review_pages)
        except RuntimeError as e:
            # a single unscrapable place shouldn't end a multi-hour run
            print(f"    review fetch failed: {e}")
            reviews = []
        print(f"    pulled {len(reviews)} review(s)")

        website = place.get("website")
        menu_source_url, menu_items = find_menu(client, name, address, website, reviews)
        if menu_items:
            print(f"    menu found: {len(menu_items)} item(s)")
        else:
            # keep the record anyway - the reviews were already paid for, and the menu
            # can be backfilled later without re-buying them
            print("    no menu found - keeping reviews only")
            menuless += 1

        results.append(
            {
                "name": name,
                "address": address,
                "place_id": place_id,
                "cid": cid,
                "distance_m": place["_distance_m"],
                "menu_source_url": menu_source_url,
                "menu_items": menu_items,
                # stored raw (unmodified) - Serper's exact field names weren't
                # confirmable from public docs, so the DB schema gets finalized
                # against this real output instead of a guess
                "reviews": reviews,
                "raw_place": {k: v for k, v in place.items() if k != "_distance_m"},
            }
        )
        # checkpoint every restaurant - this run takes hours, so an interrupt or a
        # crash partway through must not throw away what's already collected
        _write_json(out_path, results)
        time.sleep(0.3)

    _write_json(out_path, results)
    mins = (time.time() - started) / 60
    with_menu = sum(1 for r in results if r.get("menu_items"))
    print(f"\nDone in {mins:.0f} min. {len(results)} restaurant(s) saved ({with_menu} with menus, {menuless} menuless this run).")
    print(f"Wrote results to {out_path}")


if __name__ == "__main__":
    main()
