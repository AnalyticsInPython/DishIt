import json
import os
import time

import requests

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_RATE_LIMIT_RETRIES = 5
BASE_BACKOFF_S = 5
# Groq occasionally answers with a Retry-After of tens of minutes. Waiting that out
# stalls the whole run, so past this threshold give up and let the caller try the
# next tier instead.
MAX_HONORED_RETRY_AFTER_S = 90
# A request can never use more than the per-minute token ceiling, so reserving more
# output than that makes it permanently unsatisfiable (413, or an absurd Retry-After).
DEFAULT_MAX_COMPLETION_TOKENS = 8000


class GroqCallError(RuntimeError):
    pass


def call_groq_json(messages, model, api_key=None, timeout=90, max_completion_tokens=DEFAULT_MAX_COMPLETION_TOKENS):
    """POST a chat completion requesting JSON-mode output, with rate-limit backoff.

    max_completion_tokens needs real headroom: a long menu serializes to a lot of JSON,
    and truncated output comes back as a json_validate_failed 400 rather than partial data.
    """
    api_key = api_key or os.environ["GROQ_API_KEY"]
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": max_completion_tokens,
        "messages": messages,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        try:
            resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            # a laptop sleeping mid-run kills the socket; retry rather than abort the run
            if attempt == MAX_RATE_LIMIT_RETRIES - 1:
                raise GroqCallError(f"Groq request failed after {MAX_RATE_LIMIT_RETRIES} attempts: {e}") from e
            wait_s = BASE_BACKOFF_S * (2**attempt)
            print(f"    Groq connection error, retrying in {wait_s}s: {e}")
            time.sleep(wait_s)
            continue
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait_s = float(retry_after) if retry_after else BASE_BACKOFF_S * (2**attempt)
            if wait_s > MAX_HONORED_RETRY_AFTER_S:
                raise GroqCallError(f"Groq asked for a {wait_s:.0f}s wait ({model}); skipping rather than stalling")
            print(f"    Groq rate limited ({model}), waiting {wait_s:.0f}s (attempt {attempt + 1}/{MAX_RATE_LIMIT_RETRIES})")
            time.sleep(wait_s)
            continue
        if not resp.ok:
            raise GroqCallError(f"Groq call failed ({resp.status_code}): {resp.text}")
        content = resp.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise GroqCallError(f"Groq returned non-JSON content: {content!r}") from e
    raise GroqCallError(f"Groq call still rate-limited after {MAX_RATE_LIMIT_RETRIES} attempts")
