"""Extract dish-level sentiment from a restaurant review with pretrained models.

Install the dependencies and spaCy model once:
    python -m pip install -r requirements.txt
    python -m spacy download en_core_web_sm

Run the preset example:
    python dish_sentiment.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
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
ENTITY_LABELS = ["dish", "food", "menu item", "dessert", "beverage"]
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
    reviews = analyze_review(PRESET_REVIEW)
    for review in reviews:
        contexts = " | ".join(review.contexts)
        print(
            f"{review.dish}: {review.sentiment} "
            f"(confidence={review.score:.2f}, mentions={review.mentions}; "
            f"evidence: {contexts})"
        )
