"""Extract dish-level sentiment from a review with Anthropic's Messages API.

Install dependencies and set an API key:
    python -m pip install -r requirements.txt
    export ANTHROPIC_API_KEY="your-key"

Run the preset example:
    python llm_dish_sentiment_calculator.py
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol


PRESET_REVIEW = """
The cacio e pepe was rich and perfectly seasoned, although the portion was
small. I loved the crispy duck; its skin was wonderful and the meat stayed
juicy. Skip the truffle fries because they arrived cold and greasy. The
tiramisu was fine, but it was too sweet for my taste.
"""

MODEL = "claude-3-5-haiku-latest"
SYSTEM_PROMPT = """You extract dish-level restaurant-review sentiment.

Return JSON only, matching this schema exactly:
{"dishes":[{"dish":string,"sentiment":"positive"|"negative"|"neutral"|"mixed",
"confidence":number,"mentions":integer,"evidence":[string]}]}

Rules:
- Extract only dishes, menu items, drinks, or desserts explicitly named in the
  review. Do not extract ingredients, people, service, or ambience.
- Use only review text as evidence. Every evidence entry must be a direct
  sentence excerpt from the review that discusses the named dish.
- Aggregate repeated mentions of the same dish into one object.
- confidence must be between 0 and 1.
- Return {"dishes": []} when no dishes are named.
"""


class MessageClient(Protocol):
    """The portion of the Anthropic client used by this module."""

    class messages(Protocol):
        @staticmethod
        def create(**kwargs: object) -> object: ...


@dataclass(frozen=True)
class DishReview:
    dish: str
    sentiment: str
    confidence: float
    mentions: int
    evidence: tuple[str, ...]


def load_client() -> MessageClient:
    """Construct an Anthropic client from ANTHROPIC_API_KEY."""
    try:
        import anthropic
    except ImportError as error:
        raise RuntimeError(
            "Missing anthropic. Run: python -m pip install -r requirements.txt"
        ) from error

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("Set ANTHROPIC_API_KEY before running this script.")
    return anthropic.Anthropic()


def parse_response(response: object) -> list[DishReview]:
    """Validate the JSON response returned by the LLM."""
    content = getattr(response, "content", None)
    if not isinstance(content, list) or not content:
        raise ValueError("The LLM response did not contain any content blocks.")
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        raise ValueError("The LLM response did not contain a text content block.")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("The LLM returned invalid JSON.") from error

    dishes = payload.get("dishes")
    if not isinstance(dishes, list):
        raise ValueError("The LLM JSON response must contain a dishes list.")

    reviews: list[DishReview] = []
    for item in dishes:
        if not isinstance(item, dict):
            raise ValueError("Every dishes entry must be a JSON object.")
        dish = item.get("dish")
        sentiment = item.get("sentiment")
        confidence = item.get("confidence")
        mentions = item.get("mentions")
        evidence = item.get("evidence")
        if (
            not isinstance(dish, str)
            or sentiment not in {"positive", "negative", "neutral", "mixed"}
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
            or not isinstance(mentions, int)
            or mentions < 1
            or not isinstance(evidence, list)
            or not all(isinstance(quote, str) for quote in evidence)
        ):
            raise ValueError("The LLM response does not match the required schema.")
        reviews.append(
            DishReview(
                dish=dish.lower().strip(),
                sentiment=sentiment,
                confidence=float(confidence),
                mentions=mentions,
                evidence=tuple(evidence),
            )
        )
    return reviews


def analyze_review(
    review_text: str, client: MessageClient | None = None
) -> list[DishReview]:
    """Use Anthropic to extract dishes and their sentiment from one review."""
    if not review_text.strip():
        raise ValueError("review_text cannot be empty.")
    client = client or load_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1_024,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": review_text}],
    )
    return parse_response(response)


if __name__ == "__main__":
    for review in analyze_review(PRESET_REVIEW):
        evidence = " | ".join(review.evidence)
        print(
            f"{review.dish}: {review.sentiment} "
            f"(confidence={review.confidence:.2f}, mentions={review.mentions}; "
            f"evidence: {evidence})"
        )
