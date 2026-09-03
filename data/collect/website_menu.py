from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; DishItBot/1.0)"
MENU_LINK_KEYWORDS = ("menu",)
MAX_CANDIDATE_PAGES = 3
# Groq's free tier caps a request at 8000 tokens/min total, so page text has to leave
# room for the extracted JSON coming back. ~15k chars of input fits; longer pages clip.
MAX_CHARS = 15000


def _get(url, timeout=15):
    return requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})


def _page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:MAX_CHARS]


def fetch_page_text(url):
    """Scrape one arbitrary URL down to readable text. None if it can't be fetched or
    isn't HTML (PDF menus are common but need a parser we don't have)."""
    if not url or url.lower().split("?")[0].endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp")):
        return None
    try:
        resp = _get(url)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    if "html" not in resp.headers.get("Content-Type", "").lower():
        return None
    text = _page_text(resp.text)
    return text if len(text) > 200 else None


def find_website_menu_text(website_url):
    """Best-effort fetch of a restaurant's own site for menu text. Returns None on any
    failure so the caller falls back to photo-based extraction."""
    if not website_url:
        return None
    try:
        resp = _get(website_url)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    menu_links = []
    for a in soup.find_all("a", href=True):
        label = f"{a.get_text()} {a['href']}".lower()
        if any(k in label for k in MENU_LINK_KEYWORDS):
            menu_links.append(urljoin(website_url, a["href"]))

    candidate_urls = list(dict.fromkeys(menu_links))[:MAX_CANDIDATE_PAGES] or [website_url]
    for url in candidate_urls:
        try:
            page = resp if url == website_url else _get(url)
            page.raise_for_status()
        except requests.RequestException:
            continue
        text = _page_text(page.text)
        if len(text) > 200:
            return text
    return None
