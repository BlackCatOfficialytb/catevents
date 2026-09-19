# toml_config_reader.py
"""Read RSS feed configs from TOML files ("XML adapters") and scrape feeds.

Design
------
Every RSS/Atom feed gets a per-file TOML config describing how to adapt the
generic XML parser to that feed's specific element names — this is the
"XML adapter": ``list_tag``, ``title_tag``, ``score_tag``, ``default_score``.
A central registry TOML (``rss_feeds.toml``) maps feed IDs to their config
files.

This keeps feed-specific parsing logic in config, not code — adding a new
feed is just a TOML file + one registry line, no Python changes required.

The XML parsing logic (_parse_xml) intentionally mirrors
``scraper.parse_xml_feed`` so this module stays import-safe (no dependency
on scraper.py pulling in Flask + the full KDL config at import time).

Public API
----------
    SCRAPE_CONFIG_PATH   — path to the registry TOML file
    REGISTRY             — {feed_id: config_path} loaded from the registry
    load_registry()      — (re)load the registry from disk
    get_feed_config(id)  — normalized config dict for one feed, or None
    list_feed_configs()  — {feed_id: config} for all registered feeds
    scrape_feed(id)      — fetch + parse a feed -> [{"title", "score"}, ...]
"""
import logging
import os
import xml.etree.ElementTree as ET

import requests
import tomllib

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
SCRAPE_CONFIG_PATH = os.path.join(_HERE, "rss_feeds.toml")

_DEFAULT_ITEM_LIMIT = 10


# -------------------------------------------------------------------------
# Registry loading
# -------------------------------------------------------------------------
def load_registry(config_path=None):
    """Load the registry TOML mapping feed IDs to config file paths.

    Returns an empty dict if the file is missing or unparseable.
    """
    path = config_path or SCRAPE_CONFIG_PATH
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        logger.warning("RSS registry not found at %s", path)
        return {}
    except tomllib.TOMLDecodeError as e:
        logger.error("Failed to parse RSS registry %s: %s", path, e)
        return {}


REGISTRY = load_registry()


# -------------------------------------------------------------------------
# Feed config resolution
# -------------------------------------------------------------------------
def _resolve_config_path(rel_path):
    """Resolve a feed config path relative to the registry file's directory."""
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(os.path.dirname(SCRAPE_CONFIG_PATH), rel_path)


def get_feed_config(feed_id):
    """Return the normalized config for ``feed_id``, or ``None`` if not
    registered or the config file is missing/invalid.

    Normalisation applies defaults and coerces ``score_tag`` to ``None``
    when empty (matching the ``parse_xml_feed`` convention where ``None``
    means "no score tag").
    """
    rel = REGISTRY.get(feed_id)
    if rel is None:
        return None
    path = _resolve_config_path(rel)
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        logger.error("Feed config not found for '%s' (expected %s)", feed_id, path)
        return None
    except tomllib.TOMLDecodeError as e:
        logger.error("Failed to parse feed config '%s': %s", path, e)
        return None

    cfg.setdefault("list_tag", "item")
    cfg.setdefault("title_tag", "title")
    cfg.setdefault("default_score", "New")
    cfg.setdefault("timeout", 10)
    cfg.setdefault("enabled", True)
    cfg.setdefault("item_limit", _DEFAULT_ITEM_LIMIT)
    if not cfg.get("score_tag"):
        cfg["score_tag"] = None
    if not cfg.get("user_agent"):
        cfg["user_agent"] = None
    cfg["id"] = feed_id
    cfg["path"] = path
    return cfg


def list_feed_configs():
    """Return ``{feed_id: config_dict}`` for every registered, loadable feed.

    Feeds whose config files are missing or invalid map to ``None``.
    """
    return {fid: get_feed_config(fid) for fid in REGISTRY}


# -------------------------------------------------------------------------
# XML parsing (the generic adapter engine)
# -------------------------------------------------------------------------
def _parse_xml(xml_text, list_tag, title_tag, score_tag, default_score, item_limit):
    """Extract structured items from raw XML feed text using adapter tags.

    Mirrors ``scraper.parse_xml_feed`` but is self-contained so this module
    avoids importing scraper.py (which pulls in the full Flask app + KDL
    config at import time).
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("XML parse error: %s", e)
        return []
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    items = []
    for node in root.findall(f".//{list_tag}")[:item_limit]:
        title_node = node.find(title_tag)
        title = (
            title_node.text.strip()
            if title_node is not None and title_node.text
            else "No Title"
        )
        score = default_score
        if score_tag:
            sn = node.find(score_tag)
            if sn is not None and sn.text:
                score = sn.text.strip()
        items.append({"title": title, "score": score})
    return items


# -------------------------------------------------------------------------
# Scraping
# -------------------------------------------------------------------------
def scrape_feed(feed_id):
    """Fetch and parse the RSS/Atom feed identified by ``feed_id``.

    Returns a list of ``{"title", "score"}`` dicts. On HTTP failure or XML
    parse error, returns a single placeholder item marking the feed offline.
    Returns ``[]`` if the feed is not registered or is disabled.
    """
    cfg = get_feed_config(feed_id)
    if cfg is None:
        logger.warning("Unknown or missing feed config: '%s'", feed_id)
        return []

    if not cfg.get("enabled", True):
        logger.info("Feed '%s' is disabled.", feed_id)
        return [{"title": f"Feed '{feed_id}' disabled", "score": "Offline"}]

    headers = {}
    if cfg.get("user_agent"):
        headers["User-Agent"] = cfg["user_agent"]

    try:
        resp = requests.get(cfg["url"], headers=headers, timeout=cfg.get("timeout", 10))
        resp.raise_for_status()
    except Exception as e:
        logger.error("Feed '%s' request failed: %s", feed_id, e)
        return [{"title": f"Feed '{feed_id}' offline", "score": "Offline"}]

    return _parse_xml(
        resp.text,
        cfg["list_tag"],
        cfg["title_tag"],
        cfg["score_tag"],
        cfg["default_score"],
        cfg.get("item_limit", _DEFAULT_ITEM_LIMIT),
    )


if __name__ == "__main__":
    import json
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(json.dumps(list_feed_configs(), indent=2, ensure_ascii=False))
