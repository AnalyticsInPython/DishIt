"""Smoke tests against a freshly-seeded database — one assertion per
contract guarantee in specs/api-contract.md, not exhaustive coverage of
every field. Seeded data comes from db.py's real seeding path (the
Baylander export plus fixtures.json's fictional restaurants), so these
also catch a seeding regression, not just an API-shape regression.
"""

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
    assert len(body["primary"]) >= 2  # the fixture has Cacio e Pepe at 3 restaurants
    assert all(d["name"] == "Cacio e Pepe" for d in body["primary"])


def test_search_by_cuisine_leads_with_restaurants(client):
    response = client.get("/api/search", params={"q": "Korean", **MORNINGSIDE})
    body = response.json()
    assert body["result_type"] == "restaurants"
    assert body["matched_on"] == "cuisine"
    assert any(r["name"] == "Han & Sons" for r in body["primary"])


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


def test_unknown_restaurant_is_404(client):
    assert client.get("/api/restaurants/999999").status_code == 404


def test_dish_detail_includes_quotes_and_cross_restaurant_matches(client):
    search_response = client.get("/api/search", params={"q": "cacio e pepe"})
    dish_id = search_response.json()["primary"][0]["id"]

    response = client.get(f"/api/dishes/{dish_id}", params=MORNINGSIDE)
    assert response.status_code == 200
    body = response.json()
    assert body["dish"]["name"] == "Cacio e Pepe"
    assert len(body["quotes"]) > 0
    # Same dish name exists at more than one restaurant in the seed data.
    assert len({d["restaurant"]["id"] for d in body["also_at"]}) >= 2


def test_quote_with_no_source_url_is_still_valid(client):
    """Placeholder-seeded quotes have no URL — the API must still return
    them cleanly rather than omitting the field or erroring."""
    search_response = client.get("/api/search", params={"q": "garlic knots"})
    dish_id = search_response.json()["primary"][0]["id"]
    response = client.get(f"/api/dishes/{dish_id}")
    quotes = response.json()["quotes"]
    assert all("source_url" in q for q in quotes)
