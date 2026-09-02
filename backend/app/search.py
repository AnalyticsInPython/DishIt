"""Intent-routing search — ported line-for-line from frontend/app.js's
search(), so the backend reproduces the same routing decisions the
frontend's fixture-mode already made. If the scoring rule ever changes,
change it in both places; specs/api-contract.md documents the rule.
"""

from __future__ import annotations

import re


def normalize(text: str) -> str:
    lowered = text.lower()
    stripped = re.sub(r"[^a-z0-9 ]", " ", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def score(text: str, query: str) -> float:
    t = normalize(text)
    if not t:
        return 0.0
    if t == query:
        return 100.0
    if t.startswith(query):
        return 80.0
    if query in t:
        return 60.0
    query_tokens = query.split(" ")
    text_tokens = set(t.split(" "))
    hits = sum(1 for w in query_tokens if len(w) > 2 and w in text_tokens)
    return 40.0 * (hits / len(query_tokens)) if hits else 0.0


def route(query_raw: str, restaurants: list[dict], dishes: list[dict]) -> dict:
    """Decide dish vs. restaurant lead, mirroring the frontend's three-bucket scorer.

    `restaurants` rows need `id`, `name`, `cuisine`, `distance_m`.
    `dishes` rows need `id`, `name`, `mention_count`.
    """
    query = normalize(query_raw)
    if not query:
        return {
            "query": query_raw,
            "result_type": "dishes",
            "matched_on": "none",
            "dishes": [],
            "restaurants": [],
        }

    cuisine_hit = max((score(r["cuisine"] or "", query) for r in restaurants), default=0.0)
    rest_hit = max((score(r["name"], query) for r in restaurants), default=0.0)
    dish_hit = max((score(d["name"], query) for d in dishes), default=0.0)

    scored_dishes = sorted(
        (d for d in dishes if score(d["name"], query) > 0),
        key=lambda d: (-score(d["name"], query), -d["mention_count"]),
    )
    def rest_score(r: dict) -> float:
        return max(score(r["name"], query), score(r["cuisine"] or "", query))

    scored_rests = sorted(
        (r for r in restaurants if rest_score(r) > 0),
        key=lambda r: (-rest_score(r), r["distance_m"]),
    )

    if cuisine_hit >= 80 and cuisine_hit >= rest_hit and cuisine_hit >= dish_hit:
        matched_on, result_type = "cuisine", "restaurants"
    elif rest_hit >= dish_hit and rest_hit > 0:
        matched_on, result_type = "restaurant_name", "restaurants"
    else:
        matched_on, result_type = "dish_name", "dishes"

    if not scored_dishes and not scored_rests:
        return {
            "query": query_raw,
            "result_type": "dishes",
            "matched_on": "none",
            "dishes": [],
            "restaurants": [],
        }
    if result_type == "dishes" and not scored_dishes:
        matched_on, result_type = "restaurant_name", "restaurants"
    if result_type == "restaurants" and not scored_rests:
        matched_on, result_type = "dish_name", "dishes"

    return {
        "query": query_raw,
        "result_type": result_type,
        "matched_on": matched_on,
        "dishes": scored_dishes,
        "restaurants": scored_rests,
    }
