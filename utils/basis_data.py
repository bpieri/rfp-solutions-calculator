"""
Forward basis curve loader.

Pulls the live forward basis curve from Energy Rogue's Rogue Basis Scraper
output (Brian's PythonAnywhere-hosted CSV), filtered by ticker:
  - "PGE" = PG&E Citygate Basis Future
  - "SCB" = SoCal Citygate Basis Future

Falls back to a bundled local snapshot if the live URL is unreachable (e.g.
this sandbox's network policy, or the PythonAnywhere app being asleep/down),
so the app never breaks -- it just clearly flags the data as cached.
"""

import io
import os

import pandas as pd
import requests
import streamlit as st

LIVE_BASIS_URL = "https://robotamp.pythonanywhere.com/rogue_basis/output/combined_master.csv"
LOCAL_FALLBACK_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "combined_master_fallback.csv")

TICKER_LABELS = {
    "PGE": "PG&E Citygate",
    "SCB": "SoCal Citygate",
}


@st.cache_data(ttl=3600 * 4)
def _load_raw_basis_table() -> tuple[pd.DataFrame, bool]:
    """Returns (dataframe, is_live)."""
    try:
        resp = requests.get(LIVE_BASIS_URL, timeout=10)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        if df.empty:
            raise ValueError("empty basis feed")
        return df, True
    except Exception:
        df = pd.read_csv(LOCAL_FALLBACK_PATH)
        return df, False


def get_basis_forward_curve(ticker: str) -> dict:
    """
    Returns:
        {
            "curve": DataFrame[contract_month, contract_date, basis, nymex_settle_csv],
            "is_live": bool,
            "as_of": str,
        }
    `nymex_settle_csv` is the NYMEX settle value already embedded in the basis
    feed -- used only as a fallback if the live yfinance pull fails.
    """
    df, is_live = _load_raw_basis_table()
    sub = df[df["Ticker"] == ticker].copy()
    if sub.empty:
        return {"curve": pd.DataFrame(), "is_live": is_live, "as_of": "n/a"}

    sub["Contract Date"] = pd.to_datetime(sub["Contract Date"])
    sub = sub.sort_values("Contract Date")
    as_of = str(sub["Report Date"].iloc[-1]) if "Report Date" in sub.columns else "n/a"

    out = pd.DataFrame({
        "contract_month": sub["Contract Month"].values,
        "contract_date": sub["Contract Date"].values,
        "basis": pd.to_numeric(sub["Basis Settle"], errors="coerce").values,
        "nymex_symbol": sub["NYMEX Symbol"].values,
        "nymex_settle_csv": pd.to_numeric(sub["NYMEX Settle"], errors="coerce").values,
    })
    return {"curve": out, "is_live": is_live, "as_of": as_of}
