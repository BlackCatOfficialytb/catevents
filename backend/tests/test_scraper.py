# tests/test_scraper.py
"""Tests for scraper.py — XML feed parsing, scrape orchestration, Flask routes.

Network calls are mocked; no real HTTP is performed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper


# ----------------------------------------------------------- parse_xml_feed
RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>First Trend</title>
      <approx_traffic>200,000+</approx_traffic>
    </item>
    <item>
      <title>Second Trend</title>
      <approx_traffic>50,000+</approx_traffic>
    </item>
  </channel>
</rss>"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Post A</title></entry>
  <entry><title>Post B</title></entry>
</feed>"""


class TestParseXmlFeed:
    def test_parses_rss_items(self):
        items = scraper.parse_xml_feed(RSS_SAMPLE, list_tag="item", score_tag="approx_traffic")
        assert len(items) == 2
        assert items[0]["title"] == "First Trend"
        assert items[0]["score"] == "200,000+"

    def test_parses_atom_entries_with_default_score(self):
        items = scraper.parse_xml_feed(ATOM_SAMPLE, list_tag="entry", default_score="New")
        assert [i["title"] for i in items] == ["Post A", "Post B"]
        assert all(i["score"] == "New" for i in items)

    def test_strips_namespaces(self):
        # Atom sample has a default xmlns; parser strips '{...}' from tags.
        items = scraper.parse_xml_feed(ATOM_SAMPLE, list_tag="entry")
        assert len(items) == 2

    def test_malformed_xml_returns_empty(self):
        assert scraper.parse_xml_feed("<not valid", list_tag="item") == []

    def test_missing_title_gets_placeholder(self):
        xml = "<rss><channel><item><approx_traffic>5</approx_traffic></item></channel></rss>"
        items = scraper.parse_xml_feed(xml, list_tag="item", score_tag="approx_traffic")
        assert items[0]["title"] == "No Title"

    def test_respects_feed_item_limit(self, monkeypatch):
        monkeypatch.setattr(scraper, "FEED_ITEM_LIMIT", 1)
        items = scraper.parse_xml_feed(RSS_SAMPLE, list_tag="item")
        assert len(items) == 1


# --------------------------------------------------- scrape source functions
class _FakeResp:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/xml"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class TestScrapeGoogleTrends:
    def test_aggregates_and_dedupes(self, monkeypatch):
        monkeypatch.setattr(scraper, "GOOGLE_TRENDS_GEOS", ["US", "GB"])
        monkeypatch.setattr(scraper, "GOOGLE_TRENDS_RESULT_LIMIT", 10)
        monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: _FakeResp(RSS_SAMPLE))
        out = scraper.scrape_google_trends()
        titles = [i["title"] for i in out]
        # Two regions return identical items -> deduped to 2 unique titles.
        assert sorted(titles) == ["First Trend", "Second Trend"]

    def test_all_offline_returns_placeholder(self, monkeypatch):
        monkeypatch.setattr(scraper, "GOOGLE_TRENDS_GEOS", ["US"])

        def boom(*a, **k):
            raise Exception("network down")

        monkeypatch.setattr(scraper.requests, "get", boom)
        out = scraper.scrape_google_trends()
        assert "offline" in out[0]["title"].lower()


class TestScrapeReddit:
    def test_disabled_returns_placeholder(self, monkeypatch):
        monkeypatch.setattr(scraper, "REDDIT_ENABLED", False)
        out = scraper.scrape_reddit_popular()
        assert "disabled" in out[0]["title"].lower()

    def test_parses_and_sorts(self, monkeypatch):
        monkeypatch.setattr(scraper, "REDDIT_ENABLED", True)
        monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: _FakeResp(ATOM_SAMPLE))
        out = scraper.scrape_reddit_popular()
        assert len(out) == 2
        assert all("volume" in i for i in out)  # sort_trends annotated them

    def test_network_error_returns_offline(self, monkeypatch):
        monkeypatch.setattr(scraper, "REDDIT_ENABLED", True)

        def boom(*a, **k):
            raise Exception("down")

        monkeypatch.setattr(scraper.requests, "get", boom)
        out = scraper.scrape_reddit_popular()
        assert "offline" in out[0]["title"].lower()


