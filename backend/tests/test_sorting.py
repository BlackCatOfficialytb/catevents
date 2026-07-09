# tests/test_sorting.py
"""Unit tests for sorting.py — quicksort, ASCII keys, volume parsing, ranking."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sorting import (
    quicksort,
    ascii_key,
    parse_search_volume,
    compute_abnormality,
    sort_trends,
)


# ------------------------------------------------------------------ quicksort
class TestQuicksort:
    def test_sorts_ascending(self):
        assert quicksort([3, 1, 4, 1, 5, 9, 2, 6]) == [1, 1, 2, 3, 4, 5, 6, 9]

    def test_reverse(self):
        assert quicksort([3, 1, 2], reverse=True) == [3, 2, 1]

    def test_empty(self):
        assert quicksort([]) == []

    def test_single(self):
        assert quicksort([42]) == [42]

    def test_already_sorted(self):
        assert quicksort([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]

    def test_all_equal(self):
        assert quicksort([7, 7, 7, 7]) == [7, 7, 7, 7]

    def test_does_not_mutate_input(self):
        src = [3, 1, 2]
        quicksort(src)
        assert src == [3, 1, 2]

    def test_key_function(self):
        data = [{"v": 3}, {"v": 1}, {"v": 2}]
        out = quicksort(data, key=lambda d: d["v"])
        assert [d["v"] for d in out] == [1, 2, 3]

    def test_tuple_keys_lexicographic(self):
        data = [(2, "a"), (1, "b"), (1, "a")]
        assert quicksort(data, key=lambda x: x) == [(1, "a"), (1, "b"), (2, "a")]

    def test_large_batch_no_recursion_error(self):
        # Iterative quicksort must handle a large, already-sorted (worst-case-ish) input.
        data = list(range(5000))
        assert quicksort(data) == data

    def test_mixed_none_does_not_crash(self):
        # _less_eq falls back to str() comparison on TypeError.
        out = quicksort([3, None, 1])
        assert len(out) == 3


# ------------------------------------------------------------------ ascii_key
class TestAsciiKey:
    def test_spec_example(self):
        # Docstring spec: "Ab" -> (33, 34), case-folded to upper first ("AB").
        assert ascii_key("Ab", offset=32, case_insensitive=True) == (33, 34)

    def test_case_sensitive_lowercase_codes(self):
        # Without folding, 'b' (98) - 32 = 66.
        assert ascii_key("Ab", offset=32, case_insensitive=False) == (33, 66)

    def test_case_insensitive_folds(self):
        assert ascii_key("abc", case_insensitive=True) == ascii_key("ABC", case_insensitive=True)

    def test_case_sensitive_differs(self):
        assert ascii_key("abc", case_insensitive=False) != ascii_key("ABC", case_insensitive=False)

    def test_empty_string(self):
        assert ascii_key("") == ()

    def test_custom_offset(self):
        assert ascii_key("A", offset=0, case_insensitive=False) == (65,)

    def test_non_string_coerced(self):
        assert ascii_key(123, case_insensitive=False) == (ord("1") - 32, ord("2") - 32, ord("3") - 32)

    def test_ordering_matches_alpha(self):
        assert ascii_key("apple") < ascii_key("banana")


# --------------------------------------------------------- parse_search_volume
class TestParseSearchVolume:
    @pytest.mark.parametrize("raw,expected", [
        ("200,000+", 200_000),
        ("1M+", 1_000_000),
        ("20K", 20_000),
        ("1.5M", 1_500_000),
        ("2B", 2_000_000_000),
        ("500", 500),
        ("1,234", 1_234),
    ])
    def test_numeric_strings(self, raw, expected):
        assert parse_search_volume(raw) == expected

    @pytest.mark.parametrize("raw", ["New", "Offline", "▲ Popular", "", "   ", None, "abc"])
    def test_non_numeric_returns_zero(self, raw):
        assert parse_search_volume(raw) == 0

    def test_int_passthrough(self):
        assert parse_search_volume(5000) == 5000

    def test_float_truncates(self):
        assert parse_search_volume(3.9) == 3

    def test_suffix_case_insensitive(self):
        assert parse_search_volume("5k") == 5_000
        assert parse_search_volume("5m") == 5_000_000


# ------------------------------------------------------------ compute_abnormality
class TestComputeAbnormality:
    def test_all_equal_yields_zeros(self):
        assert compute_abnormality([10, 10, 10]) == [0.0, 0.0, 0.0]

    def test_empty(self):
        assert compute_abnormality([]) == []

    def test_single(self):
        assert compute_abnormality([5]) == [0.0]

    def test_spike_scores_positive(self):
        out = compute_abnormality([10, 10, 10, 100])
        assert out[3] > 0
        # below/at-mean values clamp to 0
        assert all(v == 0.0 for v in out[:3])

    def test_below_mean_clamped_to_zero(self):
        out = compute_abnormality([1, 100])
        assert out[0] == 0.0
        assert out[1] > 0

    def test_length_preserved(self):
        vols = [1, 2, 3, 4, 5, 6]
        assert len(compute_abnormality(vols)) == len(vols)


# ------------------------------------------------------------------ sort_trends
class TestSortTrends:
    def test_orders_by_volume_desc(self):
        items = [
            {"title": "low", "score": "10K"},
            {"title": "high", "score": "1M+"},
            {"title": "mid", "score": "500K"},
        ]
        out = sort_trends(items)
        assert [i["title"] for i in out] == ["high", "mid", "low"]

    def test_annotates_volume_and_abnormality(self):
        items = [{"title": "a", "score": "1M+"}]
        out = sort_trends(items)
        assert out[0]["volume"] == 1_000_000
        assert "abnormality" in out[0]

    def test_does_not_mutate_input(self):
        items = [{"title": "a", "score": "1M+"}]
        sort_trends(items)
        assert "volume" not in items[0]

    def test_title_tiebreak_ascii(self):
        # Equal volume -> tie-break by ASCII-folded title ascending.
        items = [
            {"title": "Banana", "score": "1M+"},
            {"title": "apple", "score": "1M+"},
        ]
        out = sort_trends(items)
        assert [i["title"] for i in out] == ["apple", "Banana"]

    def test_non_numeric_scores_sort_last(self):
        items = [
            {"title": "real", "score": "500K"},
            {"title": "offline", "score": "Offline"},
        ]
        out = sort_trends(items)
        assert out[0]["title"] == "real"
        assert out[-1]["title"] == "offline"

    def test_empty_list(self):
        assert sort_trends([]) == []

    def test_missing_title_key_ok(self):
        # title tie-break uses .get("title", "") so a missing title must not crash.
        out = sort_trends([{"score": "1M+"}, {"score": "500K"}])
        assert len(out) == 2
