# config.py
"""
Central configuration for catevents.

This module reads a KDL config file and translates it into plain Python
variables that the rest of the codebase imports directly, e.g.::

    from config import REPO_ID, GOOGLE_TRENDS_GEOS, CAMOUFOX

Resolution order for the config file:
    1. $CATEVENTS_CONFIG            (explicit path override)
    2. ./config.kdl                 (user copy — edit this)
    3. ./config.kdl.default         (checked-in template — fallback)

Only ONE secret is deliberately kept out of the KDL files: HF_TOKEN. It is a
credential and is read exclusively from the environment.

The KDL parser below is intentionally small and dependency-free. It supports
the subset of KDL this project uses: nodes, child blocks, string / number /
boolean (#true/#false) / null (#null) arguments, key=value properties, '//'
and '/* */' comments, ';' node terminators, and backslash line continuations.
"""
import os
import re
import logging

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# Minimal KDL parser
# -------------------------------------------------------------------------
_WHITESPACE = " \t\r﻿"
_BARE_TERMINATORS = set(" \t\r\n{};=/\"") | {""}


class KDLNode:
    """A parsed KDL node: a name, positional args, key=val props, and children."""

    __slots__ = ("name", "args", "props", "children")

    def __init__(self, name):
        self.name = name
        self.args = []            # list of scalar values
        self.props = {}           # dict of str -> scalar value
        self.children = []        # list of KDLNode


