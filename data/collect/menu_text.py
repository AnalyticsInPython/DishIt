from groq_client import call_groq_json

TEXT_MODEL = "openai/gpt-oss-120b"

PROMPT = """The following is raw text scraped from a restaurant's website, which may or may not \
contain a food/drink menu mixed in with navigation, hours, and other page content.
Respond with ONLY a JSON object, no other text, in this exact shape:
{"is_menu": true or false, "items": [{"name": "...", "price": "... or null", "description": "... or null"}]}
Set "is_menu" to false and "items" to an empty list if no real menu items are present.
Only include actual dish/drink entries - skip navigation links, hours, addresses, and section headers.
Extract EVERY item you find, across every section of the menu, from the first to the last.
Do not stop early, do not summarize, and do not return only a sample."""


def extract_menu_items_from_text(page_text, **kwargs):
    messages = [{"role": "user", "content": f"{PROMPT}\n\n---\n{page_text}"}]
    return call_groq_json(messages, TEXT_MODEL, **kwargs)
