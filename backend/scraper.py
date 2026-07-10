# scraper.py
import os
import json
import logging
import xml.etree.ElementTree as ET
import time
import threading
import traceback
from flask import Flask, jsonify, render_template, redirect
import requests
import time

# All configuration lives in config.py (loaded from config.kdl / .default).
from config import (
    HF_TOKEN, REPO_ID, HF_REMOTE_FILENAME, LOCAL_CACHE_FILE,
    SCHEDULER_ENABLED, SCHEDULER_BOOT_DELAY, SCHEDULER_INTERVAL,
    FEED_ITEM_LIMIT,
    GOOGLE_TRENDS_ENABLED, GOOGLE_TRENDS_URL_TEMPLATE, GOOGLE_TRENDS_TIMEOUT,
    GOOGLE_TRENDS_RESULT_LIMIT, GOOGLE_TRENDS_GEOS,
    REDDIT_ENABLED, REDDIT_URL, REDDIT_USER_AGENT, REDDIT_DEFAULT_SCORE, REDDIT_TIMEOUT,
    X_SCRAPING_ENABLED, X_USER_AGENT, X_QUERY, X_TIMEOUT, X_DEFAULT_SCORE,
    X_MIRROR_INSTANCES,
    CAMOUFOX,
)
from sorting import sort_trends
from ai import analyze_trends

# -------------------------------------------------------------------------
# LOGGING & CONFIGURATION
# -------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Most-recent Camoufox X/Twitter troubleshooting bundle (tracebacks + page
# diagnostics from the last failed run). Exposed via the /debug/x-troubleshooting
# endpoint so failures can be inspected without digging through logs.
_last_x_diagnostics = []


def _record_x_diagnostics(diagnostics):
    """Store the latest X-scrape troubleshooting bundle for later inspection."""
    global _last_x_diagnostics
    _last_x_diagnostics = diagnostics or []
    if _last_x_diagnostics:
        logger.info("Recorded X troubleshooting for %d mirror(s).", len(_last_x_diagnostics))


# -------------------------------------------------------------------------
# PARSERS & SCRAPERS
# -------------------------------------------------------------------------
def parse_xml_feed(xml_text, list_tag, title_tag="title", score_tag=None, default_score="New"):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as pe:
        logger.error(f"XML parsing failed: {pe}")
        return []

    for elem in root.iter():
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]

    items = []
    for item_node in root.findall(f'.//{list_tag}')[:FEED_ITEM_LIMIT]:
        title_node = item_node.find(title_tag)
        title = title_node.text.strip() if title_node is not None and title_node.text else "No Title"
        
        score = default_score
        if score_tag:
            score_node = item_node.find(score_tag)
            if score_node is not None and score_node.text:
                score = score_node.text.strip()
                
        items.append({"title": title, "score": score})
    return items

def scrape_google_trends():
    """
    Simulates a Global Feed by aggregating trends from major English-speaking 
    and international regions, eliminating duplicates.
    """
    logger.info("Generating aggregated Global Google Trends...")
    # High-volume, generally-unblocked country codes (from config).
    global_geos = GOOGLE_TRENDS_GEOS

    aggregated_trends = []
    seen_titles = set()

    for geo in global_geos:
        url = GOOGLE_TRENDS_URL_TEMPLATE.format(geo=geo)
        try:
            logger.info(f"Fetching Google Trends for region: {geo}")
            r = requests.get(url, timeout=GOOGLE_TRENDS_TIMEOUT) # Short timeout per region
            
            if r.status_code == 200:
                regional_trends = parse_xml_feed(r.text, list_tag="item", score_tag="approx_traffic")
                
                for item in regional_trends:
                    title_lower = item["title"].lower()
                    if title_lower not in seen_titles:
                        seen_titles.add(title_lower)
                        # Add region metadata for the AI's context later
                        item["region"] = geo  
                        aggregated_trends.append(item)
                        
        except Exception as e:
            logger.warning(f"Failed to fetch trends for region {geo}: {e}")
            continue
        # Prevent Rate-limited
        # time.sleep(120)

    if not aggregated_trends:
        return [{"title": "Google Trends globally offline", "score": "0"}]

    # Rank by highest search volume, then most abnormal 24h frequency (quicksort
    # over ASCII-folded keys), THEN truncate — so the top-N are the real leaders.
    ranked = sort_trends(aggregated_trends)
    final_trends = ranked[:GOOGLE_TRENDS_RESULT_LIMIT]

    logger.info(f"Successfully compiled {len(final_trends)} global trending terms.")
    return final_trends

