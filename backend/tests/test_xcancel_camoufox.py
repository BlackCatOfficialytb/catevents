# tests/test_xcancel_camoufox.py
"""Tests for the Camoufox (HTML) path when browsing xcancel.com.

XCancel has no RSS endpoint, so it must NEVER be contacted via /search/rss.
The only valid route is the Camoufox stealth browser hitting the JS-rendered
HTML search page at /search?f=tweets. These tests pin that contract.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camoufox_scraper  # noqa: F401  (ensures module imported before monkeypatch)
import scraper


# --------------------------------------------------------------------- RSS skip
class TestXCancelNotHitViaRss:
    def test_xcancel_never_queried_over_rss(self, monkeypatch):
        """The RSS loop must skip xcancel entirely (no /search/rss request)."""
        monkeypatch.setattr(scraper, "X_SCRAPING_ENABLED", True)
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES",
                            ["xcancel.com", "nitter.net"])
        monkeypatch.setattr(scraper, "CAMOUFOX", {**scraper.CAMOUFOX, "enabled": False})

        # RSS spy: a request to xcancel's RSS endpoint is a hard failure.
        def fake_get(url, **kw):
            assert "xcancel.com/search/rss" not in url, (
                f"XCancel must not be hit via RSS: {url}"
            )
            raise AssertionError("unexpected RSS url")

        monkeypatch.setattr(scraper.requests, "get", fake_get)
        # Force the RSS loop to exhaust by also failing Camoufox.
        monkeypatch.setattr(scraper, "_scrape_x_via_camoufox",
                            lambda query=None: [])
        result = scraper.scrape_x_trends(query="world cup")
        assert result[0]["score"] == "Offline"

    def test_xcancel_rss_skipped_but_other_mirrors_tried(self, monkeypatch):
        """xcancel is bypassed in the loop; a working RSS mirror still serves."""
        monkeypatch.setattr(scraper, "X_SCRAPING_ENABLED", True)
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES",
                            ["xcancel.com", "nitter.net"])
        monkeypatch.setattr(scraper, "CAMOUFOX", {**scraper.CAMOUFOX, "enabled": False})

        # A minimal valid-ish RSS document so nitter.net "succeeds" via RSS.
        rss_body = (
            '<?xml version="1.0"?><rss><channel>'
            '<item><title>#worldcup news</title></item>'
            '<item><title>match update</title></item>'
            '</channel></rss>'
        )
        seen = {}

        class _Resp:
            status_code = 200
            headers = {"Content-Type": "application/xml"}
            text = rss_body

        def fake_get(url, **kw):
            if "xcancel.com" in url:
                pytest.fail(f"XCancel must be skipped in RSS loop: {url}")
            seen["url"] = url
            return _Resp()

        monkeypatch.setattr(scraper.requests, "get", fake_get)
        result = scraper.scrape_x_trends(query="world cup")
        assert seen["url"] == (
            "https://nitter.net/search/rss?q=" + scraper.requests.utils.quote("world cup")
        )
        assert result[0]["title"] == "#worldcup news"


# ------------------------------------------------- Camoufox builds xcancel URL
class TestXCancelCamoufoxUrl:
    def _spied_scrape(self, monkeypatch, captured):
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES", ["xcancel.com"])
        monkeypatch.setattr(scraper, "CAMOUFOX",
                            {**scraper.CAMOUFOX, "enabled": True})

        def fake_page(url, selector=None, wait_selector=None, limit=None,
                      return_troubleshooting=False, **kw):
            captured["url"] = url
            captured["selector"] = selector
            captured["wait_selector"] = wait_selector
            captured["limit"] = limit
            return ([], {"ok": False}) if return_troubleshooting else []

        monkeypatch.setattr(camoufox_scraper, "scrape_page", fake_page)

    def test_camoufox_url_and_selector_for_xcancel(self, monkeypatch):
        """Camoufox must browse xcancel's HTML search page with the tweet selector."""
        captured = {}
        self._spied_scrape(monkeypatch, captured)
        scraper._scrape_x_via_camoufox(query="world cup")
        assert captured["url"] == (
            "https://xcancel.com/search?f=tweets&q="
            + scraper.requests.utils.quote("world cup")
        )
        assert captured["selector"] == ".timeline-item .tweet-content"
        assert captured["wait_selector"] == ".timeline-item"

    def test_camoufox_falls_through_from_rss(self, monkeypatch):
        """When RSS loop exhausts (only xcancel present), Camoufox is used."""
        captured = {}
        # No plain-requests RSS mirror should succeed, so xcancel (the only
        # mirror) is skipped in RSS and Camoufox is the fallthrough.
        monkeypatch.setattr(scraper, "X_SCRAPING_ENABLED", True)
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES", ["xcancel.com"])
        monkeypatch.setattr(scraper, "CAMOUFOX", {**scraper.CAMOUFOX, "enabled": True})

        def fake_get(url, **kw):
            pytest.fail(f"XCancel must not be hit via RSS: {url}")

        monkeypatch.setattr(scraper.requests, "get", fake_get)
        self._spied_scrape(monkeypatch, captured)
        scraper.scrape_x_trends(query="world cup")
        assert "xcancel.com/search?f=tweets" in captured["url"]


# ----------------------------------------------- Camoufox results flow through
class TestXCancelCamoufoxResults:
    def test_html_results_returned_with_x_score(self, monkeypatch):
        """Tweets scraped from xcancel's HTML page are returned with the X score."""
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES", ["xcancel.com"])
        monkeypatch.setattr(scraper, "CAMOUFOX", {**scraper.CAMOUFOX, "enabled": True})
        monkeypatch.setattr(scraper, "X_DEFAULT_SCORE", "Trending")

        html_tweets = [
            {"title": "Live from the World Cup", "score": "New"},
            {"title": "Breaking: election results", "score": "New"},
        ]

        def fake_page(url, **kw):
            return html_tweets, {"ok": True}

        monkeypatch.setattr(camoufox_scraper, "scrape_page", fake_page)
        out = scraper._scrape_x_via_camoufox(query="world cup")
        assert [i["title"] for i in out] == [
            "Live from the World Cup", "Breaking: election results"
        ]
        assert all(i["score"] == "Trending" for i in out)

    def test_whitelist_honeypot_skipped(self, monkeypatch):
        """A xcancel interstitial ('not whitelisted') must be rejected, not trusted."""
        monkeypatch.setattr(scraper, "X_MIRROR_INSTANCES", ["xcancel.com"])
        monkeypatch.setattr(scraper, "CAMOUFOX", {**scraper.CAMOUFOX, "enabled": True})

        def fake_page(url, **kw):
            return [{"title": "you are not whitelisted to view this", "score": "New"}], {"ok": True}

        monkeypatch.setattr(camoufox_scraper, "scrape_page", fake_page)
        out = scraper._scrape_x_via_camoufox(query="world cup")
        assert out == []  # honeypot detected -> no results accepted