class _KDLParser:
    def __init__(self, text):
        self.s = text
        self.i = 0
        self.n = len(text)

    # -- low-level helpers --------------------------------------------------
    def _error(self, msg):
        # Compute a rough line number for friendlier diagnostics.
        line = self.s.count("\n", 0, self.i) + 1
        raise ValueError(f"KDL parse error (line {line}): {msg}")

    def _peek(self):
        return self.s[self.i] if self.i < self.n else ""

    def _skip_line_ws(self):
        """Skip spaces/tabs, comments, and backslash line-continuations.

        Does NOT cross bare newlines (those terminate a node), except when the
        newline is escaped with a trailing backslash.
        """
        while self.i < self.n:
            c = self.s[self.i]
            if c in _WHITESPACE:
                self.i += 1
            elif c == "\\":
                # Line continuation: consume the backslash and the following
                # newline (plus any whitespace up to it).
                j = self.i + 1
                while j < self.n and self.s[j] in _WHITESPACE:
                    j += 1
                if j < self.n and self.s[j] == "\n":
                    self.i = j + 1
                else:
                    self._error("stray '\\' outside line continuation")
            elif c == "/" and self.i + 1 < self.n and self.s[self.i + 1] == "/":
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
            elif c == "/" and self.i + 1 < self.n and self.s[self.i + 1] == "*":
                self._skip_block_comment()
            else:
                break

    def _skip_block_comment(self):
        self.i += 2  # consume "/*"
        depth = 1
        while self.i < self.n and depth:
            if self.s.startswith("/*", self.i):
                depth += 1
                self.i += 2
            elif self.s.startswith("*/", self.i):
                depth -= 1
                self.i += 2
            else:
                self.i += 1
        if depth:
            self._error("unterminated block comment")

    def _skip_node_separators(self):
        """Skip everything between nodes: whitespace, newlines, ';', comments."""
        while self.i < self.n:
            c = self.s[self.i]
            if c in _WHITESPACE or c == "\n" or c == ";":
                self.i += 1
            elif c == "\\":
                self._skip_line_ws()
            elif c == "/" and self.s.startswith("//", self.i):
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
            elif c == "/" and self.s.startswith("/*", self.i):
                self._skip_block_comment()
            else:
                break

    # -- value / token scanners --------------------------------------------
    def _read_string(self):
        assert self.s[self.i] == '"'
        self.i += 1
        out = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                self.i += 1
                esc = self._peek()
                mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"',
                           "\\": "\\", "/": "/", "b": "\b", "f": "\f"}
                if esc in mapping:
                    out.append(mapping[esc])
                    self.i += 1
                elif esc == "u":
                    # \u{XXXX} form per KDL spec.
                    self.i += 1
                    if self._peek() != "{":
                        self._error("expected '{' in \\u escape")
                    self.i += 1
                    hexstart = self.i
                    while self.i < self.n and self.s[self.i] != "}":
                        self.i += 1
                    code = self.s[hexstart:self.i]
                    self.i += 1  # consume '}'
                    out.append(chr(int(code, 16)))
                else:
                    self._error(f"invalid escape '\\{esc}'")
            elif c == '"':
                self.i += 1
                return "".join(out)
            else:
                out.append(c)
                self.i += 1
        self._error("unterminated string")

    def _read_bare(self):
        start = self.i
        while self.i < self.n and self.s[self.i] not in _BARE_TERMINATORS:
            self.i += 1
        return self.s[start:self.i]

    @staticmethod
    def _coerce(token):
        """Turn a bare token into a Python scalar."""
        if token == "#true":
            return True
        if token == "#false":
            return False
        if token in ("#null", "null"):
            return None
        # Numbers (allow underscores per KDL).
        cleaned = token.replace("_", "")
        try:
            return int(cleaned)
        except ValueError:
            pass
        try:
            return float(cleaned)
        except ValueError:
            pass
        return token  # bare identifier / unquoted string

    def _read_value(self):
        if self._peek() == '"':
            return self._read_string()
        return self._coerce(self._read_bare())

    # -- structural parsing -------------------------------------------------
    def parse(self):
        """Parse the whole document into a list of top-level KDLNode."""
        nodes = []
        self._skip_node_separators()
        while self.i < self.n:
            node = self._parse_node()
            if node is not None:
                nodes.append(node)
            self._skip_node_separators()
        return nodes

    def _parse_node(self):
        # Node name (string or bare identifier).
        if self._peek() == '"':
            name = self._read_string()
        else:
            name = self._read_bare()
        if name == "":
            self._error("expected node name")
        node = KDLNode(name)

        # Args, props, and an optional child block until node terminator.
        while True:
            self._skip_line_ws()
            c = self._peek()
            if c == "" or c == "\n" or c == ";":
                if c in "\n;":
                    self.i += 1
                break
            if c == "}":
                break  # end of enclosing block; caller consumes it
            if c == "{":
                self.i += 1
                node.children = self._parse_block()
                break
            # Could be a bare property `key=value` or a positional arg.
            if c == '"':
                node.args.append(self._read_string())
                continue
            token = self._read_bare()
            if self._peek() == "=":
                self.i += 1  # consume '='
                node.props[token] = self._read_value()
            else:
                node.args.append(self._coerce(token))
        return node

    def _parse_block(self):
        children = []
        self._skip_node_separators()
        while self.i < self.n and self._peek() != "}":
            child = self._parse_node()
            if child is not None:
                children.append(child)
            self._skip_node_separators()
        if self._peek() != "}":
            self._error("unterminated child block '{'")
        self.i += 1  # consume '}'
        return children


def _parse_kdl(text):
    """Parse KDL text into a nested dict keyed by node name.

    Each node becomes ``{"args": [...], "props": {...}, "children": {...}}``.
    When a node has a single positional arg and no children, callers usually
    read ``args[0]``; helpers below make that ergonomic.
    """
    def to_dict(nodes):
        out = {}
        for node in nodes:
            out[node.name] = {
                "args": node.args,
                "props": node.props,
                "children": to_dict(node.children),
            }
        return out

    return to_dict(_KDLParser(text).parse())


# -------------------------------------------------------------------------
# Config file resolution + loading
# -------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve_config_path():
    override = os.getenv("CATEVENTS_CONFIG")
    if override:
        return override
    user_copy = os.path.join(_HERE, "config.kdl")
    if os.path.exists(user_copy):
        return user_copy
    return os.path.join(_HERE, "config.kdl.default")


