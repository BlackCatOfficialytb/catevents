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
import traceback

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


# Text fragments that mark a Nitter/mirror error or block page rather than
# real results. When the page body contains one of these, the mirror is dead
# and we should move on instead of trusting an empty/garbage extraction.
_ERROR_MARKERS = (
    "instance has been rate limited",
    "no results",
    "not whitelisted",
    "tweets are not available",
    "user not found",
    "error",
)


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


def _page_looks_blocked(page):
    """Return the error-panel message if the page is a block/rate-limit page.

    Returns the message string (truthy) when blocked, or None when the page
    looks normal.
    """
    try:
        # Nitter renders failures inside `.error-panel`; check it first (cheap).
        panel = page.query_selector(".error-panel")
        if panel:
            msg = (panel.inner_text() or "").strip()
            if msg:
                logger.warning("Mirror returned an error panel: %s", msg[:120])
                return msg
    except Exception:
        pass
    return None


class CamoufoxScrapeError(RuntimeError):
    """Raised by scrape_page_strict when a scrape fails.

    Carries `.troubleshooting`: a JSON-serialisable dict with the full
    traceback, per-attempt errors, and page diagnostics (URL, title, HTTP
    status, a body-text snippet) captured at failure time.
    """

    def __init__(self, message, troubleshooting):
        super().__init__(message)
        self.troubleshooting = troubleshooting


def _page_diagnostics(page, response=None):
    """Best-effort snapshot of page state for troubleshooting. Never raises."""
    diag = {}
    try:
        diag["url"] = page.url
    except Exception:
        diag["url"] = None
    try:
        diag["title"] = page.title()
    except Exception:
        diag["title"] = None
    try:
        if response is not None:
            diag["http_status"] = response.status
    except Exception:
        diag["http_status"] = None
    try:
        body = page.inner_text("body") or ""
        diag["body_snippet"] = body.strip()[:500]
        diag["body_length"] = len(body)
    except Exception:
        diag["body_snippet"] = None
    try:
        panel = _page_looks_blocked(page)
        diag["error_panel"] = panel
    except Exception:
        diag["error_panel"] = None
    return diag


def _run_scrape(url, selector, limit, timeout, wait_selector):
    """Core scrape loop.

    Returns ``(items, troubleshooting)``. `items` is the extracted list (empty
    on failure). `troubleshooting` is always a dict describing what happened:
    it includes an ``ok`` flag, per-attempt records (each with any traceback),
    the final page diagnostics, and — on hard failure — a top-level
    ``traceback`` string.
    """
    selector = selector if selector is not None else CAMOUFOX["default_selector"]
    limit = limit if limit is not None else CAMOUFOX["default_limit"]
    timeout = timeout if timeout is not None else CAMOUFOX["timeout_ms"]
    settle_ms = CAMOUFOX["settle_ms"]
    max_retries = CAMOUFOX["max_retries"]
    # When the caller wants to wait for specific content, default to the
    # extraction selector itself.
    wait_for = wait_selector if wait_selector is not None else selector

    troubleshooting = {
        "ok": False,
        "url": url,
        "selector": selector,
        "wait_selector": wait_for,
        "attempts": [],
        "launch_kwargs": _build_launch_kwargs(),
        "traceback": None,
        "error": None,
        "page": None,
    }

    logger.info("Launching Camoufox to scrape: %s", url)
    items = []

    # Import lazily so importing this module (e.g. for scrape_page in the web
    # app) does not require the Camoufox binary to be installed.
    try:
        from camoufox.sync_api import Camoufox
    except Exception as e:
        troubleshooting["error"] = f"Camoufox import failed: {e}"
        troubleshooting["traceback"] = traceback.format_exc()
        logger.error("Camoufox unavailable: %s", e)
        return items, troubleshooting

    try:
        with Camoufox(**troubleshooting["launch_kwargs"]) as browser:
            page = browser.new_page()
            _install_resource_blocking(page)
            try:
                for attempt in range(1, max_retries + 2):  # 1 initial + retries
                    record = {"attempt": attempt, "error": None, "traceback": None,
                              "items_found": 0, "blocked": None}
                    response = None
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout)

                        # Bail early on obvious error/rate-limit pages.
                        panel = _page_looks_blocked(page)
                        if panel:
                            record["blocked"] = panel
                            raise RuntimeError(f"page blocked: {panel[:120]}")

                        # Wait for the real content to render instead of trusting
                        # a fixed sleep. If it never shows, this raises, which the
                        # retry logic below handles.
                        try:
                            page.wait_for_selector(wait_for, timeout=timeout, state="attached")
                        except Exception:
                            if settle_ms:
                                page.wait_for_timeout(settle_ms)

                        items = _extract(page, selector, limit)
                        if not items and settle_ms:
                            # DOM may still be settling; give it one more window.
                            page.wait_for_timeout(settle_ms)
                            items = _extract(page, selector, limit)

                        record["items_found"] = len(items)
                        if items:
                            record["ok"] = True
                            troubleshooting["attempts"].append(record)
                            troubleshooting["ok"] = True
                            break
                        raise RuntimeError(f"no elements matched selector {selector!r}")
                    except Exception as e:
                        record["error"] = f"{type(e).__name__}: {e}"
                        record["traceback"] = traceback.format_exc()
                        # Capture page state at the moment of failure.
                        record["page"] = _page_diagnostics(page, response)
                        troubleshooting["attempts"].append(record)
                        if attempt <= max_retries:
                            logger.warning(
                                "Attempt %d/%d failed for %s: %s — retrying...",
                                attempt, max_retries + 1, url, e,
                            )
                            try:
                                page.wait_for_timeout(1000)
                            except Exception:
                                pass
                        else:
                            logger.error("Camoufox scrape failed for %s: %s", url, e)
            finally:
                # Final page snapshot (whether we succeeded or not).
                troubleshooting["page"] = _page_diagnostics(page)
                try:
                    page.close()
                except Exception:
                    pass
    except Exception as e:
        # Browser launch / context-manager level failure.
        troubleshooting["error"] = f"{type(e).__name__}: {e}"
        troubleshooting["traceback"] = traceback.format_exc()
        logger.error("Camoufox browser error for %s: %s", url, e)
        return items, troubleshooting

    if not troubleshooting["ok"]:
        # Surface the last attempt's traceback at the top level for convenience.
        last = troubleshooting["attempts"][-1] if troubleshooting["attempts"] else None
        if last:
            troubleshooting["error"] = last["error"]
            troubleshooting["traceback"] = last["traceback"]

    logger.info("Camoufox collected %d item(s).", len(items))
    return items, troubleshooting


