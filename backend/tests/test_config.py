# tests/test_config.py
"""Unit tests for config.py — the minimal KDL parser and typed accessors."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from config import _parse_kdl, _KDLParser


# --------------------------------------------------------------- KDL parsing
class TestKDLParser:
    def test_simple_node_with_arg(self):
        out = _parse_kdl('name "value"')
        assert out["name"]["args"] == ["value"]

    def test_bare_string_arg(self):
        out = _parse_kdl("host localhost")
        assert out["host"]["args"] == ["localhost"]

    def test_integer_coercion(self):
        out = _parse_kdl("port 8080")
        assert out["port"]["args"] == [8080]
        assert isinstance(out["port"]["args"][0], int)

    def test_float_coercion(self):
        out = _parse_kdl("ratio 1.5")
        assert out["ratio"]["args"] == [1.5]

    def test_underscore_in_number(self):
        out = _parse_kdl("big 1_000_000")
        assert out["big"]["args"] == [1_000_000]

    def test_boolean_true(self):
        out = _parse_kdl("enabled #true")
        assert out["enabled"]["args"] == [True]

    def test_boolean_false(self):
        out = _parse_kdl("enabled #false")
        assert out["enabled"]["args"] == [False]

    def test_null(self):
        out = _parse_kdl("thing #null")
        assert out["thing"]["args"] == [None]

    def test_properties(self):
        out = _parse_kdl('server host=localhost port=9000')
        assert out["server"]["props"]["host"] == "localhost"
        assert out["server"]["props"]["port"] == 9000

    def test_child_block(self):
        out = _parse_kdl("""
        parent {
            child "v"
        }
        """)
        assert out["parent"]["children"]["child"]["args"] == ["v"]

    def test_multiple_args(self):
        out = _parse_kdl('geos "US" "GB" "CA"')
        assert out["geos"]["args"] == ["US", "GB", "CA"]

    def test_line_comment(self):
        out = _parse_kdl("""
        // this is a comment
        port 80
        """)
        assert out["port"]["args"] == [80]

    def test_block_comment(self):
        out = _parse_kdl("port /* inline */ 80")
        assert out["port"]["args"] == [80]

    def test_nested_block_comment(self):
        out = _parse_kdl("port /* outer /* inner */ still */ 80")
        assert out["port"]["args"] == [80]

    def test_semicolon_terminator(self):
        out = _parse_kdl("a 1; b 2")
        assert out["a"]["args"] == [1]
        assert out["b"]["args"] == [2]

    def test_escaped_string(self):
        out = _parse_kdl(r'msg "line1\nline2"')
        assert out["msg"]["args"] == ["line1\nline2"]

    def test_unicode_escape(self):
        out = _parse_kdl(r'ch "\u{41}"')
        assert out["ch"]["args"] == ["A"]

    def test_line_continuation(self):
        out = _parse_kdl('items "a" \\\n  "b"')
        assert out["items"]["args"] == ["a", "b"]

    def test_unterminated_string_raises(self):
        with pytest.raises(ValueError):
            _parse_kdl('name "unterminated')

    def test_unterminated_block_raises(self):
        with pytest.raises(ValueError):
            _parse_kdl("parent { child 1")

    def test_empty_document(self):
        assert _parse_kdl("") == {}

    def test_quoted_node_name(self):
        out = _parse_kdl('"quoted name" 1')
        assert out["quoted name"]["args"] == [1]


# ----------------------------------------------------- typed accessor helpers
class TestTypedAccessors:
    def test_bool_from_string(self):
        assert config._bool.__module__  # accessor exists
        # direct behavior through _raw is env/file bound; test the coercion paths
        # via a temporary monkey of _CFG below instead.

    def test_str_coerces_non_string(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {"x": {"args": [123], "props": {}, "children": {}}})
        assert config._str("x") == "123"

    def test_int_parses(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {"x": {"args": ["42"], "props": {}, "children": {}}})
        assert config._int("x") == 42

    def test_int_rejects_non_int(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {"x": {"args": ["abc"], "props": {}, "children": {}}})
        with pytest.raises(config.ConfigError):
            config._int("x")

    @pytest.mark.parametrize("val,expected", [
        ("true", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("0", False), ("no", False), ("", False),
    ])
    def test_bool_string_variants(self, monkeypatch, val, expected):
        monkeypatch.setattr(config, "_CFG", {"x": {"args": [val], "props": {}, "children": {}}})
        assert config._bool("x") is expected

    def test_bool_native_passthrough(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {"x": {"args": [True], "props": {}, "children": {}}})
        assert config._bool("x") is True

    def test_missing_required_raises(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {})
        monkeypatch.delenv("NOPE_ENV", raising=False)
        with pytest.raises(config.ConfigError):
            config._str("does.not.exist", env="NOPE_ENV")

    def test_default_used_when_absent(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {})
        monkeypatch.delenv("NOPE_ENV", raising=False)
        assert config._str("nope", env="NOPE_ENV", default="fallback") == "fallback"

    def test_env_overrides_when_file_absent(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {})
        monkeypatch.setenv("MY_ENV_VAR", "from_env")
        assert config._str("nope", env="MY_ENV_VAR") == "from_env"

    def test_file_wins_over_env(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {"x": {"args": ["from_file"], "props": {}, "children": {}}})
        monkeypatch.setenv("MY_ENV_VAR", "from_env")
        assert config._str("x", env="MY_ENV_VAR") == "from_file"

    def test_list_from_env_comma_separated(self, monkeypatch):
        monkeypatch.setattr(config, "_CFG", {})
        monkeypatch.setenv("MY_LIST", "a, b,c")
        assert config._list("nope", env="MY_LIST") == ["a", "b", "c"]

    def test_list_nested_path(self, monkeypatch):
        cfg = {"parent": {"args": [], "props": {}, "children": {
            "kids": {"args": ["x", "y"], "props": {}, "children": {}}
        }}}
        monkeypatch.setattr(config, "_CFG", cfg)
        assert config._list("parent.kids") == ["x", "y"]


# ------------------------------------------------------ real loaded config
class TestLoadedConfig:
    """Sanity checks that the shipped config.kdl.default loads sensibly."""

    def test_geos_is_nonempty_list(self):
        assert isinstance(config.GOOGLE_TRENDS_GEOS, list)
        assert len(config.GOOGLE_TRENDS_GEOS) > 0

    def test_sorting_block_shape(self):
        assert set(config.SORTING) >= {"enabled", "order", "ascii_offset"}

    def test_camoufox_block_shape(self):
        assert "enabled" in config.CAMOUFOX
        assert "timeout_ms" in config.CAMOUFOX

    def test_server_port_is_int(self):
        assert isinstance(config.SERVER_PORT, int)
