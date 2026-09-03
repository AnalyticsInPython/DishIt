import os
import time

import requests

BASE_URL = "https://google.serper.dev"
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_RETRIES = 4
BASE_BACKOFF_S = 2


def normalize_place_id(place_id):
    """Serper wraps placeId as "https://www.google.com/goto?url=ChIJ...". The wrapped
    form makes /reviews fail with a 500, and breaks id comparisons between runs."""
    if not place_id:
        return None
    return place_id.split("url=", 1)[1] if "url=" in place_id else place_id


class SerperClient:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ["SERPER_API_KEY"]
        self.session = requests.Session()
        self.session.headers.update(
            {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        )

    def _post(self, path, payload):
        """Serper returns transient 5xx ("Scraping failed") often enough that a long
        collection run will hit one; retrying beats losing the whole run."""
        for attempt in range(MAX_RETRIES):
            last_attempt = attempt == MAX_RETRIES - 1
            try:
                resp = self.session.post(f"{BASE_URL}{path}", json=payload, timeout=30)
            except requests.RequestException as e:
                if last_attempt:
                    raise RuntimeError(f"Serper {path} failed after {MAX_RETRIES} attempts: {e}") from e
                time.sleep(BASE_BACKOFF_S * (2**attempt))
                continue
            if resp.status_code in RETRY_STATUSES and not last_attempt:
                wait_s = BASE_BACKOFF_S * (2**attempt)
                print(f"    Serper {path} returned {resp.status_code}, retrying in {wait_s}s")
                time.sleep(wait_s)
                continue
            if not resp.ok:
                raise RuntimeError(f"Serper {path} failed ({resp.status_code}): {resp.text}")
            return resp.json()

    def search_places(self, query, lat, lng, zoom=15, max_pages=5):
        """Paginate Serper's Maps search anchored at (lat, lng). Dedupes by placeId/cid."""
        results = []
        seen_ids = set()
        for page in range(1, max_pages + 1):
            data = self._post("/maps", {"q": query, "ll": f"@{lat},{lng},{zoom}z", "page": page})
            places = data.get("places", [])
            if not places:
                break
            new_count = 0
            for place in places:
                if place.get("placeId"):
                    place["placeId"] = normalize_place_id(place["placeId"])
                pid = place.get("placeId") or place.get("cid")
                if pid and pid in seen_ids:
                    continue
                if pid:
                    seen_ids.add(pid)
                results.append(place)
                new_count += 1
            if new_count == 0:
                break
            time.sleep(0.3)
        return results

    def get_reviews(self, place_id=None, cid=None, max_pages=10):
        assert place_id or cid, "place_id or cid required"
        place_id = normalize_place_id(place_id)
        all_reviews = []
        next_page_token = None
        for _ in range(max_pages):
            payload = {}
            if place_id:
                payload["placeId"] = place_id
            if cid:
                payload["cid"] = cid
            if next_page_token:
                payload["nextPageToken"] = next_page_token
            data = self._post("/reviews", payload)
            reviews = data.get("reviews", [])
            all_reviews.extend(reviews)
            next_page_token = data.get("nextPageToken")
            if not next_page_token or not reviews:
                break
            time.sleep(0.3)
        return all_reviews

    def search_images(self, query, num=5):
        data = self._post("/images", {"q": query, "num": num})
        return data.get("images", [])

    def search_web(self, query, num=5):
        data = self._post("/search", {"q": query, "num": num})
        return data.get("organic", [])