class TestScrapeX:
    def test_disabled_returns_placeholder(self, monkeypatch):
        monkeypatch.setattr(scraper, "X_SCRAPING_ENABLED", False)
        out = scraper.scrape_x_trends()
        assert "unavailable" in out[0]["title"].lower()

    def test_rss_success(self, monkeypatch):
        monkeypatch.setattr(scraper, "X_SCRAPING_ENABLED", True)
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES", ["nitter.example"])
        monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: _FakeResp(RSS_SAMPLE))
        out = scraper.scrape_x_trends()
        assert out[0]["title"] == "First Trend"

    def test_all_mirrors_fail_no_camoufox(self, monkeypatch):
        monkeypatch.setattr(scraper, "X_SCRAPING_ENABLED", True)
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES", ["nitter.example"])
        monkeypatch.setattr(scraper.requests, "get",
                            lambda *a, **k: _FakeResp("", status_code=429))
        monkeypatch.setitem(scraper.CAMOUFOX, "enabled", False)
        out = scraper.scrape_x_trends()
        assert "rate-limited" in out[0]["title"].lower()

    def test_honeypot_whitelist_skipped(self, monkeypatch):
        monkeypatch.setattr(scraper, "X_SCRAPING_ENABLED", True)
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES", ["nitter.example"])
        honeypot = ("<rss><channel><item><title>You are not whitelisted</title>"
                    "</item></channel></rss>")
        monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: _FakeResp(honeypot))
        monkeypatch.setitem(scraper.CAMOUFOX, "enabled", False)
        out = scraper.scrape_x_trends()
        # Honeypot skipped -> falls through to rate-limited placeholder.
        assert "rate-limited" in out[0]["title"].lower()


# --------------------------------------------------------------- Flask routes
@pytest.fixture
def client():
    scraper.app.config["TESTING"] = True
    with scraper.app.test_client() as c:
        yield c


class TestRoutes:
    def test_health_get(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["service"] == "catevents-scraper"

    def test_health_head(self, client):
        resp = client.head("/health")
        assert resp.status_code == 200
        assert resp.data == b""

    def test_index_redirects_to_admin(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/admin" in resp.headers["Location"]

    def test_x_troubleshooting_endpoint(self, client):
        resp = client.get("/debug/x-troubleshooting")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "diagnostics" in data
        assert "mirror_count" in data

    def test_run_scrape_success(self, client, monkeypatch):
        monkeypatch.setattr(scraper, "execute_scrape_and_upload", lambda: {"ok": True})
        resp = client.get("/run-scrape")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "Success"

    def test_run_scrape_failure(self, client, monkeypatch):
        def boom():
            raise Exception("kaboom")

        monkeypatch.setattr(scraper, "execute_scrape_and_upload", boom)
        resp = client.get("/run-scrape")
        assert resp.status_code == 500
        assert resp.get_json()["status"] == "Failed"


# ------------------------------------------------- run_all_scrapes structure
class TestRunAllScrapes:
    def test_returns_expected_keys(self, monkeypatch):
        monkeypatch.setattr(scraper, "GOOGLE_TRENDS_ENABLED", False)
        monkeypatch.setattr(scraper, "scrape_reddit_popular", lambda: [{"title": "r", "score": "1"}])
        monkeypatch.setattr(scraper, "scrape_x_trends", lambda query=None: [{"title": "x", "score": "1"}])
        # No AI, no semantic keywords -> deterministic empty analysis.
        monkeypatch.setattr(scraper, "analyze_trends",
                            lambda t: {"summary": "", "keywords": [], "source": "none"})
        out = scraper.run_all_scrapes()
        assert set(out) == {"macro_trends", "ai_summary", "ai_keywords",
                            "keyword_source", "google", "reddit", "x"}
        assert "disabled" in out["google"][0]["title"].lower()

    def test_ai_keyword_drives_x_query(self, monkeypatch):
        monkeypatch.setattr(scraper, "GOOGLE_TRENDS_ENABLED", False)
        monkeypatch.setattr(scraper, "scrape_reddit_popular", lambda: [{"title": "r", "score": "1"}])
        monkeypatch.setattr(scraper, "analyze_trends",
                            lambda t: {"summary": "S", "keywords": ["World Cup", "Messi"],
                                       "source": "ai"})
        captured = {}

        def fake_x(query=None):
            captured["query"] = query
            return [{"title": "x", "score": "1"}]

        monkeypatch.setattr(scraper, "scrape_x_trends", fake_x)
        out = scraper.run_all_scrapes()
        # Top AI keyword becomes the Nitter query; summary/keywords in payload.
        assert captured["query"] == "World Cup"
        assert out["ai_summary"] == "S"
        assert out["ai_keywords"] == ["World Cup", "Messi"]
        assert out["macro_trends"] == ["World Cup", "Messi"]
        assert out["keyword_source"] == "ai"
