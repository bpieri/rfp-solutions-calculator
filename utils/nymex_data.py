"""
Forward NYMEX (Henry Hub) natural gas futures curve via yfinance.

Each contract month's Yahoo Finance ticker (e.g. "NGV26.NYM" for Oct 2026)
matches the "NYMEX Symbol" column already present in the basis feed, so we
pull live settle prices for exactly the contract months we need.

If yfinance is unreachable or a specific contract has no live quote (thin
volume, delisted front month, etc.), we fall back per-contract to the NYMEX
settle value already embedded in the basis CSV feed -- so the forward curve
never has a gap, it's just flagged as non-live for that point.
"""

import datetime as dt

import pandas as pd
import streamlit as st

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except Exception:
    YFINANCE_AVAILABLE = False


@st.cache_data(ttl=3600 * 2)
def get_nymex_settles(symbols: tuple) -> dict:
    """
    Returns {"quotes": {symbol: last_close_price}, "fetched_at": iso timestamp}.
    The fetched_at timestamp reflects when this (possibly cached) result was
    actually pulled -- not "now" -- so the UI can honestly disclose staleness
    given the cache TTL above.
    """
    fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
    if not YFINANCE_AVAILABLE or not symbols:
        return {"quotes": {}, "fetched_at": fetched_at}

    results = {}
    try:
        data = yf.download(
            tickers=list(symbols),
            period="5d",
            interval="1d",
            group_by="ticker",
            progress=False,
            threads=True,
        )
        for sym in symbols:
            try:
                if len(symbols) == 1:
                    series = data["Close"].dropna()
                else:
                    series = data[sym]["Close"].dropna()
                if len(series):
                    results[sym] = float(series.iloc[-1])
            except Exception:
                continue
    except Exception:
        # Batch call failed entirely (network policy, rate limit, bad symbol
        # list) -- try one at a time so a single bad symbol doesn't kill the
        # whole curve.
        for sym in symbols:
            try:
                t = yf.Ticker(sym)
                hist = t.history(period="5d")
                if not hist.empty:
                    results[sym] = float(hist["Close"].dropna().iloc[-1])
            except Exception:
                continue

    return {"quotes": results, "fetched_at": fetched_at}


def build_nymex_forward_curve(basis_curve: pd.DataFrame) -> tuple:
    """
    Given the basis curve (which carries `nymex_symbol` and the CSV-embedded
    `nymex_settle_csv` fallback per contract month), returns
    (curve_with_prices, fetched_at) where curve_with_prices has a
    `nymex_price` column and an `is_live` flag per row, preferring a live
    yfinance quote and falling back to the CSV value.
    """
    if basis_curve.empty:
        empty = basis_curve.assign(nymex_price=pd.Series(dtype=float), nymex_is_live=pd.Series(dtype=bool))
        return empty, None

    symbols = tuple(basis_curve["nymex_symbol"].dropna().unique())
    result = get_nymex_settles(symbols)
    live_quotes = result["quotes"]
    fetched_at = result["fetched_at"]

    prices, is_live_flags = [], []
    for _, row in basis_curve.iterrows():
        sym = row["nymex_symbol"]
        if sym in live_quotes:
            prices.append(live_quotes[sym])
            is_live_flags.append(True)
        elif pd.notna(row.get("nymex_settle_csv")):
            prices.append(row["nymex_settle_csv"])
            is_live_flags.append(False)
        else:
            prices.append(None)
            is_live_flags.append(False)

    out = basis_curve.copy()
    out["nymex_price"] = prices
    out["nymex_is_live"] = is_live_flags
    return out.dropna(subset=["nymex_price"]), fetched_at
