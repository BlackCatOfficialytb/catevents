# standalone_scraper.py
import logging
import xml.etree.ElementTree as ET
import requests

from config import (
    FEED_ITEM_LIMIT,
    GOOGLE_TRENDS_URL_TEMPLATE, GOOGLE_TRENDS_TIMEOUT,
    REDDIT_URL, REDDIT_USER_AGENT, REDDIT_DEFAULT_SCORE, REDDIT_TIMEOUT,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] in %(module)s: %(message)s")
logger = logging.getLogger(__name__)

def parse_xml_feed(xml_text, list_tag, title_tag="title", score_tag=None, default_score="New"):
    """
    Parses RSS/Atom XML safely. Strips namespaces to avoid parser 
    failures if feeds adjust their schema urls.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as pe:
        logger.error(f"XML parsing failed: {pe}")
        return []

    # Strip namespaces (e.g. {http://www.w3.org/2005/Atom}entry -> entry)
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
    logger.info("Scraping Google Trends RSS...")
    url = GOOGLE_TRENDS_URL_TEMPLATE.format(geo="US")
    try:
        r = requests.get(url, timeout=GOOGLE_TRENDS_TIMEOUT)
        r.raise_for_status()
        return parse_xml_feed(r.text, list_tag="item", score_tag="approx_traffic")
    except Exception as e:
        logger.error(f"Google Trends failed: {e}")
        return [{"title": "Google Trends temporary failure", "score": "0"}]

def scrape_reddit_popular():
    logger.info("Scraping Reddit Popular RSS...")
    headers = {"User-Agent": REDDIT_USER_AGENT}
    try:
        r = requests.get(REDDIT_URL, headers=headers, timeout=REDDIT_TIMEOUT)
        r.raise_for_status()
        return parse_xml_feed(r.text, list_tag="entry", default_score=REDDIT_DEFAULT_SCORE)
    except Exception as e:
        logger.error(f"Reddit failed: {e}")
        return [{"title": "Reddit feed temporarily offline", "score": "Offline"}]

def scrape_x_trends():
    # X / Twitter source is temporarily disabled.
    logger.info("X/Twitter source is currently disabled. Skipping.")
    return [{"title": "X/Twitter trends temporarily unavailable", "score": "Offline"}]

def run_all_scrapes():
    """Compiles all real-time data into a single payload."""
    return {
        "macro_trends": ["AI Tech", "Market Shifts", "Global News"],
        "google": scrape_google_trends(),
        "reddit": scrape_reddit_popular(),
        "x": scrape_x_trends()
    }

if __name__ == "__main__":
    logger.info("Running standalone scrape test...")
    data = run_all_scrapes()
    import json
    print(json.dumps(data, indent=2))