def scrape_reddit_popular():
    if not REDDIT_ENABLED:
        logger.info("Reddit source is disabled. Skipping.")
        return [{"title": "Reddit source disabled", "score": "Offline"}]
    headers = {"User-Agent": REDDIT_USER_AGENT}
    try:
        r = requests.get(REDDIT_URL, headers=headers, timeout=REDDIT_TIMEOUT)
        r.raise_for_status()
        items = parse_xml_feed(r.text, list_tag="entry", default_score=REDDIT_DEFAULT_SCORE)
        # Rank by search volume / abnormal frequency (quicksort). Reddit's RSS
        # carries no numeric score, so ties fall back to ASCII-folded title
        # order; any numeric score present (custom feeds) is honoured.
        return sort_trends(items)
    except Exception as e:
        logger.error(f"Reddit failed: {e}")
        return [{"title": "Reddit feed offline", "score": "Offline"}]

def _scrape_x_via_camoufox(query=None):
    """
    Fetch X/Twitter search results through the Camoufox stealth browser.

    The Nitter mirrors bot-block plain `requests` (which is why the RSS path
    below keeps returning 'rate-limited'). Camoufox is a hardened Firefox with
    built-in anti-fingerprinting, so it can load the JS-rendered HTML search
    page. We extract tweet text via the Nitter `.tweet-content` selector.

    `query` overrides the static X_QUERY (e.g. an AI-derived keyword). When
    None, X_QUERY is used.

    Returns a list of ``{"title", "score"}`` dicts, or [] if nothing worked.
    """
    # Lazy import so the module still loads if Camoufox isn't installed.
    try:
        from camoufox_scraper import scrape_page
    except Exception as e:
        logger.warning(f"Camoufox unavailable, cannot scrape X: {e}")
        return []

    search_query = query or X_QUERY
    limit = CAMOUFOX.get("default_limit") or FEED_ITEM_LIMIT
    diagnostics = []  # troubleshooting bundles from every failed mirror
    for instance in X_MIRROR_INSTANCES:
        url = f"https://{instance}/search?f=tweets&q={requests.utils.quote(search_query)}"
        try:
            logger.info(f"Trying mirror via Camoufox: {instance}")
            # Nitter renders tweets inside `.timeline-item`; wait for that
            # container to appear before extracting the tweet text, otherwise
            # the query runs against an empty DOM and silently returns nothing.
            items, troubleshooting = scrape_page(
                url,
                selector=".timeline-item .tweet-content",
                wait_selector=".timeline-item",
                limit=limit,
                return_troubleshooting=True,
            )
            # Skip honeypot / 'not whitelisted' interstitials.
            if items and any("whitelist" in i["title"].lower() for i in items):
                logger.warning(f"Skipping {instance}: 'not whitelisted' honeypot page.")
                diagnostics.append({"instance": instance, "reason": "honeypot"})
                continue
            if items:
                for i in items:
                    i["score"] = X_DEFAULT_SCORE
                return items
            logger.warning(f"Mirror {instance} returned no tweets via Camoufox.")
            if troubleshooting.get("traceback"):
                logger.debug("Camoufox traceback for %s:\n%s", instance, troubleshooting["traceback"])
            diagnostics.append({"instance": instance, "troubleshooting": troubleshooting})
        except Exception as e:
            logger.warning(f"Camoufox failed for mirror {instance}: {e}")
            diagnostics.append({
                "instance": instance,
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
            })
            continue

    # All mirrors failed — stash troubleshooting for the caller/admin to inspect.
    _record_x_diagnostics(diagnostics)
    return []


