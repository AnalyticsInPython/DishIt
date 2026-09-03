"""Extract dish-level sentiment from a restaurant review with pretrained models.

Install the dependencies and spaCy model once:
    python -m pip install -r requirements.txt
    python -m spacy download en_core_web_sm

Run the preset example:
    python dish_sentiment_calculator.py

Analyze a stored source:
    python dish_sentiment_calculator.py analyze database.db SOURCE_ID
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

import spacy
from spacy.language import Language
from spacy.matcher import PhraseMatcher
from spacy.tokens import Doc, Span


PRESET_REVIEW = """
The cacio e pepe was rich and perfectly seasoned, although the portion was
small. I loved the crispy duck; its skin was wonderful and the meat stayed
juicy. Skip the truffle fries because they arrived cold and greasy. The
tiramisu was fine, but it was too sweet for my taste.
"""

GLINER_MODEL = "urchade/gliner_base"
ABSA_MODEL = "yangheng/deberta-v3-base-absa-v1.1"
ENTITY_LABELS = ["specific dish or menu item", "dessert", "named beverage"]
ENTITY_THRESHOLD = 0.5

EntityExtractor = Callable[[str], list[dict[str, object]]]
AspectSentimentAnalyzer = Callable[[str, str], list[dict[str, object]]]


@dataclass(frozen=True)
class DishReview:
    """Aggregate pretrained-model sentiment evidence for one dish."""

    dish: str
    sentiment: str
    score: float
    mentions: int
    contexts: tuple[str, ...]


@dataclass
class DishEvidence:
    score: float = 0.0
    mentions: int = 0
    contexts: set[str] = field(default_factory=set)


REVIEW_OUTPUT_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE restaurants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    neighborhood TEXT,
    cuisine TEXT,
    source_urls TEXT
);

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
    source_type TEXT,
    url TEXT,
    raw_text TEXT NOT NULL,
    fetched_at TEXT
);

CREATE TABLE dishes (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
    canonical_name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (restaurant_id, canonical_name)
);

CREATE TABLE mentions (
    id INTEGER PRIMARY KEY,
    dish_id INTEGER NOT NULL REFERENCES dishes(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    sentiment TEXT NOT NULL,
    confidence REAL NOT NULL,
    quote TEXT NOT NULL,
    extracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

def get_source_review(
    connection: sqlite3.Connection, source_id: int
) -> tuple[int, str]:
    """Retrieve the restaurant and review text for an existing source record."""
    row = connection.execute(
        "SELECT restaurant_id, raw_text FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No source exists with id {source_id}.")
    return int(row[0]), str(row[1])


def get_menu_items(
    connection: sqlite3.Connection, restaurant_id: int
) -> list[str]:
    """Retrieve restaurant menu items to supplement zero-shot review extraction."""
    return [
        str(row[0])
        for row in connection.execute(
            """
            SELECT canonical_name
            FROM dishes
            WHERE restaurant_id = ?
            ORDER BY canonical_name
            """,
            (restaurant_id,),
        )
    ]


def get_or_create_dish(
    connection: sqlite3.Connection, restaurant_id: int, dish_name: str
) -> int:
    """Find a dish by name or save a newly extracted dish for the restaurant."""
    row = connection.execute(
        """
        SELECT id FROM dishes
        WHERE restaurant_id = ? AND canonical_name = ?
        """,
        (restaurant_id, dish_name),
    ).fetchone()
    if row is not None:
        return int(row[0])

    cursor = connection.execute(
        """
        INSERT INTO dishes (restaurant_id, canonical_name)
        VALUES (?, ?)
        """,
        (restaurant_id, dish_name),
    )
    return int(cursor.lastrowid)


def analyze_source(
    connection: sqlite3.Connection,
    source_id: int,
    nlp: Language | None = None,
    dish_extractor: EntityExtractor | None = None,
    aspect_sentiment_analyzer: AspectSentimentAnalyzer | None = None,
) -> list[DishReview]:
    """Analyze a source row and replace its persisted dish-sentiment mentions."""
    reviews = analyze_source_review(
        connection,
        source_id,
        nlp=nlp,
        dish_extractor=dish_extractor,
        aspect_sentiment_analyzer=aspect_sentiment_analyzer,
    )

    restaurant_id, _ = get_source_review(connection, source_id)
    with connection:
        connection.execute("DELETE FROM mentions WHERE source_id = ?", (source_id,))
        for review in reviews:
            dish_id = get_or_create_dish(connection, restaurant_id, review.dish)
            confidence = abs(review.score) / review.mentions
            for context in review.contexts:
                connection.execute(
                    """
                    INSERT INTO mentions (
                        dish_id, source_id, sentiment, confidence, quote
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (dish_id, source_id, review.sentiment, confidence, context),
                )
    return reviews