def _load():
    path = _resolve_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        logger.info("Loaded configuration from %s", path)
        return _parse_kdl(text), path
    except FileNotFoundError:
        logger.error("No config file found (looked for %s). Using empty config.", path)
        return {}, path
    except Exception as e:
        logger.error("Failed to parse config %s: %s. Using empty config.", path, e)
        return {}, path


_CFG, CONFIG_PATH = _load()


# -------------------------------------------------------------------------
# Typed accessors — translate the parsed tree into native Python values
#
# Resolution chain for EVERY value:
#     1. the config file (config.kdl / config.kdl.default)
#     2. the associated environment variable (if the file lacks it)
#     3. the explicit `default=` (only when one is given)
#     4. otherwise -> raise ConfigError
#
# Because config.kdl.default ships with every value, step 4 only fires when the
# config file is missing/corrupt AND no env override exists.
# -------------------------------------------------------------------------
_MISSING = object()


class ConfigError(RuntimeError):
    """Raised when a required config value is absent from both file and env."""


def _lookup_args(path):
    """Return the positional-args list of the node at `path`, or None if the
    node is absent. An empty list means the node exists with no args."""
    parts = path.split(".")
    cur = _CFG
    for part in parts[:-1]:
        node = cur.get(part) if isinstance(cur, dict) else None
        if node is None:
            return None
        cur = node["children"]
    node = cur.get(parts[-1]) if isinstance(cur, dict) else None
    if node is None:
        return None
    return node["args"]


def _raw(path, env, default):
    """Resolve a single scalar via file -> env -> default -> raise.

    Returns a raw value (str from env, or the file's already-coerced scalar).
    """
    args = _lookup_args(path)
    if args:  # node present with >=1 arg
        return args[0]
    if env:
        ev = os.getenv(env)
        if ev is not None:
            return ev.strip().strip('\'"')
    if default is not _MISSING:
        return default
    raise ConfigError(
        f"Missing required config '{path}'"
        + (f" (env fallback '{env}')" if env else "")
        + f". Provide it in {CONFIG_PATH} or set the environment variable."
    )


def _str(path, env=None, default=_MISSING):
    v = _raw(path, env, default)
    return v if isinstance(v, str) else str(v)


