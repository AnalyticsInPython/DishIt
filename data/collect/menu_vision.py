from groq_client import GroqCallError, call_groq_json

# Each model has its own 200k tokens/day budget; 3.6 gets exhausted first, so prefer
# 3.8 and fall back to 3.6 when 3.8's own daily budget runs out.
VISION_MODELS = ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b")

PROMPT = """You are looking at a single photo that may or may not be a restaurant menu.
Respond with ONLY a JSON object, no other text, in this exact shape:
{"is_menu": true or false, "items": [{"name": "...", "price": "... or null", "description": "... or null"}]}
If the photo is not a legible restaurant menu (e.g. it's food, the storefront, a review \
screenshot, or unrelated), set "is_menu" to false and "items" to an empty list.
Only include actual dish/drink entries in "items" - skip section headers."""

MenuVisionError = GroqCallError


def extract_menu_items(image_url, **kwargs):
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    last_error = None
    for model in VISION_MODELS:
        try:
            return call_groq_json(messages, model, **kwargs)
        except GroqCallError as e:
            # only a spent daily budget is worth trying another model for; a bad image
            # or malformed response will fail the same way on every model
            if "tokens per day" not in str(e) and "wait" not in str(e):
                raise
            print(f"    {model} unavailable, trying next vision model")
            last_error = e
    raise last_error
