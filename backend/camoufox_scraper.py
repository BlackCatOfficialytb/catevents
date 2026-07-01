# camoufox_scraper.py
"""
Camoufox-based stealth scraper.

Camoufox is a hardened Firefox build with built-in anti-fingerprinting, so it
can load JavaScript-heavy pages that block plain `requests`. This module wraps
it into a small, config-driven scraper that returns text snippets in the same
``{"title": ..., "score": ...}`` shape used by the rest of the trends pipeline.

Every knob (headless, humanize, selector, limits, timeouts, retries, blocked
resource types, proxy, default URL) is read from `config.py`, which in turn is
loaded from `config.kdl` / `config.kdl.default`.

Install:
    pip install camoufox[geoip]
    python -m camoufox fetch      # downloads the patched Firefox binary once

Run standalone (uses camoufox.default_url from the config):
    python camoufox_scraper.py

Scrape a specific page:
    python camoufox_scraper.py --url https://example.com --selector "article h2" --limit 5
"""
import argparse
import json
import logging
import sys

from config import CAMOUFOX

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Resource types Camoufox/Playwright understands for request routing.
_KNOWN_RESOURCE_TYPES = {
    "document", "stylesheet", "image", "media", "font", "script",
    "texttrack", "xhr", "fetch", "eventsource", "websocket", "manifest", "other",
}


def _build_launch_kwargs():
    """Translate the CAMOUFOX config dict into Camoufox() constructor kwargs."""
    kwargs = {
        "headless": CAMOUFOX["headless"],
        "humanize": CAMOUFOX["humanize"],
    }
    proxy = (CAMOUFOX.get("proxy") or "").strip()
    if proxy:
        # Camoufox accepts a Playwright-style proxy dict.
        kwargs["proxy"] = {"server": proxy}
    return kwargs


def _install_resource_blocking(page):
    """Abort requests for heavy resource types to speed up scraping."""
    block = {r for r in CAMOUFOX.get("block_resources", []) if r in _KNOWN_RESOURCE_TYPES}
    if not block:
        return
    unknown = set(CAMOUFOX.get("block_resources", [])) - _KNOWN_RESOURCE_TYPES
    if unknown:
        logger.warning("Ignoring unknown block_resources entries: %s", ", ".join(sorted(unknown)))

    def _route(route):
        try:
            if route.request.resource_type in block:
                route.abort()
            else:
                route.continue_()
        except Exception:
            # Never let a routing hiccup crash the scrape.
            try:
                route.continue_()
            except Exception:
                pass

    page.route("**/*", _route)


def _extract(page, selector, limit):
    """Pull up to `limit` unique, non-empty text snippets matching `selector`."""
    items = []
    seen = set()
    for el in page.query_selector_all(selector):
        text = (el.inner_text() or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        items.append({"title": text, "score": "New"})
        if len(items) >= limit:
            break
    return items


def scrape_page(url, selector=None, limit=None, timeout=None):
    """
    Open `url` in a stealth headless browser and return up to `limit` text
    snippets matching `selector`.

    All arguments fall back to their configured defaults when omitted. Returns
    a list of ``{"title": ..., "score": ...}`` dicts (empty on failure).
    """
    selector = selector if selector is not None else CAMOUFOX["default_selector"]
    limit = limit if limit is not None else CAMOUFOX["default_limit"]
    timeout = timeout if timeout is not None else CAMOUFOX["timeout_ms"]
    settle_ms = CAMOUFOX["settle_ms"]
    max_retries = CAMOUFOX["max_retries"]

    # Import lazily so importing this module (e.g. for scrape_page in the web
    # app) does not require the Camoufox binary to be installed.
    from camoufox.sync_api import Camoufox

    logger.info("Launching Camoufox to scrape: %s", url)
    items = []

    with Camoufox(**_build_launch_kwargs()) as browser:
        page = browser.new_page()
        _install_resource_blocking(page)
        try:
            for attempt in range(1, max_retries + 2):  # 1 initial try + max_retries
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                    # Give client-side rendering a moment to settle.
                    if settle_ms:
                        page.wait_for_timeout(settle_ms)
                    items = _extract(page, selector, limit)
                    break
                except Exception as e:
                    if attempt <= max_retries:
                        logger.warning(
                            "Attempt %d/%d failed for %s: %s — retrying...",
                            attempt, max_retries + 1, url, e,
                        )
                        page.wait_for_timeout(1000)
                    else:
                        logger.error("Camoufox scrape failed for %s: %s", url, e)
        finally:
            try:
                page.close()
            except Exception:
                pass

    logger.info("Camoufox collected %d item(s).", len(items))
    return items


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Camoufox stealth scraper.")
    parser.add_argument("--url", default=None,
                        help="Page to scrape (default: camoufox.default_url from config).")
    parser.add_argument("--selector", default=None,
                        help="CSS selector for text snippets (default: from config).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max snippets to return (default: from config).")
    parser.add_argument("--timeout", type=int, default=None,
                        help="Navigation timeout in ms (default: from config).")
    return parser.parse_args(argv)


def main(argv=None):
    # Windows consoles default to cp1252 and choke on non-ASCII output.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if not CAMOUFOX["enabled"]:
        logger.warning("Camoufox is disabled in config (camoufox.enabled = #false).")

    # When no URL is given, run the standalone demo against the configured
    # default page and its demo selector.
    if args.url is None:
        url = CAMOUFOX["default_url"]
        selector = args.selector if args.selector is not None else CAMOUFOX["default_demo_selector"]
    else:
        url = args.url
        selector = args.selector

    data = scrape_page(url, selector=selector, limit=args.limit, timeout=args.timeout)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0 if data else 1


if __name__ == "__main__":
    raise SystemExit(main())
