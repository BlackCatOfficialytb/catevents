# standalone_scraper.py
import logging
import xml.etree.ElementTree as ET
import requests

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
    logger.info("Scraping Google Trends RSS...")
    url = "https://trends.google.com/trending/rss?geo=US"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return parse_xml_feed(r.text, list_tag="item", score_tag="approx_traffic")
    except Exception as e:
        logger.error(f"Google Trends failed: {e}")
        return [{"title": "Google Trends temporary failure", "score": "0"}]

def scrape_reddit_popular():
    logger.info("Scraping Reddit Popular RSS...")
    url = "https://www.reddit.com/r/popular/top/.rss?sort=top&t=day&limit=10"
    headers = {
        "User-Agent": "RenderTrendBot/1.0 (contact: test_render_scraper@example.com)"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return parse_xml_feed(r.text, list_tag="entry", default_score="▲ Popular")
    except Exception as e:
        logger.error(f"Reddit failed: {e}")
        return [{"title": "Reddit feed temporarily offline", "score": "Offline"}]

def scrape_x_via_nitter():
    logger.info("Scraping X via Nitter search...")
    # Falling back on public mirrors like xcancel
    nitter_instances = [
        "xcancel.com",
        "nitter.poast.org",
        "nitter.privacyredirect.com"
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    for instance in nitter_instances:
        url = f"https://{instance}/search/rss?q=%23trending"
        try:
            logger.info(f"Trying instance: {instance}")
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200:
                results = parse_xml_feed(r.text, list_tag="item", default_score="Trending")
                if results:
                    return results
        except Exception as e:
            logger.warning(f"Nitter instance {instance} failed: {e}")
            continue 
            
    logger.error("All Nitter instances failed.")
    return [{"title": "Nitter RSS feeds temporarily rate-limited", "score": "Offline"}]

def run_all_scrapes():
    """Compiles all real-time data into a single payload."""
    return {
        "macro_trends": ["AI Tech", "Market Shifts", "Global News"],
        "google": scrape_google_trends(),
        "reddit": scrape_reddit_popular(),
        "x": scrape_x_via_nitter()
    }

if __name__ == "__main__":
    logger.info("Running standalone scrape test...")
    data = run_all_scrapes()
    import json
    print(json.dumps(data, indent=2))