def scrape_page(url, selector=None, limit=None, timeout=None, wait_selector=None,
                return_troubleshooting=False):
    """
    Open `url` in a stealth headless browser and return up to `limit` text
    snippets matching `selector`.

    All arguments fall back to their configured defaults when omitted. If
    `wait_selector` is given, we explicitly wait for at least one matching
    element to appear before extracting — this is what makes JS-rendered pages
    like Nitter work, since `domcontentloaded` fires long before the tweets are
    in the DOM.

    By default returns a list of ``{"title": ..., "score": ...}`` dicts (empty
    on failure). When `return_troubleshooting=True`, returns the tuple
    ``(items, troubleshooting_dict)`` so callers can log/inspect tracebacks and
    page diagnostics even on partial success.
    """
    items, troubleshooting = _run_scrape(url, selector, limit, timeout, wait_selector)
    if return_troubleshooting:
        return items, troubleshooting
    return items


def scrape_page_strict(url, selector=None, limit=None, timeout=None, wait_selector=None):
    """
    Like `scrape_page`, but RAISES `CamoufoxScrapeError` (with `.troubleshooting`
    attached) instead of quietly returning an empty list when the scrape yields
    nothing or errors out. Use this when you want failures to surface loudly
    with full traceback data.
    """
    items, troubleshooting = _run_scrape(url, selector, limit, timeout, wait_selector)
    if not items:
        raise CamoufoxScrapeError(
            troubleshooting.get("error") or f"Camoufox scrape yielded no results for {url}",
            troubleshooting,
        )
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
    parser.add_argument("--wait-selector", default=None,
                        help="Selector to wait for before extracting (default: the extraction selector).")
    parser.add_argument("--debug", action="store_true",
                        help="Print full troubleshooting data (tracebacks, page diagnostics).")
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

    data, troubleshooting = scrape_page(
        url, selector=selector, limit=args.limit, timeout=args.timeout,
        wait_selector=args.wait_selector, return_troubleshooting=True,
    )

    if args.debug or not data:
        # On failure (or when asked), emit the troubleshooting bundle to stderr
        # so stdout stays a clean JSON list of results.
        print(json.dumps({"troubleshooting": troubleshooting}, indent=2, ensure_ascii=False),
              file=sys.stderr)

    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0 if data else 1


if __name__ == "__main__":
    raise SystemExit(main())
