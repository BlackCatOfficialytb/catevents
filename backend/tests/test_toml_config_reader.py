# tests/test_toml_config_reader.py
"""Tests for toml_config_reader.py — TOML config loading and RSS scraping.

Network calls are mocked; no real HTTP is performed.
"""
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import toml_config_reader as reader


BBC_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>UK Election Update</title>
      <description>BBC breaking news</description>
    </item>
    <item>
      <title>Floods in Pakistan</title>
      <description>BBC breaking news</description>
    </item>
  </channel>
</rss>"""

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Post A</title></entry>
  <entry><title>Post B</title></entry>
</feed>"""


SAMPLE_CFG = {
    "id": "bbc",
    "url": "http://example.com/rss",
    "list_tag": "item",
    "title_tag": "title",
    "score_tag": None,
    "default_score": "BBC News",
    "timeout": 10,
    "enabled": True,
    "item_limit": 10,
    "user_agent": "TestBot/1.0",
}


# --------------------------------------------------------------- registry
class TestLoadRegistry:
    def test_loads_shipped_registry(self):
        registry = reader.load_registry()
        assert isinstance(registry, dict)
        assert "bbc" in registry
        assert registry["bbc"] == "feeds/bbc.toml"

    def test_missing_file_returns_empty(self, tmp_path):
        assert reader.load_registry(str(tmp_path / "nope.toml")) == {}

    def test_malformed_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("not = valid = toml", encoding="utf-8")
        assert reader.load_registry(str(bad)) == {}


