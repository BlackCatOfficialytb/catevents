# tests/test_keywords.py
"""Unit tests for keywords.py — classic (non-AI) semantic keyword extraction."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keywords import extract_keywords, _tokenize, _bigrams


class TestTokenize:
    def test_lowercases_and_filters_stopwords(self):
        toks = _tokenize("The World Cup is on today")
        assert "world" in toks and "cup" in toks
        assert "the" not in toks and "is" not in toks

    def test_drops_short_tokens(self):
        assert "on" not in _tokenize("on off")

    def test_drops_digits(self):
        # \d and _ excluded; letters only.
        toks = _tokenize("2026 World Cup")
        assert "2026" not in toks
        assert "world" in toks

    def test_unicode_letters(self):
        toks = _tokenize("copa mundial fútbol")
        assert "fútbol" in toks

    def test_empty(self):
        assert _tokenize("") == []
        assert _tokenize(None) == []


class TestBigrams:
    def test_pairs(self):
        assert _bigrams(["world", "cup", "final"]) == ["world cup", "cup final"]

    def test_single_token_no_bigram(self):
        assert _bigrams(["world"]) == []


class TestExtractKeywords:
    def test_empty_returns_empty(self):
        assert extract_keywords({"google": [], "reddit": []}) == []

    def test_missing_keys(self):
        assert extract_keywords({}) == []

    def test_cross_source_term_boosted(self):
        # "football" appears in both feeds -> should rank above single-source terms.
        trends = {
            "google": [{"title": "football match"}, {"title": "cricket score"}],
            "reddit": [{"title": "football fans"}, {"title": "tennis open"}],
        }
        out = extract_keywords(trends, limit=1)
        assert out == ["football"]

    def test_recurring_bigram_surfaces(self):
        trends = {
            "google": [{"title": "World Cup final"}, {"title": "World Cup draw"}],
            "reddit": [{"title": "World Cup mania"}],
        }
        out = extract_keywords(trends, limit=3)
        assert "world cup" in out

    def test_respects_limit(self):
        trends = {
            "google": [{"title": f"topic{i} alpha beta gamma delta"} for i in range(5)],
            "reddit": [{"title": "alpha beta gamma delta epsilon"}],
        }
        assert len(extract_keywords(trends, limit=2)) == 2

    def test_no_crash_on_none_titles(self):
        trends = {"google": [{"title": None}, {}], "reddit": [{"title": "real story here"}]}
        out = extract_keywords(trends, limit=3)
        assert isinstance(out, list)
