# ai.py
"""
AI summarization + semantic keyword extraction for scraped trends.

Given the day's Google Trends and Reddit items, this module asks an LLM to:
  1. Summarize what the world is talking about right now (a short paragraph).
  2. Extract a handful of concrete search keywords/phrases that best capture the
     dominant macro trends — these drive the Nitter/Camoufox search, replacing
     the static `x.query` when AI is enabled.

Two API flavors are supported, selected by `ai.api_type` in config:
  * "openai"    — OpenAI-compatible Chat Completions  (POST {base_url}/chat/completions)
  * "anthropic" — Anthropic-compatible Messages       (POST {base_url}/v1/messages)

Calls are made with plain `requests` (no SDK dependency) so any third-party
"compatible" endpoint works. Everything is config-driven via `from config import AI`.

Public entry point:
    summarize_and_extract(trends) -> {"summary": str, "keywords": [str, ...]} | None

Returns None when AI is disabled, unconfigured (no key), or the call fails —
callers fall back to the static query in that case.
"""
import json
import logging
import re

import requests

from config import AI
from keywords import extract_keywords

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Prompt construction
# -------------------------------------------------------------------------
# Combined prompt (summary + keywords) used when the AI drives both tasks.
_SYSTEM_PROMPT = (
    "You are a trend analyst. You are given lists of currently-trending terms "
    "scraped from Google Trends and Reddit. Identify the dominant real-world "
    "macro trends and produce concise social-media search keywords for them.\n"
    "Respond with STRICT JSON only, no prose, no code fences, in exactly this shape:\n"
    '{"summary": "<one short paragraph>", "keywords": ["<phrase>", ...]}\n'
    "Keywords must be short, concrete search phrases (e.g. \"World Cup\", "
    "\"election results\") suitable for a Twitter/Nitter search — not hashtags, "
    "not full sentences."
)

# Summary-only prompt (keywords come from elsewhere, e.g. classic search).
_SUMMARY_SYSTEM_PROMPT = (
    "You are a trend analyst. Given lists of currently-trending terms from "
    "Google Trends and Reddit, write one short paragraph summarizing the "
    "dominant real-world macro trends.\n"
    "Respond with STRICT JSON only, no prose, no code fences, in exactly this shape:\n"
    '{"summary": "<one short paragraph>"}'
)

# Keywords-only prompt (summary handled elsewhere or not wanted).
_KEYWORDS_SYSTEM_PROMPT = (
    "You are a trend analyst. Given lists of currently-trending terms from "
    "Google Trends and Reddit, extract the best concise social-media search "
    "keywords capturing the dominant macro trends.\n"
    "Respond with STRICT JSON only, no prose, no code fences, in exactly this shape:\n"
    '{"keywords": ["<phrase>", ...]}\n'
    "Keywords must be short, concrete search phrases (e.g. \"World Cup\", "
    "\"election results\") suitable for a Twitter/Nitter search — not hashtags, "
    "not full sentences."
)


def _render_trends(trends, cap=25):
    """Render the scraped Google+Reddit trend titles into a compact block."""
    def _titles(items):
        out = []
        for it in (items or [])[:cap]:
            title = (it.get("title") or "").strip()
            if title:
                out.append(f"- {title}")
        return "\n".join(out) if out else "(none)"

    return (
        f"Google Trends (global):\n{_titles(trends.get('google'))}\n\n"
        f"Reddit r/popular:\n{_titles(trends.get('reddit'))}"
    )


def _build_user_prompt(trends, keyword_count):
    """User prompt for the combined summary+keywords call."""
    return (
        f"Return at most {keyword_count} keywords.\n\n"
        f"{_render_trends(trends)}\n\n"
        "Summarize the macro trends and return the JSON."
    )


# -------------------------------------------------------------------------
# Provider-specific request/response handling
# -------------------------------------------------------------------------
def _call_openai(system, user):
    """OpenAI-compatible Chat Completions call. Returns the assistant text."""
    url = AI["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {AI['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": AI["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": AI["max_tokens"],
        "temperature": AI["temperature"],
    }
    resp = requests.post(url, headers=headers, json=body, timeout=AI["request_timeout"])
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_anthropic(system, user):
    """Anthropic-compatible Messages call. Returns the assistant text."""
    url = AI["base_url"].rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": AI["api_key"],
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": AI["model"],
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": AI["max_tokens"],
        "temperature": AI["temperature"],
    }
    resp = requests.post(url, headers=headers, json=body, timeout=AI["request_timeout"])
    resp.raise_for_status()
    data = resp.json()
    # Anthropic returns a list of content blocks; concatenate the text ones.
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts)


def _chat(system, user):
    """Dispatch to the configured provider. Returns assistant text, or None on error."""
    try:
        if AI["api_type"] == "anthropic":
            return _call_anthropic(system, user)
        return _call_openai(system, user)
    except Exception as e:
        logger.error("AI request failed (%s): %s", AI["api_type"], e)
        return None


# -------------------------------------------------------------------------
# Response parsing
# -------------------------------------------------------------------------
def _extract_json_object(text):
    """Best-effort: pull the first balanced JSON object out of `text`.

    Models sometimes wrap JSON in prose or ```json fences; this strips those and
    finds the outermost {...} block. Returns a dict, or None if none parses.
    """
    if not text:
        return None
    # Strip common code-fence wrappers.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        # Fall back to the first '{' ... matching '}' span.
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    break
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        return None


