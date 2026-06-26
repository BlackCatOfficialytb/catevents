# camoufox_scraper.py
"""
Simple Camoufox-based scraper.

Camoufox is a stealth Firefox build with built-in anti-fingerprinting, so it
can load JavaScript-heavy pages that block plain `requests`. We use it here as
a lightweight, headless way to grab visible text/headlines from a page.

Install:
    pip install camoufox[geoip]
    python -m camoufox fetch      # downloads the patched Firefox binary once

Run standalone:
    python camoufox_scraper.py
"""
import logging

from camoufox.sync_api import Camoufox

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def scrape_page(url, selector="h1, h2, h3", limit=10, timeout=30000):
    """
    Open `url` in a stealth headless browser and return up to `limit` text
    snippets matching `selector`.

    Returns a list of {"title": ..., "score": ...} dicts so the output matches
    the shape used by the rest of the trends pipeline.
    """
    items = []
    logger.info(f"Launching Camoufox to scrape: {url}")

    # humanize=True adds human-like cursor movement; os/locale are spoofed for us.
    with Camoufox(headless=True, humanize=True) as browser:
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            # Give client-side rendering a moment to settle.
            page.wait_for_timeout(2000)

            seen = set()
            for el in page.query_selector_all(selector):
                text = (el.inner_text() or "").strip()
                if not text or text.lower() in seen:
                    continue
                seen.add(text.lower())
                items.append({"title": text, "score": "New"})
                if len(items) >= limit:
                    break
        except Exception as e:
            logger.error(f"Camoufox scrape failed for {url}: {e}")
        finally:
            page.close()

    logger.info(f"Camoufox collected {len(items)} item(s).")
    return items


if __name__ == "__main__":
    import json

    data = scrape_page("https://news.ycombinator.com", selector=".titleline > a")
    print(json.dumps(data, indent=2))