def scrape_x_trends(query=None):
    """
    X / Twitter trends source.

    Strategy: try the lightweight RSS mirrors over plain `requests` first; if
    they're all bot-blocked/rate-limited, fall back to the Camoufox stealth
    browser (when enabled) which can load the JS-rendered HTML search page.

    `query` overrides the static X_QUERY — this is how AI-derived keywords
    (semantic search terms from the day's Google+Reddit trends) drive the
    search. When None, the configured X_QUERY is used.
    """
    if not X_SCRAPING_ENABLED or not X_MIRROR_INSTANCES:
        logger.info("X/Twitter source is currently disabled. Skipping.")
        return [{"title": "X/Twitter trends temporarily unavailable", "score": "Offline"}]

    search_query = query or X_QUERY
    headers = {"User-Agent": X_USER_AGENT}

    for instance in X_MIRROR_INSTANCES:
        # XCancel (xcancel.com) has no RSS endpoint — it only serves the
        # JS-rendered HTML search page, so it must be handled by the Camoufox
        # path below, never via the RSS loop. Skip it here.
        if instance.lower().startswith("xcancel"):
            logger.info("Skipping %s in RSS loop (no RSS endpoint).", instance)
            continue

        url = f"https://{instance}/search/rss?q={requests.utils.quote(search_query)}"
        try:
            logger.info(f"Trying mirror: {instance}")
            r = requests.get(url, headers=headers, timeout=X_TIMEOUT)

            if r.status_code != 200:
                logger.warning(f"Skipping {instance}: Returned status code {r.status_code}")
                continue

            if "application/xml" not in r.headers.get("Content-Type", "").lower() and "<rss" not in r.text[:200]:
                logger.warning(f"Skipping {instance}: RSS feed endpoint is disabled.")
                continue

            results = parse_xml_feed(r.text, list_tag="item", default_score=X_DEFAULT_SCORE)

            # Honeypot Check: Ensure the first item isn't their "not whitelisted" prompt
            if results and any("whitelist" in item["title"].lower() for item in results):
                logger.warning(f"Skipping {instance}: Returned a 'not whitelisted' honeypot feed.")
                continue

            if results:
                return results
        except Exception as e:
            logger.warning(f"Failed to fetch from mirror {instance}: {str(e)}")
            continue

    # RSS mirrors all failed — fall back to the Camoufox stealth browser.
    if CAMOUFOX.get("enabled"):
        logger.info("RSS mirrors exhausted; falling back to Camoufox for X/Twitter.")
        camoufox_results = _scrape_x_via_camoufox(query=search_query)
        if camoufox_results:
            return camoufox_results

    return [{"title": "X/Twitter trends rate-limited", "score": "Offline"}]

def run_all_scrapes():
    google = scrape_google_trends() if GOOGLE_TRENDS_ENABLED else [
        {"title": "Google Trends source disabled", "score": "Offline"}
    ]
    reddit = scrape_reddit_popular()

    # Flag-driven trend intelligence: summarize the day's Google+Reddit trends
    # and derive Nitter search keywords. Depending on the `ai` config block this
    # uses the AI API, classic semantic search, or neither. The top keyword
    # becomes the Nitter/Camoufox search query (replacing the static X_QUERY);
    # when no keyword is available, x.query is used.
    analysis = analyze_trends({"google": google, "reddit": reddit})
    keywords = analysis["keywords"]
    macro_trends = keywords or ["AI Tech", "Market Shifts", "Global News"]

    x_query = keywords[0] if keywords else None
    x = scrape_x_trends(query=x_query)

    return {
        "macro_trends": macro_trends,
        "ai_keywords": keywords,
        "keyword_source": analysis["source"],
        "google": google,
        "reddit": reddit,
        "x": x,
    }