def _int(path, env=None, default=_MISSING):
    v = _raw(path, env, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ConfigError(f"Config '{path}' must be an integer, got {v!r}")


def _bool(path, env=None, default=_MISSING):
    v = _raw(path, env, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "#true")
    return bool(v)


def _list(path, env=None, default=_MISSING):
    """Resolve a list via file -> env (comma/space separated) -> default -> raise.

    A node that exists with zero args resolves to an empty list (intentional).
    """
    args = _lookup_args(path)
    if args is not None:
        return list(args)
    if env:
        ev = os.getenv(env)
        if ev is not None:
            return [x for x in re.split(r"[,\s]+", ev.strip()) if x]
    if default is not _MISSING:
        return list(default)
    raise ConfigError(
        f"Missing required config list '{path}'"
        + (f" (env fallback '{env}')" if env else "")
        + f". Provide it in {CONFIG_PATH} or set the environment variable."
    )


# =========================================================================
# NATIVE PYTHON CONFIG VARIABLES
# =========================================================================

# Every value below resolves as: config file -> environment variable -> raise.
# Only genuinely-optional values pass an explicit `default=`. When neither the
# file nor the env supplies a required value, ConfigError is raised at import.

# --- Secret (env preferred; KDL fallback) --------------------------------
# HF_TOKEN is an optional credential; absence is allowed (None disables upload).
#
# Resolution: HF_TOKEN env var (highest priority) -> huggingface.hf_token in the
# config file -> None. The env var always wins, so a deployment secret is never
# shadowed by a value in config.kdl. Keep real tokens in config.kdl (which is
# git-ignored) — never in the committed config.kdl.default. Strip accidental
# surrounding quotes (common on Windows CMD) and treat the placeholder as unset.
_hf_token = os.getenv("HF_TOKEN")
if _hf_token is None:
    _hf_token = _str("huggingface.hf_token", default="")
_hf_token = (_hf_token or "").strip().strip('\'"')
HF_TOKEN = _hf_token if _hf_token and _hf_token != "YOUR_HF_TOKEN" else None

# --- Hugging Face --------------------------------------------------------
REPO_ID = _str("huggingface.repo_id", env="REPO_ID")
HF_REMOTE_FILENAME = _str("huggingface.remote_filename", env="HF_REMOTE_FILENAME")

# --- Cache ---------------------------------------------------------------
LOCAL_CACHE_FILE = _str("cache.local_file", env="LOCAL_CACHE_FILE")

# --- Server --------------------------------------------------------------
# PORT is the conventional deployment env var (Render/Heroku/etc.).
SERVER_PORT = _int("server.port", env="PORT")
SERVER_HOST = _str("server.host", env="SERVER_HOST")
SERVER_DEBUG = _bool("server.debug", env="SERVER_DEBUG")

# --- Scheduler -----------------------------------------------------------
SCHEDULER_ENABLED = _bool("scheduler.enabled", env="SCHEDULER_ENABLED")
SCHEDULER_BOOT_DELAY = _int("scheduler.boot_delay_seconds", env="SCHEDULER_BOOT_DELAY")
SCHEDULER_INTERVAL = _int("scheduler.interval_seconds", env="SCHEDULER_INTERVAL")

# --- Feed parsing --------------------------------------------------------
FEED_ITEM_LIMIT = _int("feed.item_limit", env="FEED_ITEM_LIMIT")

# --- Google Trends -------------------------------------------------------
GOOGLE_TRENDS_ENABLED = _bool("google_trends.enabled", env="GOOGLE_TRENDS_ENABLED")
GOOGLE_TRENDS_URL_TEMPLATE = _str("google_trends.url_template", env="GOOGLE_TRENDS_URL_TEMPLATE")
GOOGLE_TRENDS_TIMEOUT = _int("google_trends.request_timeout", env="GOOGLE_TRENDS_TIMEOUT")
GOOGLE_TRENDS_RESULT_LIMIT = _int("google_trends.result_limit", env="GOOGLE_TRENDS_RESULT_LIMIT")
GOOGLE_TRENDS_GEOS = _list("google_trends.geos", env="GOOGLE_TRENDS_GEOS")

# --- Reddit --------------------------------------------------------------
REDDIT_ENABLED = _bool("reddit.enabled", env="REDDIT_ENABLED")
REDDIT_URL = _str("reddit.url", env="REDDIT_URL")
REDDIT_USER_AGENT = _str("reddit.user_agent", env="REDDIT_USER_AGENT")
REDDIT_DEFAULT_SCORE = _str("reddit.default_score", env="REDDIT_DEFAULT_SCORE")
REDDIT_TIMEOUT = _int("reddit.request_timeout", env="REDDIT_TIMEOUT")

# --- X / Twitter ---------------------------------------------------------
X_SCRAPING_ENABLED = _bool("x.enabled", env="X_SCRAPING_ENABLED")
X_USER_AGENT = _str("x.user_agent", env="X_USER_AGENT")
X_QUERY = _str("x.query", env="X_QUERY")
X_TIMEOUT = _int("x.request_timeout", env="X_TIMEOUT")
X_DEFAULT_SCORE = _str("x.default_score", env="X_DEFAULT_SCORE")
X_MIRROR_INSTANCES = _list("x.mirrors", env="X_MIRROR_INSTANCES")

# --- Camoufox ------------------------------------------------------------
# Grouped into a dict for ergonomic import: `from config import CAMOUFOX`.
# `proxy` is optional (empty = no proxy); everything else is required.
CAMOUFOX = {
    "enabled": _bool("camoufox.enabled", env="CAMOUFOX_ENABLED"),
    "headless": _bool("camoufox.headless", env="CAMOUFOX_HEADLESS"),
    "humanize": _bool("camoufox.humanize", env="CAMOUFOX_HUMANIZE"),
    "default_selector": _str("camoufox.default_selector", env="CAMOUFOX_DEFAULT_SELECTOR"),
    "default_limit": _int("camoufox.default_limit", env="CAMOUFOX_DEFAULT_LIMIT"),
    "timeout_ms": _int("camoufox.timeout_ms", env="CAMOUFOX_TIMEOUT_MS"),
    "settle_ms": _int("camoufox.settle_ms", env="CAMOUFOX_SETTLE_MS"),
    "max_retries": _int("camoufox.max_retries", env="CAMOUFOX_MAX_RETRIES"),
    "block_resources": _list("camoufox.block_resources", env="CAMOUFOX_BLOCK_RESOURCES", default=[]),
    "proxy": _str("camoufox.proxy", env="CAMOUFOX_PROXY", default="") or "",
    "default_url": _str("camoufox.default_url", env="CAMOUFOX_DEFAULT_URL"),
    "default_demo_selector": _str("camoufox.default_demo_selector", env="CAMOUFOX_DEFAULT_DEMO_SELECTOR"),
}


if __name__ == "__main__":
    # `python config.py` prints the resolved configuration for debugging.
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # Windows consoles default to cp1252 and choke on non-ASCII (e.g. '▲').
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    snapshot = {
        "CONFIG_PATH": CONFIG_PATH,
        "HF_TOKEN_set": bool(HF_TOKEN),
        "REPO_ID": REPO_ID,
        "HF_REMOTE_FILENAME": HF_REMOTE_FILENAME,
        "LOCAL_CACHE_FILE": LOCAL_CACHE_FILE,
        "SERVER_PORT": SERVER_PORT,
        "SERVER_HOST": SERVER_HOST,
        "SERVER_DEBUG": SERVER_DEBUG,
        "SCHEDULER_ENABLED": SCHEDULER_ENABLED,
        "SCHEDULER_BOOT_DELAY": SCHEDULER_BOOT_DELAY,
        "SCHEDULER_INTERVAL": SCHEDULER_INTERVAL,
        "FEED_ITEM_LIMIT": FEED_ITEM_LIMIT,
        "GOOGLE_TRENDS_ENABLED": GOOGLE_TRENDS_ENABLED,
        "GOOGLE_TRENDS_URL_TEMPLATE": GOOGLE_TRENDS_URL_TEMPLATE,
        "GOOGLE_TRENDS_TIMEOUT": GOOGLE_TRENDS_TIMEOUT,
        "GOOGLE_TRENDS_RESULT_LIMIT": GOOGLE_TRENDS_RESULT_LIMIT,
        "GOOGLE_TRENDS_GEOS": GOOGLE_TRENDS_GEOS,
        "REDDIT_ENABLED": REDDIT_ENABLED,
        "REDDIT_URL": REDDIT_URL,
        "REDDIT_USER_AGENT": REDDIT_USER_AGENT,
        "REDDIT_DEFAULT_SCORE": REDDIT_DEFAULT_SCORE,
        "REDDIT_TIMEOUT": REDDIT_TIMEOUT,
        "X_SCRAPING_ENABLED": X_SCRAPING_ENABLED,
        "X_USER_AGENT": X_USER_AGENT,
        "X_QUERY": X_QUERY,
        "X_TIMEOUT": X_TIMEOUT,
        "X_DEFAULT_SCORE": X_DEFAULT_SCORE,
        "X_MIRROR_INSTANCES": X_MIRROR_INSTANCES,
        "CAMOUFOX": CAMOUFOX,
    }
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
