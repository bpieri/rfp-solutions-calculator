"""
Day-over-day price memory, so the app can show "NYMEX up $0.08 vs. yesterday's
close" the way a stock ticker does -- the single biggest lever for turning a
one-time calculator visit into a daily habit.

Implementation: a simple local JSON file, keyed by ticker, storing today's
and the prior stored day's front-month values. No database needed for an
MVP. On a VPS with persistent disk this behaves correctly day over day; on
ephemeral hosting (e.g. Streamlit Community Cloud free tier, which resets
storage on redeploy) it will periodically lose history -- acceptable for a
pitch prototype, worth upgrading to real persistent storage (e.g. a small
SQLite file or database) before a production rollout.
"""

import datetime as dt
import json
import os

CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "daily_snapshot_cache.json")


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass  # best-effort -- never let a storage hiccup break the page


def record_and_get_prior_close(ticker: str, nymex_price: float, basis_price: float) -> dict | None:
    """
    Call once per page load with today's live front-month NYMEX + basis.
    Returns the PRIOR stored day's {"date", "nymex", "basis"} for delta
    display, or None if there's no prior day on record yet (first-ever run,
    or storage was reset).
    """
    today_str = dt.date.today().isoformat()
    cache = _load_cache()
    entry = cache.get(ticker, {})
    today_entry = entry.get("today")
    prior_entry = entry.get("prior")

    if today_entry is None or today_entry.get("date") != today_str:
        # New calendar day (or first run ever) -- yesterday's "today" becomes
        # the new "prior", and today's live value becomes the new "today".
        new_prior = today_entry  # may be None on very first run
        cache[ticker] = {
            "today": {"date": today_str, "nymex": nymex_price, "basis": basis_price},
            "prior": new_prior,
        }
        _save_cache(cache)
        return new_prior
    else:
        # Same calendar day as last recorded -- keep the existing "prior"
        # (yesterday's close) as the comparison point; don't overwrite it
        # with today's intraday movement.
        return prior_entry
