"""
Market Snapshot Generator -- writes market_snapshot.json for the landing page.

The landing page (static HTML in systeme.io) can't run Python, so this script
runs on a schedule, fetches the forward basis feed + live NYMEX quotes, and
writes a tiny JSON file the page's JavaScript reads on load.

Primary deployment: GitHub Actions (.github/workflows/market_snapshot.yml)
publishes the JSON to the `snapshot-data` branch every 30 minutes; the page
reads it from raw.githubusercontent.com (which sends the CORS header browsers
need). No server to maintain.

Alternative: run it as a PythonAnywhere scheduled task (paid account needed
for hourly runs + unrestricted internet for yfinance) and point
OUTPUT_PATH / HISTORY_PATH at your served directory.

Env vars (all optional):
  OUTPUT_PATH, HISTORY_PATH   where to write (default out/...)
  BASIS_FEED_URL              basis CSV URL (default: robotamp feed)
  BASIS_CSV_PATH              read the basis CSV from a local file instead
"""

import datetime as dt
import io
import json
import os

import pandas as pd
import requests
from zoneinfo import ZoneInfo

try:
    import yfinance as yf
except ImportError:
    yf = None

BASIS_FEED_URL = os.environ.get("BASIS_FEED_URL", "https://robotamp.pythonanywhere.com/rogue_basis/output/combined_master.csv")
# Local file override (used for testing): BASIS_CSV_PATH=/path/to/combined_master.csv
BASIS_CSV_PATH = os.environ.get("BASIS_CSV_PATH", "")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "out/market_snapshot.json")
HISTORY_PATH = os.environ.get("HISTORY_PATH", "out/market_snapshot_history.json")  # day-over-day deltas

TICKERS = {"SCB": "SoCal Citygate", "PGE": "PG&E Citygate", "DKR": "Transco Zone 5"}


def get_candidate_rows(df: pd.DataFrame, ticker: str) -> list:
    """Returns all rows for a ticker, sorted by contract date ascending."""
    df = df.copy()
    df["Contract Date"] = pd.to_datetime(df["Contract Date"])
    sub = df[df["Ticker"] == ticker].sort_values("Contract Date")
    return sub.to_dict("records")


def get_live_nymex(symbol: str, fallback) -> tuple:
    """Returns (price, is_live). `fallback` may be None."""
    if yf is not None and symbol:
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if not hist.empty:
                return float(hist["Close"].dropna().iloc[-1]), True
        except Exception:
            pass
    return fallback, False


def get_best_available_quote(df: pd.DataFrame, ticker: str) -> dict:
    """
    Walks forward through this ticker's contract months (nearest first)
    until it finds one with a usable NYMEX price (live or CSV fallback) --
    the feed occasionally has a blank NYMEX settle for the very front
    month, and the website should never show a blank number.
    """
    for row in get_candidate_rows(df, ticker):
        basis = row.get("Basis Settle")
        if pd.isna(basis):
            continue
        nymex_symbol = row.get("NYMEX Symbol")
        nymex_settle_csv = row.get("NYMEX Settle")
        nymex_settle_csv = float(nymex_settle_csv) if pd.notna(nymex_settle_csv) else None
        price, is_live = get_live_nymex(nymex_symbol, nymex_settle_csv)
        if price is not None:
            return {
                "contract_month": row.get("Contract Month"),
                "basis": float(basis),
                "nymex": price,
                "nymex_is_live": is_live,
            }
    return {}


def live_quotes(symbols) -> dict:
    """{symbol: last close} for many NYMEX contracts in one batch call (empty dict if unreachable)."""
    symbols = [x for x in dict.fromkeys(symbols) if x]
    if yf is None or not symbols:
        return {}
    out = {}
    try:
        data = yf.download(tickers=symbols, period="5d", interval="1d", group_by="ticker",
                           progress=False, threads=True)
        for sym in symbols:
            try:
                series = (data["Close"] if len(symbols) == 1 else data[sym]["Close"]).dropna()
                if len(series):
                    out[sym] = float(series.iloc[-1])
            except Exception:
                continue
    except Exception:
        pass
    return out


def next12_vs_front_pct(df: pd.DataFrame, ticker: str, quotes: dict):
    """
    Same definition as the calculator's Buyer Signal: how far the average of the
    next 12 months (NYMEX + basis) sits above/below the front month, in percent.
    """
    totals = []
    for row in get_candidate_rows(df, ticker)[:16]:
        basis = row.get("Basis Settle")
        nymex = quotes.get(row.get("NYMEX Symbol"))
        if nymex is None and pd.notna(row.get("NYMEX Settle")):
            nymex = float(row["NYMEX Settle"])
        if pd.isna(basis) or nymex is None:
            continue
        totals.append(nymex + float(basis))
    if len(totals) < 4:
        return None
    nxt = totals[1:13]
    return round((sum(nxt) / len(nxt) - totals[0]) / totals[0] * 100, 1)


def main():
    if BASIS_CSV_PATH:
        df = pd.read_csv(BASIS_CSV_PATH)
    else:
        resp = requests.get(BASIS_FEED_URL, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
    report_date = str(df["Report Date"].iloc[-1]) if "Report Date" in df.columns else None

    # Load yesterday's snapshot for day-over-day deltas (same two-tier
    # today/prior pattern as the Streamlit app's daily_snapshot.py).
    try:
        with open(HISTORY_PATH) as f:
            history = json.load(f)
    except Exception:
        history = {}

    today_str = dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    snapshot = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "basis_report_date": report_date, "tickers": {}}

    all_symbols = []
    for t in TICKERS:
        all_symbols += [r.get("NYMEX Symbol") for r in get_candidate_rows(df, t)[:16]]
    quotes = live_quotes(all_symbols)

    for ticker, label in TICKERS.items():
        quote = get_best_available_quote(df, ticker)
        if not quote:
            continue
        nymex_price = quote["nymex"]
        basis = quote["basis"]

        # Day-over-day: "today" is overwritten on every run, so when the (New York)
        # calendar day rolls over it still holds the LAST reading of the previous
        # day (~ the settle). That becomes "prior". Deltas are only shown when the
        # front month is the same contract on both days (a roll compares apples
        # to oranges).
        rec = history.setdefault(ticker, {})
        today_rec = rec.get("today")
        if today_rec and today_rec.get("date") != today_str:
            rec["prior"] = today_rec
        rec["today"] = {"date": today_str, "contract_month": quote["contract_month"],
                        "nymex": nymex_price, "basis": basis}
        prior_for_delta = rec.get("prior")
        if prior_for_delta and prior_for_delta.get("contract_month") != quote["contract_month"]:
            prior_for_delta = None

        snapshot["tickers"][ticker] = {
            "label": label,
            "contract_month": quote["contract_month"],
            "nymex": round(nymex_price, 3),
            "nymex_is_live": quote["nymex_is_live"],
            "basis": round(basis, 3),
            "combined": round(nymex_price + basis, 3),
            "next12_vs_front_pct": next12_vs_front_pct(df, ticker, quotes),
            "prior_nymex": prior_for_delta.get("nymex") if prior_for_delta else None,
            "prior_basis": prior_for_delta.get("basis") if prior_for_delta else None,
            "prior_date": prior_for_delta.get("date") if prior_for_delta else None,
        }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(snapshot, f, indent=2)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)

    print(f"Wrote snapshot to {OUTPUT_PATH}: {json.dumps(snapshot, indent=2)}")


if __name__ == "__main__":
    main()
