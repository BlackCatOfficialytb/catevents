# keywords.py
"""
Classic (non-AI) semantic keyword extraction from scraped trends.

When the AI API is disabled (or not tasked with finding Nitter keywords), this
module derives search keywords from the day's Google Trends + Reddit titles
using lightweight, dependency-free text analysis:

  * tokenize titles, drop stopwords and short tokens
  * score terms by cross-source frequency (a term appearing in BOTH Google and
    Reddit is a stronger macro-trend signal and is boosted)
  * also surface high-signal multi-word phrases (bigrams) that recur

This is the "semantic_search" path referenced in config: it's a heuristic
relevance ranking, not an LLM. Returns concrete phrases suitable for a
Twitter/Nitter search.

Public entry point:
    extract_keywords(trends, limit=3) -> [str, ...]
"""
import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

# Common English + a few cross-language filler words that add no search signal.
_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "for", "and", "is", "on", "it", "its",
    "was", "we", "you", "your", "my", "me", "he", "she", "his", "her", "their",
    "that", "this", "with", "at", "by", "from", "as", "be", "are", "or", "but",
    "not", "have", "has", "had", "do", "does", "did", "so", "if", "then", "than",
    "just", "about", "into", "out", "up", "down", "over", "after", "before",
    "new", "get", "got", "make", "made", "way", "day", "today", "back", "how",
    "why", "what", "when", "who", "will", "can", "one", "all", "more", "most",
    "now", "here", "there", "some", "any", "been", "were", "they", "them",
    "our", "his", "hers", "also", "like", "even", "much", "many", "such",
    "de", "la", "el", "los", "las", "und", "der", "die", "das", "le", "les",
    "du", "des", "en", "un", "una", "di", "il",
}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # letters only, unicode-aware


def _tokenize(title):
    """Lowercase word tokens (unicode letters), stopwords/short tokens removed."""
    return [
        w for w in (m.group(0).lower() for m in _WORD_RE.finditer(title or ""))
        if len(w) >= 3 and w not in _STOPWORDS
    ]


def _title_list(items):
    return [(it.get("title") or "").strip() for it in (items or []) if (it.get("title") or "").strip()]


def _bigrams(tokens):
    return [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]


def extract_keywords(trends, limit=3):
    """
    Derive up to `limit` search keywords from Google+Reddit trend titles.

    `trends` is a dict with "google" and "reddit" lists of ``{"title", ...}``.

    Terms present in BOTH sources are boosted (cross-source = stronger macro
    trend). Falls back gracefully: returns [] when there's nothing usable, so
    callers can then fall back to the static x.query.
    """
    google_titles = _title_list(trends.get("google"))
    reddit_titles = _title_list(trends.get("reddit"))

    google_tokens, reddit_tokens = [], []
    for t in google_titles:
        google_tokens += _tokenize(t)
    for t in reddit_titles:
        reddit_tokens += _tokenize(t)

    if not google_tokens and not reddit_tokens:
        return []

    google_set = set(google_tokens)
    reddit_set = set(reddit_tokens)
    freq = Counter(google_tokens) + Counter(reddit_tokens)

    # Cross-source boost: double the score of terms seen in both feeds.
    scored = {}
    for term, count in freq.items():
        score = count
        if term in google_set and term in reddit_set:
            score *= 2
        scored[term] = score

    # Prefer recurring bigrams that are made of already-salient unigrams — these
    # are more search-worthy phrases (e.g. "world cup") than lone words.
    all_titles_tokens = [_tokenize(t) for t in google_titles + reddit_titles]
    bigram_freq = Counter()
    for toks in all_titles_tokens:
        bigram_freq.update(_bigrams(toks))

    keywords = []
    seen = set()

    # 1) Strong recurring bigrams (appear >= 2 times).
    for phrase, count in bigram_freq.most_common():
        if count < 2:
            break
        if phrase not in seen:
            seen.add(phrase)
            keywords.append(phrase)
        if len(keywords) >= limit:
            return keywords

    # 2) Top-scoring unigrams (cross-source boosted).
    for term, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0])):
        if term not in seen:
            seen.add(term)
            keywords.append(term)
        if len(keywords) >= limit:
            break

    logger.info("Classic semantic search derived keywords: %s", keywords[:limit])
    return keywords[:limit]
