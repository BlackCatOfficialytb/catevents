# tests/test_camoufox_scraper.py
"""Tests for camoufox_scraper.py helpers that don't require the browser binary."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camoufox_scraper as cf


# ------------------------------------------------------- _build_launch_kwargs
class TestBuildLaunchKwargs:
    def test_includes_headless_and_humanize(self):
        kwargs = cf._build_launch_kwargs()
        assert "headless" in kwargs
        assert "humanize" in kwargs

    def test_no_proxy_when_empty(self, monkeypatch):
        monkeypatch.setitem(cf.CAMOUFOX, "proxy", "")
        assert "proxy" not in cf._build_launch_kwargs()

    def test_proxy_included_when_set(self, monkeypatch):
        monkeypatch.setitem(cf.CAMOUFOX, "proxy", "http://127.0.0.1:8080")
        kwargs = cf._build_launch_kwargs()
        assert kwargs["proxy"] == {"server": "http://127.0.0.1:8080"}


# -------------------------------------------------------------------- _extract
class _FakeEl:
    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class _FakePage:
    def __init__(self, texts):
        self._texts = texts

    def query_selector_all(self, selector):
        return [_FakeEl(t) for t in self._texts]


class TestExtract:
    def test_extracts_titles(self):
        page = _FakePage(["Tweet one", "Tweet two"])
        items = cf._extract(page, ".sel", limit=10)
        assert [i["title"] for i in items] == ["Tweet one", "Tweet two"]
        assert all(i["score"] == "New" for i in items)

    def test_dedupes_case_insensitive(self):
        page = _FakePage(["Same", "same", "SAME", "Other"])
        items = cf._extract(page, ".sel", limit=10)
        assert len(items) == 2

    def test_skips_empty_and_whitespace(self):
        page = _FakePage(["", "   ", "Real"])
        items = cf._extract(page, ".sel", limit=10)
        assert [i["title"] for i in items] == ["Real"]

    def test_respects_limit(self):
        page = _FakePage(["a", "b", "c", "d"])
        items = cf._extract(page, ".sel", limit=2)
        assert len(items) == 2

    def test_none_inner_text_handled(self):
        page = _FakePage([None, "Real"])
        items = cf._extract(page, ".sel", limit=10)
        assert [i["title"] for i in items] == ["Real"]


# ------------------------------------------------------------ _page_diagnostics
class _DiagPage:
    url = "https://example.com/x"

    def title(self):
        return "Example"

    def inner_text(self, sel):
        return "body text here"

    def query_selector(self, sel):
        return None


class TestPageDiagnostics:
    def test_captures_url_and_title(self):
        diag = cf._page_diagnostics(_DiagPage())
        assert diag["url"] == "https://example.com/x"
        assert diag["title"] == "Example"

    def test_body_snippet_truncated(self):
        class BigBody(_DiagPage):
            def inner_text(self, sel):
                return "x" * 1000

        diag = cf._page_diagnostics(BigBody())
        assert len(diag["body_snippet"]) == 500
        assert diag["body_length"] == 1000

    def test_never_raises_on_broken_page(self):
        class Broken:
            @property
            def url(self):
                raise RuntimeError("boom")

            def title(self):
                raise RuntimeError("boom")

            def inner_text(self, sel):
                raise RuntimeError("boom")

            def query_selector(self, sel):
                raise RuntimeError("boom")

        diag = cf._page_diagnostics(Broken())
        assert diag["url"] is None
        assert diag["title"] is None


# ----------------------------------------------------------- _page_looks_blocked
class TestPageLooksBlocked:
    def test_returns_none_when_no_panel(self):
        class NoPanel:
            def query_selector(self, sel):
                return None

        assert cf._page_looks_blocked(NoPanel()) is None

    def test_returns_message_when_error_panel(self):
        class Panel:
            def query_selector(self, sel):
                return _FakeEl("Instance has been rate limited")

        assert "rate limited" in cf._page_looks_blocked(Panel()).lower()


# --------------------------------------------------- CamoufoxScrapeError shape
class TestCamoufoxScrapeError:
    def test_carries_troubleshooting(self):
        err = cf.CamoufoxScrapeError("fail", {"ok": False})
        assert err.troubleshooting == {"ok": False}
        assert str(err) == "fail"


# ---------------------------------------------------------- scrape_page (mocked)
class TestScrapePageMocked:
    def test_returns_items_from_run_scrape(self, monkeypatch):
        monkeypatch.setattr(cf, "_run_scrape",
                            lambda *a, **k: ([{"title": "t", "score": "New"}], {"ok": True}))
        out = cf.scrape_page("http://x", selector=".s")
        assert out == [{"title": "t", "score": "New"}]

    def test_returns_troubleshooting_tuple(self, monkeypatch):
        monkeypatch.setattr(cf, "_run_scrape",
                            lambda *a, **k: ([], {"ok": False, "error": "nope"}))
        items, ts = cf.scrape_page("http://x", return_troubleshooting=True)
        assert items == []
        assert ts["error"] == "nope"

    def test_strict_raises_on_empty(self, monkeypatch):
        monkeypatch.setattr(cf, "_run_scrape",
                            lambda *a, **k: ([], {"ok": False, "error": "no results"}))
        with pytest.raises(cf.CamoufoxScrapeError) as exc:
            cf.scrape_page_strict("http://x")
        assert exc.value.troubleshooting["error"] == "no results"

    def test_strict_returns_items_on_success(self, monkeypatch):
        monkeypatch.setattr(cf, "_run_scrape",
                            lambda *a, **k: ([{"title": "t", "score": "New"}], {"ok": True}))
        assert cf.scrape_page_strict("http://x") == [{"title": "t", "score": "New"}]
