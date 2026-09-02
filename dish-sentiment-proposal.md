# Product Proposal: Dish-Level Sentiment Tool
**Working title: "DishIt"** 

Prepared: August 31, 2026

---

## 1. One-Line Pitch

For any restaurant, surface which dishes people actually talk about — and whether the chatter on each one is positive, negative, or split — pulled from critic coverage, Reddit discussion, and review excerpts.

## 2. Problem & Opportunity

Star ratings tell you if a restaurant is good. They don't tell you *what to order* once you're there. That answer already exists — scattered across critic write-ups ("get the duck"), Reddit threads ("don't sleep on the cacio e pepe"), and review text — but nobody aggregates it into a single, ranked, per-restaurant view. This is a natural extension of the NYC food-discovery app already explored (Node.js/React/Supabase, with Resy/OpenTable integration): a lightweight, ownable data layer that could sit inside that app or ship as a standalone feature.

## 3. What It Does

- **Dish leaderboard per restaurant** — ranks dishes by how often they're mentioned across sources.
- **Sentiment badge per dish** — Positive / Negative / Mixed, based on the tone of the mentions.
- **"Controversial" flag** — dishes with high mention volume but split sentiment surface as their own category, since a polarizing dish is more interesting (and more shareable) than a flat consensus pick.
- **Supporting quotes** — one or two short excerpts per dish, attributed to their source, functioning as social proof without reproducing full reviews.

## 4. What Data Is Stored & Data Sources

Pull from review sites (Google, Reddit, Beli) as well as from food critic sites (NYT, Infatuation, etc.)

| Table | Purpose | Key fields |
|---|---|---|
| `restaurants` | One row per restaurant in scope | id, name, neighborhood, cuisine, distance to user (dynamically updated), hours open today (can drop if complicated), source_urls |
| `dishes` | Canonical dish entities, deduplicated per restaurant | id, restaurant_id, canonical_name, sentiment |
| `reviews` | The raw text unit a mention came from | id, restaurant_id, dish_id, source_type (critic/reddit/yelp/google), url, raw_text, fetched_at |

## 5. Group Members

William Su
Akhil Parlapalli
Jacob Lundquist
Jon Ye