def analyze_source_review(
    connection: sqlite3.Connection,
    source_id: int,
    nlp: Language | None = None,
    dish_extractor: EntityExtractor | None = None,
    aspect_sentiment_analyzer: AspectSentimentAnalyzer | None = None,
) -> list[DishReview]:
    """Analyze an existing source without writing to its input database."""
    restaurant_id, review_text = get_source_review(connection, source_id)
    return analyze_review(
        review_text,
        menu_items=get_menu_items(connection, restaurant_id),
        nlp=nlp,
        dish_extractor=dish_extractor,
        aspect_sentiment_analyzer=aspect_sentiment_analyzer,
    )


def analyze_all_sources(
    connection: sqlite3.Connection,
    nlp: Language | None = None,
    dish_extractor: EntityExtractor | None = None,
    aspect_sentiment_analyzer: AspectSentimentAnalyzer | None = None,
) -> dict[int, list[DishReview]]:
    """Analyze every stored source and persist its dish-sentiment mentions."""
    source_ids = [
        int(row[0]) for row in connection.execute("SELECT id FROM sources ORDER BY id")
    ]
    return {
        source_id: analyze_source(
            connection,
            source_id,
            nlp=nlp,
            dish_extractor=dish_extractor,
            aspect_sentiment_analyzer=aspect_sentiment_analyzer,
        )
        for source_id in source_ids
    }