# --------------------------------------------------------------- config resolution
class TestGetFeedConfig:
    def test_bbc_config_loaded_and_normalized(self):
        cfg = reader.get_feed_config("bbc")
        assert cfg is not None
        assert cfg["id"] == "bbc"
        assert cfg["url"] == "https://feeds.bbci.co.uk/news/rss.xml"
        assert cfg["list_tag"] == "item"
        assert cfg["title_tag"] == "title"
        assert cfg["score_tag"] is None  # empty "" -> None
        assert cfg["default_score"] == "BBC News"
        assert cfg["timeout"] == 15
        assert cfg["enabled"] is True
        assert cfg["item_limit"] == 10
        assert cfg["user_agent"] == "CatEventsBot/1.0"

    def test_unknown_feed_returns_none(self):
        assert reader.get_feed_config("nonexistent") is None

    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(reader, "SCRAPE_CONFIG_PATH", str(tmp_path / "rss_feeds.toml"))
        monkeypatch.setattr(reader, "REGISTRY", {"lost": "gone.toml"})
        assert reader.get_feed_config("lost") is None

    def test_defaults_applied(self, monkeypatch, tmp_path):
        cfg_file = tmp_path / "minimal.toml"
        cfg_file.write_text('url = "http://example.com/rss"', encoding="utf-8")
        monkeypatch.setattr(reader, "SCRAPE_CONFIG_PATH", str(tmp_path / "rss_feeds.toml"))
        monkeypatch.setattr(reader, "REGISTRY", {"minimal": cfg_file.name})
        cfg = reader.get_feed_config("minimal")
        assert cfg["list_tag"] == "item"
        assert cfg["title_tag"] == "title"
        assert cfg["default_score"] == "New"
        assert cfg["enabled"] is True
        assert cfg["timeout"] == 10
        assert cfg["item_limit"] == 10
        assert cfg["score_tag"] is None
        assert cfg["user_agent"] is None
        assert cfg["id"] == "minimal"

    def test_abs_path_in_registry(self, monkeypatch, tmp_path):
        cfg_file = tmp_path / "abs.toml"
        cfg_file.write_text('url = "http://abs.com"', encoding="utf-8")
        monkeypatch.setattr(reader, "SCRAPE_CONFIG_PATH", str(tmp_path / "rss_feeds.toml"))
        monkeypatch.setattr(reader, "REGISTRY", {"abs": str(cfg_file)})
        cfg = reader.get_feed_config("abs")
        assert cfg is not None
        assert cfg["url"] == "http://abs.com"

    def test_malformed_config_returns_none(self, monkeypatch, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("broken === toml", encoding="utf-8")
        monkeypatch.setattr(reader, "SCRAPE_CONFIG_PATH", str(tmp_path / "rss_feeds.toml"))
        monkeypatch.setattr(reader, "REGISTRY", {"bad": bad.name})
        assert reader.get_feed_config("bad") is None


# --------------------------------------------------------------- list
class TestListFeedConfigs:
    def test_returns_all_registered(self, monkeypatch, tmp_path):
        monkeypatch.setattr(reader, "SCRAPE_CONFIG_PATH", str(tmp_path / "rss_feeds.toml"))
        monkeypatch.setattr(reader, "REGISTRY", {"a": "a.toml", "b": "b.toml"})
        (tmp_path / "a.toml").write_text('url = "http://a.com/rss"', encoding="utf-8")
        configs = reader.list_feed_configs()
        assert "a" in configs
        assert configs["a"]["url"] == "http://a.com/rss"
        assert "b" in configs
        assert configs["b"] is None  # missing file -> None


# --------------------------------------------------------------- XML parsing
class TestParseXml:
    def test_parses_rss_items(self):
        items = reader._parse_xml(BBC_RSS_XML, "item", "title", None, "Default", 10)
        assert len(items) == 2
        assert items[0]["title"] == "UK Election Update"
        assert items[0]["score"] == "Default"
        assert items[1]["title"] == "Floods in Pakistan"

    def test_parses_atom_entries_strips_namespace(self):
        items = reader._parse_xml(ATOM_XML, "entry", "title", None, "New", 10)
        assert [i["title"] for i in items] == ["Post A", "Post B"]

    def test_malformed_xml_returns_empty(self):
        assert reader._parse_xml("<not valid", "item", "title", None, "New", 10) == []

    def test_missing_title_gets_placeholder(self):
        xml = "<rss><channel><item><x>5</x></item></channel></rss>"
        items = reader._parse_xml(xml, "item", "title", None, "New", 10)
        assert items[0]["title"] == "No Title"

    def test_score_tag_extracted(self):
        xml = "<rss><channel><item><title>T</title><traffic>200,000+</traffic></item></channel></rss>"
        items = reader._parse_xml(xml, "item", "title", "traffic", "New", 10)
        assert items[0]["score"] == "200,000+"

    def test_item_limit_respected(self):
        items = reader._parse_xml(BBC_RSS_XML, "item", "title", None, "Default", 1)
        assert len(items) == 1


# --------------------------------------------------------------- scrape_feed
class TestScrapeFeed:
    class _FakeResp:
        def __init__(self, text="", status_code=200):
            self.text = text
            self.status_code = status_code
            self.headers = {"Content-Type": "application/xml"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    def test_unknown_feed_returns_empty(self, monkeypatch):
        monkeypatch.setattr(reader, "get_feed_config", lambda fid: None)
        assert reader.scrape_feed("nope") == []

    def test_disabled_feed(self, monkeypatch):
        cfg = {**SAMPLE_CFG, "enabled": False}
        monkeypatch.setattr(reader, "get_feed_config", lambda fid: cfg)
        result = reader.scrape_feed("bbc")
        assert len(result) == 1
        assert "disabled" in result[0]["title"].lower()

    def test_success(self, monkeypatch):
        monkeypatch.setattr(reader, "get_feed_config", lambda fid: SAMPLE_CFG)
        monkeypatch.setattr(reader.requests, "get", lambda *a, **k: self._FakeResp(BBC_RSS_XML))
        result = reader.scrape_feed("bbc")
        assert len(result) == 2
        assert result[0]["title"] == "UK Election Update"
        assert result[0]["score"] == "BBC News"

    def test_network_error_returns_offline(self, monkeypatch):
        def boom(*a, **k):
            raise Exception("connection refused")

        monkeypatch.setattr(reader, "get_feed_config", lambda fid: SAMPLE_CFG)
        monkeypatch.setattr(reader.requests, "get", boom)
        result = reader.scrape_feed("bbc")
        assert "offline" in result[0]["title"].lower()

    def test_http_error_returns_offline(self, monkeypatch):
        monkeypatch.setattr(reader, "get_feed_config", lambda fid: SAMPLE_CFG)
        monkeypatch.setattr(reader.requests, "get", lambda *a, **k: self._FakeResp("", status_code=429))
        result = reader.scrape_feed("bbc")
        assert "offline" in result[0]["title"].lower()

    def test_user_agent_passed(self, monkeypatch):
        captured = {}

        def fake_get(url, headers=None, timeout=None, **k):
            captured["headers"] = headers
            captured["url"] = url
            return self._FakeResp(BBC_RSS_XML)

        monkeypatch.setattr(reader, "get_feed_config", lambda fid: SAMPLE_CFG)
        monkeypatch.setattr(reader.requests, "get", fake_get)
        reader.scrape_feed("bbc")
        assert captured["headers"]["User-Agent"] == "TestBot/1.0"

    def test_real_bbc_feed_end_to_end(self, monkeypatch):
        """End-to-end: shipped bbc.toml config feeds _parse_xml correctly."""
        monkeypatch.setattr(reader.requests, "get", lambda *a, **k: self._FakeResp(BBC_RSS_XML))
        result = reader.scrape_feed("bbc")
        assert len(result) == 2
        assert result[0]["title"] == "UK Election Update"
        assert result[0]["score"] == "BBC News"
