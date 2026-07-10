# tests/test_ai.py
"""Tests for ai.py — provider calls (mocked), JSON parsing, flag-driven orchestrator.

The AI module is now a Nitter/X search-query predictor: it predicts keywords
from the day's trends. No real network calls are made; requests.post is
monkeypatched.
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
        assert ai._extract_json_object('{"keywords": ["a"]}') == {"keywords": ["a"]}

    def test_fenced(self):
        assert ai._extract_json_object('```json\n{"keywords": ["a"]}\n```') == {"keywords": ["a"]}

    def test_prose_wrapped(self):
        assert ai._extract_json_object('Here: {"keywords": ["a"]} cheers') == {"keywords": ["a"]}

    def test_nested_braces(self):
        assert ai._extract_json_object('{"keywords": [{"b": 2}]}') == {"keywords": [{"b": 2}]}

    def test_no_json(self):
        assert ai._extract_json_object("no json here") is None

    def test_empty(self):
        assert ai._extract_json_object("") is None

    def test_malformed(self):
        assert ai._extract_json_object("{not valid}") is None


class TestNormalize:
    def test_strips_hash_and_dedupes(self):
        out = ai._normalize({"keywords": ["#Tag", "tag", "X"]}, 3)
        assert out == ["Tag", "X"]

    def test_respects_count(self):
        out = ai._normalize({"keywords": ["a", "b", "c", "d"]}, 2)
        assert out == ["a", "b"]

    def test_none_when_empty(self):
        assert ai._normalize({"keywords": []}, 3) is None

    def test_non_dict(self):
        assert ai._normalize(["nope"], 3) is None

    def test_non_string_keywords_skipped(self):
        out = ai._normalize({"keywords": ["a", 5, None, "b"]}, 5)
        assert out == ["a", "b"]


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
class TestPredictKeywords:
    def _mock(self, monkeypatch, content, api_type="openai"):
        monkeypatch.setitem(ai.AI, "api_type", api_type)
        monkeypatch.setitem(ai.AI, "api_key", "k")
        monkeypatch.setitem(ai.AI, "keyword_count", 3)
        payload = _anthropic_payload(content) if api_type == "anthropic" else _openai_payload(content)
        monkeypatch.setattr(ai.requests, "post", lambda *a, **k: _FakeResp(payload))

    def test_predict_keywords(self, monkeypatch):
        self._mock(monkeypatch, '{"keywords": ["World Cup", "Messi"]}')
        assert ai.predict_keywords(TRENDS) == ["World Cup", "Messi"]

    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.setitem(ai.AI, "api_key", "")
        assert ai.predict_keywords(TRENDS) is None

    def test_unparseable_response(self, monkeypatch):
        self._mock(monkeypatch, "sorry, no json")
        assert ai.predict_keywords(TRENDS) is None


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

    def test_disabled_no_semantic_returns_none_source(self, monkeypatch):
        self._flags(monkeypatch, enabled=False, semantic_search=False)
        out = ai.analyze_trends(TRENDS)
        assert out["source"] == "none"
        assert out["keywords"] == []

    def test_ai_predicts_keywords(self, monkeypatch):
        self._flags(monkeypatch, enabled=True, use_ai_to_find_nitter_result=True,
                    semantic_search=True)
        monkeypatch.setitem(ai.AI, "api_type", "openai")
        monkeypatch.setattr(ai.requests, "post",
                            lambda *a, **k: _FakeResp(_openai_payload('{"keywords": ["World Cup"]}')))
        out = ai.analyze_trends(TRENDS)
        assert out["source"] == "ai"
        assert out["keywords"] == ["World Cup"]

    def test_ai_off_flag_uses_semantic(self, monkeypatch):
        self._flags(monkeypatch, enabled=True, use_ai_to_find_nitter_result=False,
                    semantic_search=True)
        monkeypatch.setattr(ai.requests, "post",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI call")))
        out = ai.analyze_trends(TRENDS)
        assert out["source"] == "semantic"

    def test_ai_keywords_fail_falls_back_to_semantic(self, monkeypatch):
        self._flags(monkeypatch, enabled=True, use_ai_to_find_nitter_result=True,
                    semantic_search=True)
        monkeypatch.setitem(ai.AI, "api_type", "openai")
        monkeypatch.setattr(ai.requests, "post",
                            lambda *a, **k: _FakeResp(_openai_payload("garbage")))
        out = ai.analyze_trends(TRENDS)
        assert out["source"] == "semantic"
