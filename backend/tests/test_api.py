"""Smoke tests against a fresh canonical-schema database fixture."""

import pytest

from app.main import sentiment_and_flags

MORNINGSIDE = {"lat": 40.8075, "lng": -73.9626}


def test_popular_only_returns_dishes_at_or_above_threshold(client):
    response = client.get("/api/popular", params=MORNINGSIDE)
    assert response.status_code == 200
    body = response.json()
    for bucket in ("talked_about", "controversial", "top_rated"):
        assert bucket in body
        for dish in body[bucket]:
            assert dish["mention_count"] >= 5

    assert all(d["is_controversial"] for d in body["controversial"])


def test_search_by_dish_name_leads_with_dishes(client):
    response = client.get("/api/search", params={"q": "cacio e pepe", **MORNINGSIDE})
    body = response.json()
    assert body["result_type"] == "dishes"
    assert body["matched_on"] == "dish_name"
    assert len(body["primary"]) >= 2
    assert all(d["name"].casefold() == "cacio e pepe" for d in body["primary"])


def test_search_by_cuisine_leads_with_restaurants(client):
    response = client.get("/api/search", params={"q": "Korean", **MORNINGSIDE})
    body = response.json()
    assert body["result_type"] == "restaurants"
    assert body["matched_on"] == "cuisine"
    restaurant = next(r for r in body["primary"] if r["name"] == "Han & Sons")
    assert restaurant["cuisine"] == "Korean restaurant"
    assert restaurant["neighborhood"] is None
    assert restaurant["cross_street"] == "114th Street, New York, NY"


def test_search_with_no_match_returns_empty_not_error(client):
    response = client.get("/api/search", params={"q": "zzz_no_such_thing", **MORNINGSIDE})
    assert response.status_code == 200
    assert response.json()["primary"] == []


def test_restaurant_detail_includes_distance_and_dishes(client):
    response = client.get("/api/restaurants/1", params=MORNINGSIDE)
    assert response.status_code == 200
    body = response.json()
    assert body["restaurant"]["distance_m"] is not None
    assert len(body["dishes"]) > 0
    assert all(dish["on_current_menu"] is True for dish in body["dishes"])


def test_unknown_restaurant_is_404(client):
    assert client.get("/api/restaurants/999999").status_code == 404


def test_dish_detail_includes_quotes_and_cross_restaurant_matches(client):
    search_response = client.get("/api/search", params={"q": "cacio e pepe"})
    dish_id = search_response.json()["primary"][0]["id"]

    response = client.get(f"/api/dishes/{dish_id}", params=MORNINGSIDE)
    assert response.status_code == 200
    body = response.json()
    assert body["dish"]["name"].casefold() == "cacio e pepe"
    assert len(body["quotes"]) > 0
    assert all(quote["source_type"] == "google" for quote in body["quotes"])
    # Same dish name exists at more than one restaurant in the canonical fixture.
    assert len({d["restaurant"]["id"] for d in body["also_at"]}) >= 2


def test_quote_with_no_source_url_is_still_valid(client):
    """Google review evidence may omit a public URL."""
    response = client.get("/api/dishes/1")
    quotes = response.json()["quotes"]
    assert all("source_url" in q for q in quotes)
    assert any(q["source_url"] is None for q in quotes)


def test_mixed_summary_maps_to_contract_sentiment_fields(client):
    response = client.get("/api/dishes/1")
    sentiment = response.json()["dish"]["sentiment"]
    assert sentiment == {
        "label": "mixed",
        "score": 50,
        "positive": 2,
        "negative": 2,
        "neutral": 0,
    }
    assert response.json()["dish"]["source_mix"] == {"critic": 0, "public": 5}


def test_menu_dish_with_no_mentions_is_neutral(client):
    response = client.get("/api/dishes/4")
    assert response.json()["dish"]["sentiment"] == {
        "label": "neutral",
        "score": 0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
    }


# (positive, negative, mixed, expected score, expected label)
SENTIMENT_BANDS = [
    # The regression this guards: `mixed` being non-zero used to decide the label
    # on its own, so a dish nobody disliked was published as "100% mixed".
    (7, 0, 2, 100, "positive"),
    (100, 0, 0, 100, "positive"),
    (65, 35, 0, 65, "positive"),   # inclusive lower edge of positive
    (64, 36, 0, 64, "mixed"),
    (40, 60, 0, 40, "mixed"),      # inclusive lower edge of mixed
    (39, 61, 0, 39, "negative"),
    (0, 4, 0, 0, "negative"),
    # No polarity to divide, so the score is 0 but the dish is not negative.
    (0, 0, 3, 0, "mixed"),
    # Nothing said at all.
    (0, 0, 0, 0, "neutral"),
]


@pytest.mark.parametrize(("positive", "negative", "mixed", "score", "label"), SENTIMENT_BANDS)
def test_label_is_read_off_the_score(positive, negative, mixed, score, label):
    sentiment, _, _ = sentiment_and_flags(
        {
            "positive": positive,
            "negative": negative,
            "mention_count": positive + negative + mixed,
        }
    )
    assert (sentiment["score"], sentiment["label"]) == (score, label)