def _normalize(parsed, keyword_count):
    """Coerce a parsed dict into the {"summary", "keywords"} contract."""
    if not isinstance(parsed, dict):
        return None
    summary = parsed.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""

    raw_keywords = parsed.get("keywords")
    keywords = []
    if isinstance(raw_keywords, list):
        seen = set()
        for kw in raw_keywords:
            if not isinstance(kw, str):
                continue
            kw = kw.strip().lstrip("#").strip()
            key = kw.lower()
            if kw and key not in seen:
                seen.add(key)
                keywords.append(kw)
    keywords = keywords[:keyword_count]

    if not summary and not keywords:
        return None
    return {"summary": summary, "keywords": keywords}


# -------------------------------------------------------------------------
# AI task functions (each guarded by config; return None on any failure)
# -------------------------------------------------------------------------
def summarize_and_extract(trends):
    """
    Single combined AI call: summarize the day's trends AND extract Nitter
    search keywords. Returns ``{"summary", "keywords"}`` or ``None`` on failure.

    Assumes the caller has already checked AI is enabled + keyed; it re-checks
    the API key defensively.
    """
    if not AI.get("api_key"):
        logger.warning("AI enabled but no API key configured; skipping.")
        return None
    text = _chat(_SYSTEM_PROMPT, _build_user_prompt(trends, AI["keyword_count"]))
    result = _normalize(_extract_json_object(text), AI["keyword_count"])
    if result is None:
        logger.warning("AI response could not be parsed into summary/keywords: %r", (text or "")[:200])
    return result


def ai_summarize(trends):
    """AI summary only. Returns a summary string, or None on failure."""
    if not AI.get("api_key"):
        logger.warning("AI enabled but no API key configured; cannot summarize.")
        return None
    user = f"{_render_trends(trends)}\n\nSummarize the macro trends and return the JSON."
    text = _chat(_SUMMARY_SYSTEM_PROMPT, user)
    parsed = _extract_json_object(text)
    if isinstance(parsed, dict) and isinstance(parsed.get("summary"), str):
        return parsed["summary"].strip()
    logger.warning("AI summary could not be parsed: %r", (text or "")[:200])
    return None


def ai_find_keywords(trends):
    """AI keyword extraction only. Returns a list of keywords, or None on failure."""
    if not AI.get("api_key"):
        logger.warning("AI enabled but no API key configured; cannot find keywords.")
        return None
    user = (f"Return at most {AI['keyword_count']} keywords.\n\n"
            f"{_render_trends(trends)}\n\nReturn the JSON.")
    text = _chat(_KEYWORDS_SYSTEM_PROMPT, user)
    result = _normalize(_extract_json_object(text), AI["keyword_count"])
    if result is None:
        logger.warning("AI keywords could not be parsed: %r", (text or "")[:200])
        return None
    return result["keywords"]


# -------------------------------------------------------------------------
# Public orchestrator — flag-driven trend intelligence
# -------------------------------------------------------------------------
def analyze_trends(trends):
    """
    Produce a summary and Nitter search keywords from the day's trends,
    according to the config flags in the `ai` block.

    Flag logic:
      * ai.enabled = #false
            -> NO AI call. If ai.semantic_search, use classic keyword
               extraction for Nitter keywords; else no keywords (caller falls
               back to the static x.query). No summary.
      * ai.enabled = #true
            -> ai.ai_summarize                : AI writes the summary.
               ai.use_ai_to_find_nitter_result: AI finds the Nitter keywords.
               If BOTH are #false: the AI system does nothing useful — log a
               warning recommending ai.enabled be set to #false. Keywords then
               fall back to classic semantic search (if enabled) or the static
               query.
            When both AI tasks are on, a single combined API call is used
            (cheaper than two round-trips).

    Keyword resolution order (first non-empty wins):
        AI keywords -> classic semantic search -> [] (caller uses x.query)

    Returns ``{"summary": str, "keywords": [str, ...], "source": str}``. `source`
    describes where the keywords came from ("ai" | "semantic" | "none").
    """
    summary = ""
    keywords = []
    source = "none"

    want_summary = bool(AI.get("ai_summarize"))
    want_ai_keywords = bool(AI.get("use_ai_to_find_nitter_result"))
    semantic_on = bool(AI.get("semantic_search"))

    if AI.get("enabled"):
        if not want_summary and not want_ai_keywords:
            logger.warning(
                "ai.enabled is #true but both ai_summarize and "
                "use_ai_to_find_nitter_result are #false — the AI system will do "
                "nothing. Recommend setting ai.enabled to #false."
            )
        elif want_summary and want_ai_keywords:
            # Both tasks -> one combined call.
            combined = summarize_and_extract(trends)
            if combined:
                summary = combined["summary"]
                if combined["keywords"]:
                    keywords = combined["keywords"]
                    source = "ai"
        else:
            # Exactly one AI task requested.
            if want_summary:
                summary = ai_summarize(trends) or ""
            if want_ai_keywords:
                kws = ai_find_keywords(trends)
                if kws:
                    keywords = kws
                    source = "ai"

    # Classic semantic fallback for keywords when the AI didn't provide any.
    if not keywords and semantic_on:
        classic = extract_keywords(trends, limit=AI.get("keyword_count", 3))
        if classic:
            keywords = classic
            source = "semantic"

    return {"summary": summary, "keywords": keywords, "source": source}
