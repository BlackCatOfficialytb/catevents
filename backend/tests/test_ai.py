# tests/test_ai.py
"""Tests for ai.py — provider calls (mocked), JSON parsing, flag-driven orchestrator.

No real network calls are made; requests.post is monkeypatched.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai


TRENDS = {
    "google": [{"title": "World Cup final"}, {"title": "Messi record"}],
    "reddit": [{"title": "Egypt coach vs refs"}],
}


# ---------------------------------------------------------- fake HTTP layer
class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _openai_payload(content):
    return {"choices": [{"message": {"content": content}}]}


def _anthropic_payload(text):
    return {"content": [{"type": "text", "text": text}]}


# ------------------------------------------------------------- JSON parsing
class TestExtractJsonObject:
    def test_plain(self):
        assert ai._extract_json_object('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert ai._extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_wrapped(self):
        assert ai._extract_json_object('Here you go: {"a": 1} cheers') == {"a": 1}

    def test_nested_braces(self):
        assert ai._extract_json_object('{"a": {"b": 2}}') == {"a": {"b": 2}}

    def test_no_json(self):
        assert ai._extract_json_object("no json here") is None

    def test_empty(self):
        assert ai._extract_json_object("") is None

    def test_malformed(self):
        assert ai._extract_json_object("{not valid}") is None


class TestNormalize:
    def test_strips_hash_and_dedupes(self):
        out = ai._normalize({"summary": " s ", "keywords": ["#Tag", "tag", "X"]}, 3)
        assert out["summary"] == "s"
        assert out["keywords"] == ["Tag", "X"]

    def test_respects_count(self):
        out = ai._normalize({"summary": "", "keywords": ["a", "b", "c", "d"]}, 2)
        assert out["keywords"] == ["a", "b"]

    def test_none_when_both_empty(self):
        assert ai._normalize({"summary": "", "keywords": []}, 3) is None

    def test_non_dict(self):
        assert ai._normalize(["nope"], 3) is None

    def test_non_string_keywords_skipped(self):
        out = ai._normalize({"summary": "s", "keywords": ["a", 5, None, "b"]}, 5)
        assert out["keywords"] == ["a", "b"]


# ------------------------------------------------------- provider dispatch
class TestChatProviders:
    def test_openai_path(self, monkeypatch):
        monkeypatch.setitem(ai.AI, "api_type", "openai")
        monkeypatch.setitem(ai.AI, "api_key", "k")
        captured = {}

        def fake_post(url, **kw):
            captured["url"] = url
            captured["headers"] = kw["headers"]
            return _FakeResp(_openai_payload("hello"))

        monkeypatch.setattr(ai.requests, "post", fake_post)
        assert ai._chat("sys", "usr") == "hello"
        assert captured["url"].endswith("/chat/completions")
        assert "Authorization" in captured["headers"]

    def test_anthropic_path(self, monkeypatch):
        monkeypatch.setitem(ai.AI, "api_type", "anthropic")
        monkeypatch.setitem(ai.AI, "api_key", "k")
        captured = {}

        def fake_post(url, **kw):
            captured["url"] = url
            captured["headers"] = kw["headers"]
            return _FakeResp(_anthropic_payload("hi there"))

        monkeypatch.setattr(ai.requests, "post", fake_post)
        assert ai._chat("sys", "usr") == "hi there"
        assert captured["url"].endswith("/v1/messages")
        assert "x-api-key" in captured["headers"]

    def test_chat_returns_none_on_error(self, monkeypatch):
        monkeypatch.setitem(ai.AI, "api_type", "openai")
        monkeypatch.setitem(ai.AI, "api_key", "k")

        def boom(*a, **k):
            raise Exception("network")

        monkeypatch.setattr(ai.requests, "post", boom)
        assert ai._chat("s", "u") is None


# ---------------------------------------------------- task-level functions
class TestTaskFunctions:
    def _mock(self, monkeypatch, content, api_type="openai"):
        monkeypatch.setitem(ai.AI, "api_type", api_type)
        monkeypatch.setitem(ai.AI, "api_key", "k")
        monkeypatch.setitem(ai.AI, "keyword_count", 3)
        payload = _anthropic_payload(content) if api_type == "anthropic" else _openai_payload(content)
        monkeypatch.setattr(ai.requests, "post", lambda *a, **k: _FakeResp(payload))

    def test_summarize_and_extract(self, monkeypatch):
        self._mock(monkeypatch, '{"summary": "big day", "keywords": ["World Cup"]}')
        out = ai.summarize_and_extract(TRENDS)
        assert out["summary"] == "big day"
        assert out["keywords"] == ["World Cup"]

    def test_ai_summarize_only(self, monkeypatch):
        self._mock(monkeypatch, '{"summary": "just a summary"}')
        assert ai.ai_summarize(TRENDS) == "just a summary"

    def test_ai_find_keywords_only(self, monkeypatch):
        self._mock(monkeypatch, '{"keywords": ["A", "B"]}')
        assert ai.ai_find_keywords(TRENDS) == ["A", "B"]

    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.setitem(ai.AI, "api_key", "")
        assert ai.summarize_and_extract(TRENDS) is None
        assert ai.ai_summarize(TRENDS) is None
        assert ai.ai_find_keywords(TRENDS) is None

    def test_unparseable_response(self, monkeypatch):
        self._mock(monkeypatch, "sorry, no json")
        assert ai.summarize_and_extract(TRENDS) is None


# ------------------------------------------------ flag-driven orchestrator
class TestAnalyzeTrends:
    def _flags(self, monkeypatch, **flags):
        for k, v in flags.items():
            monkeypatch.setitem(ai.AI, k, v)
        monkeypatch.setitem(ai.AI, "api_key", "k")
        monkeypatch.setitem(ai.AI, "keyword_count", 3)

    def test_disabled_uses_semantic(self, monkeypatch):
        self._flags(monkeypatch, enabled=False, semantic_search=True)
        # no requests.post allowed
        monkeypatch.setattr(ai.requests, "post",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI call")))
        out = ai.analyze_trends(TRENDS)
        assert out["source"] == "semantic"
        assert out["keywords"]  # classic extraction found something
        assert out["summary"] == ""

    def test_disabled_no_semantic_returns_none_source(self, monkeypatch):
        self._flags(monkeypatch, enabled=False, semantic_search=False)
        out = ai.analyze_trends(TRENDS)
        assert out["source"] == "none"
        assert out["keywords"] == []

    def test_both_ai_tasks_combined_call(self, monkeypatch):
        self._flags(monkeypatch, enabled=True, ai_summarize=True,
                    use_ai_to_find_nitter_result=True, semantic_search=True)
        calls = {"n": 0}

        def fake_post(*a, **k):
            calls["n"] += 1
            return _FakeResp(_openai_payload('{"summary": "S", "keywords": ["World Cup"]}'))

        monkeypatch.setitem(ai.AI, "api_type", "openai")
        monkeypatch.setattr(ai.requests, "post", fake_post)
        out = ai.analyze_trends(TRENDS)
        assert out["source"] == "ai"
        assert out["summary"] == "S"
        assert out["keywords"] == ["World Cup"]
        assert calls["n"] == 1  # single combined call, not two

    def test_both_off_warns_and_falls_back(self, monkeypatch, caplog):
        self._flags(monkeypatch, enabled=True, ai_summarize=False,
                    use_ai_to_find_nitter_result=False, semantic_search=True)
        monkeypatch.setattr(ai.requests, "post",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI call")))
        import logging
        with caplog.at_level(logging.WARNING):
            out = ai.analyze_trends(TRENDS)
        assert "nothing" in caplog.text.lower()
        assert out["source"] == "semantic"

    def test_ai_keywords_fail_falls_back_to_semantic(self, monkeypatch):
        self._flags(monkeypatch, enabled=True, ai_summarize=False,
                    use_ai_to_find_nitter_result=True, semantic_search=True)
        monkeypatch.setitem(ai.AI, "api_type", "openai")
        monkeypatch.setattr(ai.requests, "post",
                            lambda *a, **k: _FakeResp(_openai_payload("garbage")))
        out = ai.analyze_trends(TRENDS)
        assert out["source"] == "semantic"

    def test_summary_only(self, monkeypatch):
        self._flags(monkeypatch, enabled=True, ai_summarize=True,
                    use_ai_to_find_nitter_result=False, semantic_search=False)
        monkeypatch.setitem(ai.AI, "api_type", "openai")
        monkeypatch.setattr(ai.requests, "post",
                            lambda *a, **k: _FakeResp(_openai_payload('{"summary": "only summary"}')))
        out = ai.analyze_trends(TRENDS)
        assert out["summary"] == "only summary"
        assert out["keywords"] == []
        assert out["source"] == "none"
