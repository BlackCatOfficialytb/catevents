# scraper.py
import os
import json
import logging
import xml.etree.ElementTree as ET
import time
import threading
from flask import Flask, jsonify, render_template, redirect
import requests
import time

# -------------------------------------------------------------------------
# LOGGING & CONFIGURATION
# -------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Strip accidental surrounding quotes (common on Windows CMD)
HF_TOKEN = os.getenv("HF_TOKEN").strip('\'"') if os.getenv("HF_TOKEN") else None
REPO_ID = os.getenv("REPO_ID", "YOUR_USERNAME/YOUR_DATASET").strip('\'"')
LOCAL_CACHE_FILE = "trends_debug.json"

# X / Twitter scraping is temporarily disabled. Flip to True to re-enable.
X_SCRAPING_ENABLED = False

# Mirror endpoints used only when X scraping is re-enabled.
_x_mirror_instances = [
    "xcancel.com",
    "nitter.net",
    "nuku.trabun.org",
    "nitter.space",
    "nitter.privacyredirect.com",
    "nitter.kareem.one",
    "nitter.poast.org",
    "nitter.catsarch.com"
]

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
    for item_node in root.findall(f'.//{list_tag}')[:10]:
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
    # A cleaned list of high-volume, unblocked Google Trends country codes
    global_geos = [
        "US", "GB", "CA", "AU", "NZ", "IE",  # English-speaking
        "FR", "DE", "IT", "ES", "NL", "BE", "CH", "AT", "SE", "NO", "DK", "FI", "PL", # Europe
        "JP", "KR", "IN", "SG", "MY", "PH", "ID", "TH", "VN", "TW", "HK", # Asia (Excluding mainland China)
        "BR", "MX", "AR", "CO", "CL", "PE", # Latin America
        "ZA", "NG", "KE", "EG", # Africa
        "AE", "SA", "IL", "TR"  # Middle East
    ]
    
    aggregated_trends = []
    seen_titles = set()
    
    for geo in global_geos:
        url = f"https://trends.google.com/trending/rss?geo={geo}"
        try:
            logger.info(f"Fetching Google Trends for region: {geo}")
            r = requests.get(url, timeout=5) # Short timeout per region
            
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

    # Return top 15 unique global trends (or adjust limit as needed)
    final_trends = aggregated_trends[:15]
    
    if not final_trends:
        return [{"title": "Google Trends globally offline", "score": "0"}]
        
    logger.info(f"Successfully compiled {len(final_trends)} global trending terms.")
    return final_trends

def scrape_reddit_popular():
    url = "https://www.reddit.com/r/popular/top/.rss?sort=top&t=day&limit=10"
    headers = {"User-Agent": "RenderTrendBot/1.0 (contact: test@example.com)"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return parse_xml_feed(r.text, list_tag="entry", default_score="▲ Popular")
    except Exception as e:
        logger.error(f"Reddit failed: {e}")
        return [{"title": "Reddit feed offline", "score": "Offline"}]

def scrape_x_trends():
    """
    X / Twitter trends source. Temporarily disabled — returns a neutral
    'unavailable' placeholder so the rest of the pipeline keeps working.
    """
    if not X_SCRAPING_ENABLED or not _x_mirror_instances:
        logger.info("X/Twitter source is currently disabled. Skipping.")
        return [{"title": "X/Twitter trends temporarily unavailable", "score": "Offline"}]

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for instance in _x_mirror_instances:
        url = f"https://{instance}/search/rss?q=%23trending"
        try:
            logger.info(f"Trying mirror: {instance}")
            r = requests.get(url, headers=headers, timeout=8)

            if r.status_code != 200:
                logger.warning(f"Skipping {instance}: Returned status code {r.status_code}")
                continue

            if "application/xml" not in r.headers.get("Content-Type", "").lower() and "<rss" not in r.text[:200]:
                logger.warning(f"Skipping {instance}: RSS feed endpoint is disabled.")
                continue

            results = parse_xml_feed(r.text, list_tag="item", default_score="Trending")

            # Honeypot Check: Ensure the first item isn't their "not whitelisted" prompt
            if results and any("whitelist" in item["title"].lower() for item in results):
                logger.warning(f"Skipping {instance}: Returned a 'not whitelisted' honeypot feed.")
                continue

            if results:
                return results
        except Exception as e:
            logger.warning(f"Failed to fetch from mirror {instance}: {str(e)}")
            continue

    return [{"title": "X/Twitter trends rate-limited", "score": "Offline"}]

def run_all_scrapes():
    return {
        "macro_trends": ["AI Tech", "Market Shifts", "Global News"],
        "google": scrape_google_trends(),
        "reddit": scrape_reddit_popular(),
        "x": scrape_x_trends()
    }

def get_latest_scraped_data():
    hf_active = bool(HF_TOKEN and HF_TOKEN != "YOUR_HF_TOKEN" and REPO_ID and REPO_ID != "YOUR_USERNAME/YOUR_DATASET")
    
    if hf_active:
        try:
            from huggingface_hub import hf_hub_download
            local_path = hf_hub_download(
                repo_id=REPO_ID, 
                filename="trends.json", 
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
                path_in_repo="trends.json",
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
        time.sleep(5)
        logger.info("Scheduler thread active. Executing first background run...")
        
        while True:
            try:
                execute_scrape_and_upload()
            except Exception as e:
                logger.error(f"Background scheduler task encountered an error: {e}")
            
            logger.info("Scheduled scrape complete. Next run in 5 minutes...")
            time.sleep(300) # 300 seconds = 5 minutes

    # Create thread as a daemon so it exits cleanly when the main process shuts down
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()

# Only run the scheduler once (prevents double runs if debug mode is active)
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
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