def get_latest_scraped_data():
    hf_active = bool(HF_TOKEN and HF_TOKEN != "YOUR_HF_TOKEN" and REPO_ID and REPO_ID != "YOUR_USERNAME/YOUR_DATASET")
    
    if hf_active:
        try:
            from huggingface_hub import hf_hub_download
            local_path = hf_hub_download(
                repo_id=REPO_ID,
                filename=HF_REMOTE_FILENAME,
                repo_type="dataset",
                token=HF_TOKEN
            )
            with open(local_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load cache from HF: {e}")

    if os.path.exists(LOCAL_CACHE_FILE):
        try:
            with open(LOCAL_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "google": [{"title": "No scrape executed yet", "score": "-"}],
        "reddit": [{"title": "No scrape executed yet", "score": "-"}],
        "x": [{"title": "No scrape executed yet", "score": "-"}]
    }

# -------------------------------------------------------------------------
# CORE SCRAPE EXECUTION PIPELINE (Used by endpoints and background threads)
# -------------------------------------------------------------------------
def execute_scrape_and_upload():
    """Consolidated logic to run the scraper, cache locally, and upload to HF."""
    logger.info("Executing automatic scraping pipeline...")
    payload = run_all_scrapes()

    try:
        json_bytes = json.dumps(payload, indent=4).encode("utf-8")
        with open(LOCAL_CACHE_FILE, "wb") as f:
            f.write(json_bytes)
    except Exception as e:
        logger.error(f"Failed to write local cache: {e}")
        raise e

    if HF_TOKEN and HF_TOKEN != "YOUR_HF_TOKEN":
        try:
            from huggingface_hub import HfApi
            api = HfApi()
            api.upload_file(
                path_or_fileobj=json_bytes,
                path_in_repo=HF_REMOTE_FILENAME,
                repo_id=REPO_ID,
                repo_type="dataset",
                token=HF_TOKEN
            )
            logger.info("Successfully uploaded dataset update to Hugging Face.")
        except Exception as e:
            logger.error(f"Hugging Face Upload failed: {e}")
            raise e
    else:
        logger.info("HF_TOKEN not set or using placeholder. Skipped upload.")
        
    return payload

# -------------------------------------------------------------------------
# AUTOMATIC BACKGROUND SCHEDULER
# -------------------------------------------------------------------------
def start_scheduler():
    def scheduler_loop():
        # Small delay on boot to allow the server to finish printing initialization logs
        time.sleep(SCHEDULER_BOOT_DELAY)
        logger.info("Scheduler thread active. Executing first background run...")

        while True:
            try:
                execute_scrape_and_upload()
            except Exception as e:
                logger.error(f"Background scheduler task encountered an error: {e}")

            logger.info(f"Scheduled scrape complete. Next run in {SCHEDULER_INTERVAL} seconds...")
            time.sleep(SCHEDULER_INTERVAL)

    # Create thread as a daemon so it exits cleanly when the main process shuts down
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

# Only run the scheduler once (prevents double runs if debug mode is active)
if SCHEDULER_ENABLED and (os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug):
    start_scheduler()

# -------------------------------------------------------------------------
# ROUTES
# -------------------------------------------------------------------------
@app.route("/health", methods=["GET", "HEAD"])
def health_check():
    """Lightweight liveness probe for uptime monitors / load balancers.

    Supports HEAD (cheap ping — no body sent) and GET (returns JSON status).
    """
    from flask import request

    if request.method == "HEAD":
        # HEAD responses must not include a body; an empty 200 is enough.
        return ("", 200)

    return jsonify({
        "status": "ok",
        "service": "catevents-scraper",
        "x_scraping_enabled": X_SCRAPING_ENABLED
    }), 200

@app.route("/debug/x-troubleshooting", methods=["GET"])
def x_troubleshooting():
    """Return the troubleshooting bundle (tracebacks + page diagnostics) from
    the most recent failed Camoufox X/Twitter scrape."""
    return jsonify({
        "x_scraping_enabled": X_SCRAPING_ENABLED,
        "mirror_count": len(_last_x_diagnostics),
        "diagnostics": _last_x_diagnostics,
    }), 200

@app.route("/", methods=["GET"])
def index():
    return redirect("/admin")

@app.route("/admin", methods=["GET"])
def admin_dashboard():
    latest_data = get_latest_scraped_data()
    raw_json_str = json.dumps(latest_data, indent=4)
    token_active = bool(HF_TOKEN and HF_TOKEN != "YOUR_HF_TOKEN")

    return render_template(
        "admin.html",
        repo_id=REPO_ID,
        hf_token_active=token_active,
        latest_data=latest_data,
        raw_json=raw_json_str
    )

@app.route("/run-scrape", methods=["GET"])
def handle_scrape_and_upload():
    try:
        payload = execute_scrape_and_upload()
        return jsonify({"status": "Success", "message": "Scraped and updated successfully"}), 200
    except Exception as e:
        return jsonify({"status": "Failed", "error": str(e)}), 500