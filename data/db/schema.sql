PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS restaurants (
    id                  INTEGER PRIMARY KEY,
    place_id            TEXT NOT NULL UNIQUE,
    cid                 TEXT,
    name                TEXT NOT NULL,
    address             TEXT,
    latitude            REAL,
    longitude           REAL,
    distance_m          INTEGER,
    category            TEXT,
    website             TEXT,
    phone               TEXT,
    google_rating       REAL,
    google_rating_count INTEGER,
    price_level         TEXT,
    menu_source_url     TEXT,
    collected_at        TEXT NOT NULL
);

-- a place carries several Google categories (Junzi is both "Chinese restaurant"
-- and "Noodle shop"); a child table keeps those filterable by index
CREATE TABLE IF NOT EXISTS restaurant_types (
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    type          TEXT NOT NULL,
    PRIMARY KEY (restaurant_id, type)
);

CREATE TABLE IF NOT EXISTS dishes (
    id            INTEGER PRIMARY KEY,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    description   TEXT,
    -- price_raw keeps the menu's own wording ("M: $2.5, L: $3.25", "+1");
    -- price_min is a best-effort number so prices stay sortable
    price_raw     TEXT,
    price_min     REAL
);

CREATE TABLE IF NOT EXISTS reviews (
    id             INTEGER PRIMARY KEY,
    restaurant_id  INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
    external_id    TEXT NOT NULL UNIQUE,
    rating         INTEGER,
    text           TEXT,
    published_at   TEXT,
    relative_date  TEXT,
    likes          INTEGER,
    url            TEXT,
    author_name    TEXT,
    owner_response TEXT
);

CREATE TABLE IF NOT EXISTS review_media (
    id         INTEGER PRIMARY KEY,
    review_id  INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    image_url  TEXT NOT NULL,
    caption    TEXT
);

-- filled by the dish-extraction/sentiment step, not by the loader
CREATE TABLE IF NOT EXISTS dish_mentions (
    id           INTEGER PRIMARY KEY,
    dish_id      INTEGER NOT NULL REFERENCES dishes(id) ON DELETE CASCADE,
    review_id    INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    sentiment    TEXT CHECK (sentiment IN ('positive', 'negative', 'mixed', 'neutral')),
    quote        TEXT,
    extracted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_dishes_restaurant   ON dishes(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_dishes_name         ON dishes(name);
CREATE INDEX IF NOT EXISTS idx_reviews_restaurant  ON reviews(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_reviews_rating      ON reviews(rating);
CREATE INDEX IF NOT EXISTS idx_media_review        ON review_media(review_id);
CREATE INDEX IF NOT EXISTS idx_types_type          ON restaurant_types(type);
CREATE INDEX IF NOT EXISTS idx_mentions_dish       ON dish_mentions(dish_id);
CREATE INDEX IF NOT EXISTS idx_mentions_review     ON dish_mentions(review_id);
-- A review may support a dish with more than one distinct quoted sentence,
-- but re-running calculation must not duplicate an identical mention.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mentions_unique
    ON dish_mentions(dish_id, review_id, quote);

-- the dish leaderboard the product is built around; every dish appears, with zero
-- counts until mentions exist. Dropped first because CREATE VIEW IF NOT EXISTS keeps
-- whatever definition a database already has, so a corrected one would never reach it.
DROP VIEW IF EXISTS dish_sentiment_summary;
CREATE VIEW dish_sentiment_summary AS
SELECT
    d.id                AS dish_id,
    d.name              AS dish_name,
    r.name              AS restaurant_name,
    COUNT(m.id)                                          AS mention_count,
    COUNT(CASE WHEN m.sentiment = 'positive' THEN 1 END) AS positive,
    COUNT(CASE WHEN m.sentiment = 'negative' THEN 1 END) AS negative,
    -- 'neutral' and 'mixed' mean the same thing here, so anything not clearly
    -- positive or negative is mixed. Taking the remainder rather than counting
    -- 'mixed' alone keeps the three buckets summing to mention_count whatever else
    -- the CHECK constraint lets through, NULL included.
    COUNT(m.id)
      - COUNT(CASE WHEN m.sentiment = 'positive' THEN 1 END)
      - COUNT(CASE WHEN m.sentiment = 'negative' THEN 1 END) AS mixed
FROM dishes d
JOIN restaurants r  ON r.id = d.restaurant_id
LEFT JOIN dish_mentions m ON m.dish_id = d.id
GROUP BY d.id;
