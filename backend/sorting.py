# sorting.py
"""
Ranking helpers for scraped trend items.

Two things live here:

1. `quicksort` — a classic in-place-ish quicksort (Lomuto partition) that takes
   a `key` function and a `reverse` flag, so we don't lean on Python's built-in
   `sorted`. This is the sort used to order trends.

2. Trend ranking — turns each ``{"title", "score", ...}`` dict into a numeric
   *search volume* and an *abnormality* score (how far a term spikes above the
   typical volume in the current 24h batch), then quicksorts by:

       (volume desc, abnormality desc, title asc)

   The title tie-break compares strings via `ascii_key`, which converts each
   character to its ASCII code and subtracts 32 (case-folded to upper first, so
   the ordering is case-insensitive) — per the project's spec.
"""
import re

try:
    from config import SORTING
except Exception:
    # Sensible standalone defaults if config isn't importable (e.g. unit tests).
    SORTING = {
        "enabled": True,
        "order": "desc",
        "case_insensitive": True,
        "use_abnormality": True,
        "ascii_offset": 32,
    }


# -------------------------------------------------------------------------
# Quicksort
# -------------------------------------------------------------------------
def quicksort(items, key=None, reverse=False):
    """
    Return a new list containing `items` sorted via quicksort.

    `key`     : callable mapping an item to a comparable value (default identity).
    `reverse` : descending order when True.

    Comparable values may be tuples; they compare lexicographically as usual.
    """
    if key is None:
        key = lambda x: x
    arr = list(items)
    _quicksort(arr, 0, len(arr) - 1, key)
    if reverse:
        arr.reverse()
    return arr


def _quicksort(arr, lo, hi, key):
    # Iterative quicksort to avoid Python recursion limits on large batches.
    stack = [(lo, hi)]
    while stack:
        lo, hi = stack.pop()
        if lo >= hi:
            continue
        p = _partition(arr, lo, hi, key)
        # Push the larger side last so the stack stays shallow.
        stack.append((lo, p - 1))
        stack.append((p + 1, hi))


def _partition(arr, lo, hi, key):
    """Lomuto partition using the middle element as pivot (median-ish)."""
    mid = (lo + hi) // 2
    arr[mid], arr[hi] = arr[hi], arr[mid]  # move pivot to end
    pivot = key(arr[hi])
    i = lo
    for j in range(lo, hi):
        if _less_eq(key(arr[j]), pivot):
            arr[i], arr[j] = arr[j], arr[i]
            i += 1
    arr[i], arr[hi] = arr[hi], arr[i]
    return i


def _less_eq(a, b):
    """`a <= b` that tolerates mixed/None comparisons deterministically."""
    try:
        return a <= b
    except TypeError:
        # Fall back to comparing string forms so the sort never crashes.
        return str(a) <= str(b)


# -------------------------------------------------------------------------
# ASCII string key  (spec: "transfer the strings to ASCII then subtract 32")
# -------------------------------------------------------------------------
def ascii_key(text, offset=None, case_insensitive=None):
    """
    Convert a string to a tuple of ``ord(ch) - offset`` values for
    lexicographic comparison (spec: convert to ASCII, then subtract 32).

    `offset` defaults to `sorting.ascii_offset` from config (32). When
    `case_insensitive` (default from config) the string is upper-cased first so
    'a' and 'A' fold to the same code.

    Example: "Ab" -> (ord('A')-32, ord('B')-32) = (33, 34)
    """
    if offset is None:
        offset = SORTING.get("ascii_offset", 32)
    if case_insensitive is None:
        case_insensitive = SORTING.get("case_insensitive", True)
    s = str(text).upper() if case_insensitive else str(text)
    return tuple((ord(ch) - offset) for ch in s)


# -------------------------------------------------------------------------
# Search-volume parsing
# -------------------------------------------------------------------------
_SUFFIX_MULT = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_NUM_RE = re.compile(r"([\d][\d,\.]*)\s*([KMB])?", re.IGNORECASE)


def parse_search_volume(score):
    """
    Parse a Google-Trends-style traffic string into an integer.

    Handles: "200,000+", "1M+", "20K", "1.5M", plain ints, and non-numeric
    labels like "New" / "▲ Popular" / "Offline" (which return 0).
    """
    if score is None:
        return 0
    if isinstance(score, (int, float)):
        return int(score)
    s = str(score).strip()
    m = _NUM_RE.search(s)
    if not m:
        return 0
    num = m.group(1).replace(",", "")
    try:
        value = float(num)
    except ValueError:
        return 0
    suffix = (m.group(2) or "").upper()
    return int(value * _SUFFIX_MULT.get(suffix, 1))


# -------------------------------------------------------------------------
# Abnormality (spike) scoring for the current 24h batch
# -------------------------------------------------------------------------
def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _pstdev(values, mean):
    if len(values) < 2:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5


def compute_abnormality(volumes):
    """
    Given the list of volumes in a 24h batch, return a parallel list of
    abnormality scores: how many standard deviations each volume sits above the
    batch mean (a z-score clamped at 0 below the mean).

    A term whose volume is far above the typical search volume for the window is
    "abnormally frequent" and scores high; ordinary terms score ~0.
    """
    mean = _mean(volumes)
    sd = _pstdev(volumes, mean)
    if sd == 0:
        return [0.0 for _ in volumes]
    return [max(0.0, (v - mean) / sd) for v in volumes]


# -------------------------------------------------------------------------
# Public: rank a list of trend items
# -------------------------------------------------------------------------
def sort_trends(items, score_field="score", volume_field="volume",
                abnormality_field="abnormality"):
    """
    Sort scraped trend dicts by highest search volume, then by most abnormal
    24h frequency, then alphabetically (ASCII-folded) — using `quicksort`.

    Non-destructively annotates each returned dict with numeric `volume` and
    `abnormality` fields so callers/UI can display them. Items whose score is
    non-numeric (e.g. Reddit's "▲ Popular", "Offline") get volume 0 and sort
    below any item with real volume, preserving their alphabetical order.

    Respects the `sorting` config block: when `sorting.enabled` is #false the
    items are returned in their original order (still annotated with volume /
    abnormality). `sorting.order` chooses ascending/descending volume, and
    `sorting.use_abnormality` toggles the 24h-spike tie-break.

    Returns a NEW list; the input is not mutated.
    """
    enriched = []
    volumes = []
    for it in items:
        vol = parse_search_volume(it.get(score_field))
        volumes.append(vol)
        enriched.append(dict(it))  # shallow copy so we don't mutate the input

    abn = compute_abnormality(volumes)
    for it, vol, a in zip(enriched, volumes, abn):
        it[volume_field] = vol
        it[abnormality_field] = round(a, 4)

    if not SORTING.get("enabled", True):
        return enriched

    descending = SORTING.get("order", "desc").lower() != "asc"
    use_abn = SORTING.get("use_abnormality", True)
    sign = -1 if descending else 1

    # Primary: volume (sign flips for desc). Secondary: abnormality spike
    # (always highest-first when enabled). Tertiary: ASCII-folded title.
    def _key(it):
        abn_component = (-it[abnormality_field],) if use_abn else ()
        return (
            sign * it[volume_field],
            *abn_component,
            ascii_key(it.get("title", "")),
        )

    return quicksort(enriched, key=_key)
