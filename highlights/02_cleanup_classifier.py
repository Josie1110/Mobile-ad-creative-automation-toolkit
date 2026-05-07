# ============================================================
# Highlight: Creative Cleanup — Priority Classification + Rate-Limited Execution
# Source: featured/creative_cleanup/MTG_Cleanup_V2_GUI.py
#
# Why this matters:
#   Cleanup is destructive — wrong calls delete production assets.
#   This module does the two safety-critical pieces:
#     1. Per-creative classification with a strict priority chain
#        (whitelist > protected > keep > to_remove)
#     2. A token-bucket rate limiter that bounds API call rate
#        across all worker threads (Mintegral caps at ~30/min)
#
# Things to notice:
#   - Spend lookup falls back from name match to MD5 match
#   - "DB has no record" defaults to $0 spend, NOT skipped — keeps
#     forgotten creatives from leaking past the filter
#   - Whitelist short-circuits BEFORE the protection check —
#     non-target-language sets are immune regardless of age
#   - RateLimiter is a sleep-loop, not a queue — simpler, no thread
#     starvation under contention, and "lost wakeups" don't matter
#     because the next iteration just rechecks
# ============================================================

import re
import threading
import time

_MD5_RE = re.compile(r'md5-([0-9a-f]{32})', re.IGNORECASE)


# ---- Spend lookup -------------------------------------------------

def build_spend_index(db_rows):
    """Build name->spend and md5->spend lookup dicts from DB rows."""
    name_to_spend = {name: float(spend) for name, spend in db_rows}
    md5_to_spend = {}
    for name, spend in name_to_spend.items():
        m = _MD5_RE.search(name)
        if m:
            md5_to_spend[m.group(1).lower()] = spend
    return name_to_spend, md5_to_spend


def get_spend(creative_name, creative_md5, name_to_spend, md5_to_spend):
    """Returns (spend_or_None, match_type). match_type: 'name' | 'md5' | 'no_match'"""
    if creative_name in name_to_spend:
        return name_to_spend[creative_name], "name"
    if creative_md5:
        val = md5_to_spend.get(creative_md5.lower())
        if val is not None:
            return val, "md5"
    return None, "no_match"


# ---- The priority chain ------------------------------------------

def _set_in_target_lang(set_name, target_lang_codes):
    """A set is eligible for cleanup only if its name contains a target lang segment."""
    segments = set_name.upper().split("_")
    return any(seg in target_lang_codes for seg in segments)


def classify_creative(creative_name, creative_md5, created_at, set_name,
                      name_to_spend, md5_to_spend, protect_cutoff_ts,
                      threshold_min, threshold_max, target_lang_codes):
    """
    Priority order (first match wins):
        whitelist  -> set is not in a target language (immune)
        protected  -> creative is too new (within protection window)
        keep       -> spend is OUTSIDE the to-remove range
        to_remove  -> spend is in the cleanup band [min, max)

    Returns (status_str, spend_float, match_type_str)
    """
    spend, match = get_spend(creative_name, creative_md5, name_to_spend, md5_to_spend)
    if spend is None:
        spend = 0.0   # DB miss treated as $0, not skipped — must still classify

    if not _set_in_target_lang(set_name, target_lang_codes):
        return "whitelist", spend, match

    if (created_at or 0) > protect_cutoff_ts:
        return "protected", spend, match

    if not (threshold_min <= spend < threshold_max):
        return "keep", spend, match

    return "to_remove", spend, match


# ---- Rate limiter (token bucket) ----------------------------------

class RateLimiter:
    """
    Global API call rate limiter, shared across worker threads.
    Mintegral's open API is capped around 30 requests/minute — bursting
    above triggers temporary blocks that ruin a long cleanup run.
    """
    def __init__(self, calls_per_minute):
        self._interval = 60.0 / calls_per_minute
        self._lock = threading.Lock()
        self._last = 0.0

    def acquire(self):
        while True:
            with self._lock:
                now = time.time()
                if now - self._last >= self._interval:
                    self._last = now
                    return
            time.sleep(0.05)