def build_manual_review_outputs(
    input_database: Path,
    review_output_database: Path,
    review_output_json: Path,
    dish_output_database: Path | None = None,
    dish_output_json: Path | None = None,
    nlp: Language | None = None,
    dish_extractor: EntityExtractor | None = None,
    aspect_sentiment_analyzer: AspectSentimentAnalyzer | None = None,
) -> tuple[Path, Path]:
    """Create review-first and dish-first SQLite/JSON exports for manual review."""
    dish_output_database = dish_output_database or review_output_database.with_stem(
        f"{review_output_database.stem}_by_dish"
    )
    dish_output_json = dish_output_json or review_output_json.with_stem(
        f"{review_output_json.stem}_by_dish"
    )
    output_paths = (
        review_output_database,
        review_output_json,
        dish_output_database,
        dish_output_json,
    )
    if any(path.exists() for path in output_paths):
        raise FileExistsError("Review output paths must not already exist.")

    with sqlite3.connect(input_database) as input_connection:
        input_connection.row_factory = sqlite3.Row
        restaurant_rows = input_connection.execute(
            "SELECT id, name, neighborhood, cuisine, source_urls FROM restaurants"
        ).fetchall()
        source_rows = input_connection.execute(
            """
            SELECT id, restaurant_id, source_type, url, raw_text, fetched_at
            FROM sources
            ORDER BY id
            """
        ).fetchall()
        source_reviews = {
            int(source["id"]): analyze_source_review(
                input_connection,
                int(source["id"]),
                nlp=nlp,
                dish_extractor=dish_extractor,
                aspect_sentiment_analyzer=aspect_sentiment_analyzer,
            )
            for source in source_rows
        }

    review_results: list[dict[str, object]] = []
    with sqlite3.connect(review_output_database) as output_connection:
        output_connection.executescript(REVIEW_OUTPUT_SCHEMA)
        with output_connection:
            output_connection.executemany(
                """
                INSERT INTO restaurants (id, name, neighborhood, cuisine, source_urls)
                VALUES (:id, :name, :neighborhood, :cuisine, :source_urls)
                """,
                restaurant_rows,
            )
            output_connection.executemany(
                """
                INSERT INTO sources (
                    id, restaurant_id, source_type, url, raw_text, fetched_at
                )
                VALUES (:id, :restaurant_id, :source_type, :url, :raw_text, :fetched_at)
                """,
                source_rows,
            )
            for source in source_rows:
                restaurant_id = int(source["restaurant_id"])
                for review in source_reviews[int(source["id"])]:
                    dish_id = get_or_create_dish(output_connection, restaurant_id, review.dish)
                    confidence = abs(review.score) / review.mentions
                    review_results.append(
                        {
                            "source_id": source["id"],
                            "restaurant_id": restaurant_id,
                            "dish_id": dish_id,
                            "dish": review.dish,
                            "sentiment": review.sentiment,
                            "confidence": confidence,
                            "mentions": review.mentions,
                            "evidence": list(review.contexts),
                        }
                    )
                    for context in review.contexts:
                        output_connection.execute(
                            """
                            INSERT INTO mentions (
                                dish_id, source_id, sentiment, confidence, quote
                            )
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                dish_id,
                                source["id"],
                                review.sentiment,
                                confidence,
                                context,
                            ),
                        )

    review_payload = {
        "restaurants": [dict(restaurant) for restaurant in restaurant_rows],
        "reviews": [
            {
                "source_id": source["id"],
                "restaurant_id": source["restaurant_id"],
                "review_text": source["raw_text"],
                "dish_sentiment": [
                    result
                    for result in review_results
                    if result["source_id"] == source["id"]
                ],
            }
            for source in source_rows
        ],
    }
    review_output_json.write_text(
        json.dumps(review_payload, indent=2) + "\n", encoding="utf-8"
    )

    with sqlite3.connect(review_output_database) as review_connection:
        with sqlite3.connect(dish_output_database) as output_connection:
            review_connection.backup(output_connection)
            output_connection.executescript(
                """
                CREATE TABLE dish_sentiment_summary (
                    dish_id INTEGER PRIMARY KEY REFERENCES dishes(id),
                    overall_sentiment TEXT NOT NULL,
                    mention_count INTEGER NOT NULL,
                    positive_mentions INTEGER NOT NULL,
                    negative_mentions INTEGER NOT NULL,
                    neutral_mentions INTEGER NOT NULL,
                    average_confidence REAL NOT NULL
                );
                """
            )
            with output_connection:
                output_connection.execute(
                    """
                    INSERT INTO dish_sentiment_summary (
                        dish_id, overall_sentiment, mention_count, positive_mentions,
                        negative_mentions, neutral_mentions, average_confidence
                    )
                    SELECT
                        dish_id,
                        CASE
                            WHEN SUM(sentiment = 'positive') > 0
                             AND SUM(sentiment = 'negative') > 0 THEN 'mixed'
                            WHEN SUM(sentiment = 'positive') > SUM(sentiment = 'negative')
                                THEN 'positive'
                            WHEN SUM(sentiment = 'negative') > SUM(sentiment = 'positive')
                                THEN 'negative'
                            ELSE 'neutral'
                        END,
                        COUNT(*),
                        SUM(sentiment = 'positive'),
                        SUM(sentiment = 'negative'),
                        SUM(sentiment = 'neutral'),
                        AVG(confidence)
                    FROM mentions
                    GROUP BY dish_id
                    """
                )
                dish_rows = output_connection.execute(
                    """
                    SELECT
                        d.id, d.restaurant_id, d.canonical_name,
                        s.overall_sentiment, s.mention_count, s.positive_mentions,
                        s.negative_mentions, s.neutral_mentions, s.average_confidence
                    FROM dishes AS d
                    JOIN dish_sentiment_summary AS s ON s.dish_id = d.id
                    ORDER BY d.id
                    """
                ).fetchall()
                mention_rows = output_connection.execute(
                    """
                    SELECT dish_id, source_id, sentiment, confidence, quote
                    FROM mentions
                    ORDER BY id
                    """
                ).fetchall()

    dish_payload = {
        "restaurants": [dict(restaurant) for restaurant in restaurant_rows],
        "dishes": [
            {
                "dish_id": dish_id,
                "restaurant_id": restaurant_id,
                "dish": canonical_name,
                "sentiment_summary": {
                    "overall_sentiment": overall_sentiment,
                    "mention_count": mention_count,
                    "positive_mentions": positive_mentions,
                    "negative_mentions": negative_mentions,
                    "neutral_mentions": neutral_mentions,
                    "average_confidence": round(average_confidence, 4),
                },
                "mentions": [
                    {
                        "source_id": source_id,
                        "sentiment": sentiment,
                        "confidence": round(confidence, 4),
                        "evidence": quote,
                    }
                    for (
                        mention_dish_id,
                        source_id,
                        sentiment,
                        confidence,
                        quote,
                    ) in mention_rows
                    if mention_dish_id == dish_id
                ],
            }
            for (
                dish_id,
                restaurant_id,
                canonical_name,
                overall_sentiment,
                mention_count,
                positive_mentions,
                negative_mentions,
                neutral_mentions,
                average_confidence,
            ) in dish_rows
        ],
    }
    dish_output_json.write_text(
        json.dumps(dish_payload, indent=2) + "\n", encoding="utf-8"
    )
    return dish_output_database, dish_output_json


def load_nlp() -> Language:
    """Load English sentence segmentation for extracted entity context."""
    try:
        return spacy.load("en_core_web_sm")
    except OSError as error:
        raise RuntimeError(
            "Missing spaCy's English model. Run: python -m spacy download en_core_web_sm"
        ) from error


@lru_cache(maxsize=1)
def load_dish_extractor() -> EntityExtractor:
    """Load GLiNER, which identifies dish-like spans without a menu vocabulary."""
    try:
        from gliner import GLiNER
    except ImportError as error:
        raise RuntimeError(
            "Missing GLiNER. Run: python -m pip install -r requirements.txt"
        ) from error

    model = GLiNER.from_pretrained(GLINER_MODEL)

    def extract(text: str) -> list[dict[str, object]]:
        return model.predict_entities(
            text, ENTITY_LABELS, threshold=ENTITY_THRESHOLD
        )

    return extract


@lru_cache(maxsize=1)
def load_aspect_sentiment_analyzer() -> AspectSentimentAnalyzer:
    """Load a pretrained aspect-based sentiment classifier."""
    try:
        from transformers import pipeline
    except ImportError as error:
        raise RuntimeError(
            "Missing transformers. Run: python -m pip install -r requirements.txt"
        ) from error

    classifier = pipeline("text-classification", model=ABSA_MODEL)

    def classify(sentence: str, aspect: str) -> list[dict[str, object]]:
        return classifier(sentence, text_pair=aspect, top_k=None)

    return classify


def make_menu_matcher(nlp: Language, menu_items: Iterable[str]) -> PhraseMatcher:
    """Create a matcher from optional caller-provided menu items."""
    matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
    patterns = [nlp.make_doc(item) for item in menu_items if item.strip()]
    if patterns:
        matcher.add("MENU_ITEM", patterns)
    return matcher


def extracted_dish_spans(
    doc: Doc, nlp: Language, extractor: EntityExtractor, menu_items: Iterable[str]
) -> list[Span]:
    """Merge GLiNER review-only entities with exact optional menu-item matches."""
    spans = list(make_menu_matcher(nlp, menu_items)(doc, as_spans=True))
    # GLiNER offsets guarantee a candidate comes from the review rather than a
    # hallucinated menu item. char_span converts those offsets into spaCy spans.
    for entity in extractor(doc.text):
        label = str(entity.get("label", "")).lower()
        if label not in ENTITY_LABELS:
            continue
        start = entity.get("start")
        end = entity.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        span = doc.char_span(start, end, alignment_mode="expand")
        if span is not None:
            spans.append(span)

    unique_spans = {(span.start, span.end): span for span in spans}
    selected: list[Span] = []
    for span in sorted(
        unique_spans.values(), key=lambda item: (item.start, -(item.end - item.start))
    ):
        if any(
            span.start < selected_span.end and selected_span.start < span.end
            for selected_span in selected
        ):
            continue
        selected.append(span)
    return selected


def classified_score(results: list[dict[str, object]]) -> float:
    """Convert the highest-confidence ABSA label into a signed confidence."""
    if not results:
        raise ValueError("The ABSA classifier returned no sentiment labels.")
    result = max(results, key=lambda item: float(item["score"]))
    label = str(result["label"]).lower().replace("_", "")
    confidence = float(result["score"])
    if label in {"positive", "label2"}:
        return confidence
    if label in {"negative", "label0"}:
        return -confidence
    if label in {"neutral", "label1"}:
        return 0.0
    raise ValueError(f"Unsupported sentiment label returned by classifier: {result['label']}")


def label_sentiment(score: float) -> str:
    if score > 0.05:
        return "positive"
    if score < -0.05:
        return "negative"
    return "neutral"


def analyze_review(
    review_text: str,
    menu_items: Iterable[str] = (),
    nlp: Language | None = None,
    dish_extractor: EntityExtractor | None = None,
    aspect_sentiment_analyzer: AspectSentimentAnalyzer | None = None,
) -> list[DishReview]:
    """Extract review dishes, then classify sentiment for each dish as an aspect."""
    nlp = nlp or load_nlp()
    dish_extractor = dish_extractor or load_dish_extractor()
    aspect_sentiment_analyzer = (
        aspect_sentiment_analyzer or load_aspect_sentiment_analyzer()
    )
    doc = nlp(review_text)
    evidence: dict[str, DishEvidence] = {}

    for dish in extracted_dish_spans(doc, nlp, dish_extractor, menu_items):
        name = dish.text.lower().strip()
        context = dish.sent.text.strip()
        score = classified_score(aspect_sentiment_analyzer(context, dish.text))
        dish_evidence = evidence.setdefault(name, DishEvidence())
        dish_evidence.score += score
        dish_evidence.mentions += 1
        dish_evidence.contexts.add(context)

    return [
        DishReview(
            dish=name,
            sentiment=label_sentiment(values.score),
            score=values.score,
            mentions=values.mentions,
            contexts=tuple(sorted(values.contexts)),
        )
        for name, values in sorted(evidence.items())
    ]


# TODO: Once a labeled restaurant-review dataset exists, fine-tune a dish NER
# model and ABSA classifier on it. Replace the two pretrained loaders above
# while preserving analyze_review's review_text and optional menu_items inputs.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract dish-level sentiment with pretrained local models."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("analyze", "analyze-all", "review"),
        help="Analyze one/all sources or create manual-review exports.",
    )
    parser.add_argument("database", nargs="?", help="Path to the SQLite database.")
    parser.add_argument(
        "target",
        nargs="?",
        help="The sources.id for analyze, or output SQLite path for review.",
    )
    parser.add_argument(
        "output_json",
        nargs="?",
        help="JSON output path for the review command.",
    )
    arguments = parser.parse_args()

    if arguments.command == "analyze":
        if arguments.database is None or arguments.target is None:
            parser.error("analyze requires DATABASE and SOURCE_ID.")
        try:
            source_id = int(arguments.target)
        except ValueError:
            parser.error("SOURCE_ID must be an integer.")
        with sqlite3.connect(arguments.database) as connection:
            reviews = analyze_source(connection, source_id)
    elif arguments.command == "analyze-all":
        if arguments.database is None or arguments.target is not None:
            parser.error("analyze-all requires only DATABASE.")
        with sqlite3.connect(arguments.database) as connection:
            all_reviews = analyze_all_sources(connection)
        reviews = [
            review for source_reviews in all_reviews.values() for review in source_reviews
        ]
    elif arguments.command == "review":
        if (
            arguments.database is None
            or arguments.target is None
            or arguments.output_json is None
        ):
            parser.error("review requires INPUT_DATABASE OUTPUT_DATABASE OUTPUT_JSON.")
        dish_database, dish_json = build_manual_review_outputs(
            Path(arguments.database),
            Path(arguments.target),
            Path(arguments.output_json),
        )
        print(
            f"Wrote manual-review exports to {arguments.target} and "
            f"{arguments.output_json}; dish rollups to {dish_database} and {dish_json}."
        )
        raise SystemExit(0)
    else:
        reviews = analyze_review(PRESET_REVIEW)

    for review in reviews:
        contexts = " | ".join(review.contexts)
        print(
            f"{review.dish}: {review.sentiment} "
            f"(confidence={review.score:.2f}, mentions={review.mentions}; "
            f"evidence: {contexts})"
        )
