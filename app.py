import base64
import datetime as dt
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.tariffs import (
    SOCALGAS_CORE_G10,
    PGE_GNR1,
    bracketed_cost,
    pge_transport_only_cost,
    local_delivery_dollars_per_therm,
    season_for_month,
    wacog_premium_dollars_per_therm,
)
from utils.basis_data import get_basis_forward_curve, TICKER_LABELS, LIVE_BASIS_URL
from utils.nymex_data import build_nymex_forward_curve
from utils.daily_snapshot import record_and_get_prior_close

# ---------------------------------------------------------------------------
# Page config + dark theming
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="RFP Energy Solutions | Know What Your Natural Gas Should Cost",
    page_icon="🔥",
    layout="wide",
)

NAVY = "#0B1E3D"          # page background
CARD = "#132A50"          # card background (one step lighter than page)
CARD_BORDER = "rgba(255,255,255,0.08)"
GOLD = "#F2C94C"
GREEN = "#2ECC71"
CYAN = "#2F80ED"
RED = "#E74C3C"
ORANGE = "#E67E22"
TEXT = "#EAF0FA"
SUBTEXT = "#8FA3C4"

LOGO_PATH = Path(__file__).parent / "assets" / "rfpes-logo-white.png"
_logo_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode() if LOGO_PATH.exists() else None


def dark_chart_layout(**overrides) -> dict:
    """
    Shared Plotly dark-theme layout: sets font, axis line/tick/grid colors
    and legend text color explicitly, since `font_color` alone at the layout
    level does not reliably cascade to every axis/legend sub-element in
    Plotly -- leaving gridlines and tick labels at their light-mode default
    (dark grey), which is invisible against a dark navy background.

    Nested keys (xaxis, yaxis, legend) are merged with the dark-theme
    defaults rather than replaced, so a caller can add e.g.
    legend=dict(orientation="h") without losing the legend font color.
    """
    base = {
        "plot_bgcolor": CARD,
        "paper_bgcolor": NAVY,
        "font": dict(color=TEXT),
        "xaxis": dict(color=TEXT, gridcolor="rgba(255,255,255,0.10)", linecolor="rgba(255,255,255,0.25)",
                      tickfont=dict(color=TEXT), title=dict(font=dict(color=TEXT))),
        "yaxis": dict(color=TEXT, gridcolor="rgba(255,255,255,0.10)", linecolor="rgba(255,255,255,0.25)",
                      tickfont=dict(color=TEXT), title=dict(font=dict(color=TEXT))),
        "legend": dict(font=dict(color=TEXT)),
    }
    for key in ("xaxis", "yaxis", "legend"):
        if key in overrides:
            merged = dict(base[key])
            merged.update(overrides.pop(key))
            base[key] = merged
    base.update(overrides)
    return base


st.markdown(
    f"""
    <style>
        .stApp {{ background-color: {NAVY}; }}
        [data-testid="stHeader"] {{ background-color: {NAVY}; }}
        [data-testid="stSidebar"] {{ background-color: {CARD}; }}
        html, body, .stMarkdown, .stCaption, p, span, label, .stTextInput label,
        .stNumberInput label, .stSelectbox label, .stSlider label, .stRadio label {{ color: {TEXT} !important; }}
        h1, h2, h3, h4 {{ color: {TEXT} !important; }}
        .stTabs [data-baseweb="tab"] {{ color: {SUBTEXT}; }}
        .stTabs [aria-selected="true"] {{ color: {GOLD} !important; }}
        [data-testid="stDataFrame"] {{ background-color: {CARD}; }}
        .rfp-hero {{
            background: linear-gradient(135deg, {CARD} 0%, #0E2244 100%);
            padding: 2rem 2.2rem; border-radius: 14px; color: {TEXT}; margin-bottom: 1.5rem;
            border: 1px solid {CARD_BORDER};
        }}
        .rfp-hero h1 {{ color: {TEXT} !important; font-size: 2.1rem; margin: 0.8rem 0 0.3rem 0; }}
        .rfp-hero p {{ color: {SUBTEXT}; font-size: 1.05rem; }}
        .rfp-badge {{
            display:inline-block; background:{GOLD}; color:{NAVY} !important; font-weight:700;
            padding: 3px 10px; border-radius: 20px; font-size: 0.78rem; margin-right:8px;
        }}
        .rfp-card {{
            background:{CARD}; border-radius: 12px; padding: 1.2rem 1.4rem;
            box-shadow: 0 1px 4px rgba(0,0,0,0.25); margin-bottom: 1rem;
            border: 1px solid {CARD_BORDER};
        }}
        .rfp-card.plus {{ border-left: 3px solid {CYAN}; }}
        .rfp-card.opaque {{ border: 1px dashed {ORANGE}; background: rgba(230,126,34,0.08); }}
        .rfp-card.total {{ border: 2px solid {GOLD}; background: rgba(242,201,76,0.08); }}
        .rfp-stat {{ font-size: 1.9rem; font-weight:800; color:{TEXT}; }}
        .rfp-stat-label {{ color:{SUBTEXT}; font-size:0.85rem; }}
        .source-note {{ color:{SUBTEXT}; font-size:0.78rem; }}
        .rfp-signal-card {{
            background: linear-gradient(135deg, {CARD} 0%, #0E2244 100%);
            border-radius: 14px; padding: 1.4rem 1.6rem; margin-bottom: 1.5rem;
            border: 1px solid {CARD_BORDER};
        }}
        .rfp-cta-banner {{
            background: linear-gradient(135deg, {ORANGE} 0%, #C0392B 100%);
            border-radius: 14px; padding: 1.3rem 1.6rem; margin: 1.2rem 0;
            color: white;
        }}
        .rfp-cta-banner b {{ color: white; }}
    </style>
    """,
    unsafe_allow_html=True,
)

TAXES_DEFAULT_PER_DTH = 0.25      # $/Dth -- RFP's own example figure
ADVISORY_FEE_DEFAULT_PER_DTH = 0.25  # $/Dth -- RFP's anticipated advisory fee, part of the opaque bucket

UTILITY_OPTIONS = {
    "SoCalGas — SoCal Citygate": {"ticker": "SCB", "utility_key": "SoCalGas"},
    "PG&E — PG&E Citygate": {"ticker": "PGE", "utility_key": "PG&E"},
}

# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="rfp-hero">
        {'<img src="data:image/png;base64,' + _logo_b64 + '" style="height:56px;margin-bottom:0.4rem;">' if _logo_b64 else ''}
        <div>
        <span class="rfp-badge">PG&E Natural Gas Service Provider</span>
        <span class="rfp-badge">SoCalGas Non-Core Agent</span>
        </div>
        <h1>Know What Your Natural Gas Should Cost</h1>
        <p>See an indicative fixed price built from live NYMEX futures, live forward basis, and your utility's
        own published tariff — then connect with RFP for the real, guaranteed number.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# THE ONE TRIGGER: utility/citygate dropdown drives everything below
# ---------------------------------------------------------------------------
st.header("Indicative Fixed Price Calculator")
top_col1, top_col2, top_col3 = st.columns([1.3, 1, 1])
with top_col1:
    utility_label = st.selectbox("Your citygate", list(UTILITY_OPTIONS.keys()))
with top_col2:
    monthly_dth = st.number_input("Average monthly usage (Dth)", min_value=10, max_value=50000, value=500, step=10,
                                   help="1 Dth ≈ 1 MMBtu ≈ 10 therms.")
with top_col3:
    fixed_term_months = st.selectbox("Fixed price term", [6, 12, 24], index=1,
                                      help="How many forward months to average into your indicative fixed price.")

# Internal-only: the underlying utility tariffs are published in $/therm, so
# this converts the Dth input for those calls. Never shown to the user --
# every number on screen is in Dth.
monthly_therms = monthly_dth * 10

utility_choice = UTILITY_OPTIONS[utility_label]
ticker = utility_choice["ticker"]
utility_key = utility_choice["utility_key"]

with st.expander("⚙️ Assumptions behind the estimated Local Delivery, Advisory & Taxes (adjust freely)"):
    st.caption(
        "These three vary by your specific account, meter, and contract terms — RFP combines them into a "
        "single estimate here rather than pretending to know your exact number. Set in $/Dth, matching "
        "standard natural gas marketer convention."
    )
    advisory_fee_per_dth = st.slider("RFP Advisory Fee ($/Dth)", min_value=0.0, max_value=1.50,
                                      value=ADVISORY_FEE_DEFAULT_PER_DTH, step=0.05,
                                      help="Covers procurement strategy, market intelligence, and the senior-level "
                                           "relationship — not a generic markup.")
    taxes_per_dth = st.slider("Taxes & Regulatory Fees ($/Dth)", min_value=0.0, max_value=1.00,
                               value=TAXES_DEFAULT_PER_DTH, step=0.05)

# ---------------------------------------------------------------------------
# Pull live data: forward basis (Rogue Basis feed) -> forward NYMEX (yfinance)
# ---------------------------------------------------------------------------
basis_result = get_basis_forward_curve(ticker)
basis_curve = basis_result["curve"]

if basis_curve.empty:
    st.error(f"No forward basis data found for ticker '{ticker}'. Check the data feed.")
    st.stop()

curve, nymex_fetched_at = build_nymex_forward_curve(basis_curve)
curve = curve.sort_values("contract_date").reset_index(drop=True)

# NYMEX/basis from the data feed are natively $/MMBtu, which we treat as
# equal to $/Dth throughout this calculator (standard gas-industry
# convention). The utility's own published Cost of Gas, used to measure the
# WACOG premium, is $/therm-native -- that one internal helper stays in
# therms for the comparison to be apples-to-apples, then gets converted to
# Dth immediately below for everything downstream.
front_month_commodity_per_therm = (curve.iloc[0]["nymex_price"] + curve.iloc[0]["basis"]) / 10.0
wacog_premium_per_therm = wacog_premium_dollars_per_therm(utility_key, front_month_commodity_per_therm)
wacog_premium_per_dth = wacog_premium_per_therm * 10

# ---------------------------------------------------------------------------
# Daily engagement: day-over-day price change + a curve-shape "Buyer Signal"
# -- the reason a buyer has to check back tomorrow, not just today.
# (Percentage-based, so unit-invariant -- no Dth conversion needed here.)
# ---------------------------------------------------------------------------
front_nymex_raw = float(curve.iloc[0]["nymex_price"])   # $/MMBtu ~= $/Dth
front_basis_raw = float(curve.iloc[0]["basis"])          # $/MMBtu ~= $/Dth
prior_close = record_and_get_prior_close(ticker, front_nymex_raw, front_basis_raw)

nymex_change = front_nymex_raw - prior_close["nymex"] if prior_close else None
basis_change = front_basis_raw - prior_close["basis"] if prior_close else None

front_month_commodity_per_dth = front_nymex_raw + front_basis_raw
next12 = curve.iloc[1:13]
if len(next12) >= 3:
    forward_avg_commodity = (next12["nymex_price"] + next12["basis"]).mean()
    signal_gap_pct = (forward_avg_commodity - front_month_commodity_per_dth) / front_month_commodity_per_dth * 100
    if signal_gap_pct > 4:
        signal_label, signal_color = "🟢 Consider Locking", GREEN
        signal_text = (f"The forward curve is pricing the next 12 months an average of {signal_gap_pct:.1f}% "
                        f"above today's front-month price — the market expects higher prices ahead.")
    elif signal_gap_pct < -4:
        signal_label, signal_color = "🟡 Consider Waiting", GOLD
        signal_text = (f"The forward curve is pricing the next 12 months an average of {abs(signal_gap_pct):.1f}% "
                        f"below today's front-month price — the market expects softer prices ahead.")
    else:
        signal_label, signal_color = "⚪ Neutral", SUBTEXT
        signal_text = (f"The forward curve is roughly flat (within {abs(signal_gap_pct):.1f}%) across the next "
                        f"12 months — no strong signal either way today.")
else:
    signal_label, signal_color = "⚪ Insufficient Data", SUBTEXT
    signal_text = "Not enough forward months in the feed to compute a signal."

# ---------------------------------------------------------------------------
# Build the full forward curve stack (for charts / context), fully in $/Dth.
# NYMEX and Basis are used as-is (already $/MMBtu ~= $/Dth, no conversion).
# Transportation, Local Delivery and the WACOG premium come from $/therm-
# native tariff functions, so each is converted (x10) at the point of use.
# ---------------------------------------------------------------------------
rows = []
for _, r in curve.iterrows():
    contract_date = pd.Timestamp(r["contract_date"])
    season = season_for_month(contract_date.month)

    nymex_per_dth = r["nymex_price"]
    basis_per_dth = r["basis"]
    market_commodity_per_dth = nymex_per_dth + basis_per_dth
    projected_utility_commodity_per_dth = market_commodity_per_dth + wacog_premium_per_dth

    if utility_key == "SoCalGas":
        transport_per_dth = (bracketed_cost(monthly_therms, SOCALGAS_CORE_G10.transport_only_tiers) / 100.0 / monthly_therms) * 10
    else:
        transport_per_dth = (pge_transport_only_cost(monthly_therms, PGE_GNR1, season) / monthly_therms) * 10

    local_delivery_per_dth = local_delivery_dollars_per_therm(utility_key, monthly_therms) * 10
    utility_bundled_per_dth = projected_utility_commodity_per_dth + transport_per_dth + local_delivery_per_dth + taxes_per_dth

    rows.append({
        "contract_month": r["contract_month"],
        "contract_date": contract_date,
        "nymex_is_live": r["nymex_is_live"],
        "NYMEX": nymex_per_dth,
        "Basis": basis_per_dth,
        "Commodity": market_commodity_per_dth,
        "Transportation": transport_per_dth,
        "Local Delivery": local_delivery_per_dth,
        "Utility Bundled Cost": utility_bundled_per_dth,
    })

stack_df = pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# THE INDICATIVE FIXED PRICE -- averaged over the selected term. All figures
# from here on are $/Dth.
#
# Core Price (transparent, real, sourced) = avg NYMEX + avg Basis + real
# tariff Transportation over the term. Shown broken out, on purpose --
# these three are provable from public data.
#
# Local Delivery + Advisory Fee + Taxes (opaque, combined into ONE number) --
# these genuinely vary by account/meter/contract, so they are shown as a
# single estimate with an explicit "connect with RFP" flag rather than a
# false-precision breakout.
# ---------------------------------------------------------------------------
term_window = stack_df.head(fixed_term_months)
avg_nymex = term_window["NYMEX"].mean()
avg_basis = term_window["Basis"].mean()
avg_transport = term_window["Transportation"].mean()
avg_local_delivery = term_window["Local Delivery"].mean()
avg_utility_bundled = term_window["Utility Bundled Cost"].mean()

core_price = avg_nymex + avg_basis + avg_transport
opaque_bucket = avg_local_delivery + advisory_fee_per_dth + taxes_per_dth
total_fixed_price = core_price + opaque_bucket

savings_per_dth = avg_utility_bundled - total_fixed_price
savings_pct = (savings_per_dth / avg_utility_bundled * 100) if avg_utility_bundled else 0
annual_savings = savings_per_dth * monthly_dth * 12

# ---------------------------------------------------------------------------
# Live data strip
# ---------------------------------------------------------------------------
live_nymex_tag = "🟢 live (yfinance)" if stack_df.iloc[0]["nymex_is_live"] else "🟡 cached (feed fallback)"
live_basis_tag = "🟢 live" if basis_result["is_live"] else "🟡 cached (local snapshot)"

nymex_delta_html = ""
if nymex_change is not None:
    arrow, color = ("▲", RED) if nymex_change > 0 else ("▼", GREEN) if nymex_change < 0 else ("—", SUBTEXT)
    nymex_delta_html = f'<span style="color:{color} !important;font-weight:700;">{arrow} ${abs(nymex_change):.3f} vs. {prior_close["date"]}</span>'
basis_delta_html = ""
if basis_change is not None:
    arrow, color = ("▲", RED) if basis_change > 0 else ("▼", GREEN) if basis_change < 0 else ("—", SUBTEXT)
    basis_delta_html = f'<span style="color:{color} !important;font-weight:700;">{arrow} ${abs(basis_change):.3f} vs. {prior_close["date"]}</span>'

c1, c2 = st.columns(2)
c1.markdown(f"""<div class="rfp-card"><div class="rfp-stat">${stack_df.iloc[0]['NYMEX']:.2f}</div>
<div class="rfp-stat-label">Front-month NYMEX ($/Dth) — {live_nymex_tag}</div>{nymex_delta_html}</div>""", unsafe_allow_html=True)
c2.markdown(f"""<div class="rfp-card"><div class="rfp-stat">${stack_df.iloc[0]['Basis']:+.2f}</div>
<div class="rfp-stat-label">{TICKER_LABELS.get(ticker, ticker)} basis ($/Dth) — {live_basis_tag}, as of {basis_result['as_of']}</div>{basis_delta_html}</div>""", unsafe_allow_html=True)

if not prior_close:
    st.caption("📌 First visit recorded today — come back tomorrow to see day-over-day price movement here.")

# ---------------------------------------------------------------------------
# Daily Buyer Signal
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="rfp-signal-card">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
            <div>
                <div style="color:{SUBTEXT}; font-size:0.85rem; text-transform:uppercase; letter-spacing:0.05em;">Today's RFP Buyer Signal — {utility_key}</div>
                <div style="font-size:1.6rem; font-weight:800; color:{signal_color};">{signal_label}</div>
            </div>
            <div style="max-width:520px; color:{TEXT}; font-size:0.95rem;">{signal_text}</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# THE INDICATIVE FIXED PRICE BUILD -- the core conversion moment
# ---------------------------------------------------------------------------
st.subheader(f"Your {fixed_term_months}-Month Indicative Fixed Price — {utility_key}")
st.caption("All figures below are in **\\$/Dth** (1 Dth ≈ 1 MMBtu ≈ 10 therms) for consistency across the whole calculator.")

b1, b2, b3, b4 = st.columns(4)
b1.markdown(f"""<div class="rfp-card"><div class="rfp-stat">${avg_nymex:.3f}<span style="font-size:1rem;">/Dth</span></div>
<div class="rfp-stat-label">NYMEX (avg, {fixed_term_months}-mo)</div></div>""", unsafe_allow_html=True)
b2.markdown(f"""<div class="rfp-card plus"><div class="rfp-stat">${avg_basis:+.3f}<span style="font-size:1rem;">/Dth</span></div>
<div class="rfp-stat-label">+ Basis (avg, {fixed_term_months}-mo)</div></div>""", unsafe_allow_html=True)
b3.markdown(f"""<div class="rfp-card plus"><div class="rfp-stat">${avg_transport:.3f}<span style="font-size:1rem;">/Dth</span></div>
<div class="rfp-stat-label">+ Transportation (tariff)</div></div>""", unsafe_allow_html=True)
b4.markdown(f"""<div class="rfp-card plus" style="border-left-color:{GOLD};"><div class="rfp-stat" style="color:{GOLD};">${core_price:.3f}<span style="font-size:1rem;">/Dth</span></div>
<div class="rfp-stat-label">= Core Price</div></div>""", unsafe_allow_html=True)

st.markdown(f"""<div class="rfp-card opaque">
<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
    <div>
        <div class="rfp-stat" style="color:{ORANGE};">≈ ${opaque_bucket:.3f}<span style="font-size:1rem;">/Dth (estimated)</span></div>
        <div class="rfp-stat-label">+ Local Delivery, Advisory &amp; Taxes</div>
    </div>
    <div style="max-width:480px; color:{TEXT}; font-size:0.88rem;">
        ⚠️ <b>This is an estimate.</b> Your actual Local Delivery, Advisory &amp; Taxes depend on your specific
        meter, volume tier, and contract terms — this is exactly why RFP shows this as one combined number
        instead of a false-precision breakdown. <b>Connect with RFP for your exact, guaranteed rate.</b>
    </div>
</div>
</div>""", unsafe_allow_html=True)

st.markdown(f"""<div class="rfp-card total">
<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
    <div>
        <div class="rfp-stat" style="font-size:2.3rem;">${total_fixed_price:.3f}<span style="font-size:1.1rem;">/Dth</span></div>
        <div class="rfp-stat-label">= Your {fixed_term_months}-Month Indicative Fixed Price</div>
    </div>
    <div>
        <div class="rfp-stat" style="color:{GREEN if savings_per_dth>=0 else RED}; font-size:2.3rem;">${savings_per_dth:+.3f}/Dth</div>
        <div class="rfp-stat-label">vs. {utility_key} projected bundled rate (${avg_utility_bundled:.3f}/Dth)</div>
    </div>
    <div>
        <div class="rfp-stat" style="color:{GREEN if annual_savings>=0 else RED}; font-size:2.3rem;">${annual_savings:,.0f}</div>
        <div class="rfp-stat-label">Estimated annual $ impact at {monthly_dth:,} Dth/mo</div>
    </div>
</div>
</div>""", unsafe_allow_html=True)

st.markdown(
    f"""
    <div class="rfp-cta-banner">
        <b>This is an indicative price, not a quote.</b> The Core Price above is built from real, live market
        data — but the final number depends on details only RFP can confirm for your account. Get your real,
        guaranteed {fixed_term_months}-month fixed price below.
    </div>
    """,
    unsafe_allow_html=True,
)

with st.expander("📩 Get my real, guaranteed quote from RFP", expanded=False):
    st.markdown(f"""
    **Already carrying forward what you've entered:** {utility_key}, {monthly_dth:,} Dth/mo,
    {fixed_term_months}-month term — RFP gets a qualified, usage-sized lead, not just an email address.

    **What you'll get weekly, by email:**
    - Your indicative fixed price, recalculated for your citygate and usage
    - Front-month basis for both SoCal Citygate and PG&E Citygate
    - A short note on what actually moved the basis that week
    - RFP's daily market newsletter
    """)
    with st.form("lead_form"):
        name = st.text_input("Name")
        email = st.text_input("Work email")
        company = st.text_input("Company")
        newsletter_optin = st.checkbox("Also send me RFP's daily market newsletter", value=True)
        submitted = st.form_submit_button("Request my real quote")
        if submitted:
            # In production: post name/email/company/newsletter_optin PLUS the already-selected
            # utility_key, monthly_dth, fixed_term_months, and total_fixed_price to the
            # systeme.io funnel, so the weekly email can be personalized without asking the
            # lead to re-enter anything.
            st.success(f"Thanks {name or 'there'} — in production this posts to the systeme.io funnel "
                       f"with your {utility_key} / {monthly_dth:,} Dth/mo details attached, and "
                       f"triggers the nurture sequence + a booked-meeting link.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Forward Strips -- mirrors RFP's own "One Minute Energy Update" format
# ---------------------------------------------------------------------------
st.subheader("Forward Strips")
st.caption(f"{TICKER_LABELS.get(ticker, ticker)} all-in ($/Dth) — commodity (NYMEX + basis) only, same format as RFP's daily market update.")

stack_df["cal_year"] = stack_df["contract_date"].dt.year
stack_df["cal_month"] = stack_df["contract_date"].dt.month
next12_df = stack_df.head(12)
winter_next12 = next12_df[next12_df["cal_month"].isin([11, 12, 1, 2, 3])]
summer_next12 = next12_df[next12_df["cal_month"].isin([4, 5, 6, 7, 8, 9, 10])]

strip_cols = st.columns(5)
strip_defs = [
    ("6-Month Strip", stack_df.head(6)),
    ("12-Month Strip", stack_df.head(12)),
    ("24-Month Strip", stack_df.head(24)),
    ("Next Winter (Nov–Mar)", winter_next12),
    ("Next Summer (Apr–Oct)", summer_next12),
]
for col, (label, sub_df) in zip(strip_cols, strip_defs):
    val = sub_df["Commodity"].mean() if not sub_df.empty else None
    col.markdown(f"""<div class="rfp-card"><div class="rfp-stat" style="font-size:1.4rem;">{f'${val:.2f}' if val else '—'}</div>
    <div class="rfp-stat-label">{label}</div></div>""", unsafe_allow_html=True)

cal_years = sorted(stack_df["cal_year"].unique())[:4]
if cal_years:
    st.write("**Calendar Year Averages**")
    cy_cols = st.columns(len(cal_years))
    for col, yr in zip(cy_cols, cal_years):
        yr_avg = stack_df[stack_df["cal_year"] == yr]["Commodity"].mean()
        col.markdown(f"""<div class="rfp-card"><div class="rfp-stat" style="font-size:1.3rem;">${yr_avg:.2f}</div>
        <div class="rfp-stat-label">Calendar {yr}</div></div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Fixed vs. Utility, across the curve
# ---------------------------------------------------------------------------
st.subheader(f"Your Fixed Price vs. {utility_key} — What The Utility Can't Offer")
fig_structures = go.Figure()
fig_structures.add_trace(go.Scatter(
    x=stack_df["contract_month"], y=[total_fixed_price] * len(stack_df),
    name=f"Your {fixed_term_months}-Month Fixed Price", mode="lines", line=dict(color=GOLD, width=3, dash="dash"),
))
fig_structures.add_trace(go.Scatter(
    x=stack_df["contract_month"], y=stack_df["Utility Bundled Cost"],
    name=f"{utility_key} Bundled Rate (variable)", mode="lines+markers", line=dict(color=RED, width=2, dash="dot"),
))
fig_structures.update_layout(**dark_chart_layout(
    height=420, yaxis=dict(title=dict(text="$ per Dth")),
    legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40, b=20),
))
st.plotly_chart(fig_structures, use_container_width=True)
st.caption(
    f"The utility's rate is variable by regulatory design — it resets monthly with the market. Your "
    f"{fixed_term_months}-month fixed price is locked using today's forward curve. This is a real, "
    f"CPUC-documented category — competing CTAs already sell Fixed products in this exact market "
    f"(e.g. Spark Energy, XOOM Energy, Symmetry Energy on SoCalGas's own CTA cost-comparison tool)."
)

# ---------------------------------------------------------------------------
# Layer breakdown + pie -- 4 layers now: Core Price components + one opaque bucket
# ---------------------------------------------------------------------------
layers = ["NYMEX", "Basis", "Transportation", "Local Delivery, Advisory & Taxes"]
colors = ["#4FC3F7", GOLD, GREEN, ORANGE]

chart_df = stack_df.copy()
chart_df["Local Delivery, Advisory & Taxes"] = chart_df["Local Delivery"] + advisory_fee_per_dth + taxes_per_dth

fig = go.Figure()
for layer, color in zip(layers, colors):
    fig.add_trace(go.Scatter(
        x=chart_df["contract_month"], y=chart_df[layer], name=layer, mode="lines",
        stackgroup="one", line=dict(width=0.5, color=color), fillcolor=color,
    ))
fig.add_trace(go.Scatter(
    x=chart_df["contract_month"], y=chart_df["Utility Bundled Cost"],
    name=f"{utility_key} Bundled Rate", mode="lines+markers", line=dict(color=RED, width=3, dash="dot"),
))
fig.update_layout(**dark_chart_layout(
    height=440, yaxis=dict(title=dict(text="$ per Dth")),
    legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40, b=20),
))
st.plotly_chart(fig, use_container_width=True)
st.caption(
    "Stacked area = the real, transparent Core Price components plus the estimated combined Local "
    f"Delivery/Advisory/Taxes bucket, for every forward month. The dotted red line is {utility_key}'s "
    "rate, projected forward with the market plus the measured WACOG premium (see Data Sources tab)."
)

pie_col, table_col = st.columns([1, 1.3])
with pie_col:
    st.subheader("Where the Money Goes")
    avg_layers = chart_df[layers].mean()
    fig_pie = go.Figure(go.Pie(
        labels=layers, values=avg_layers.values, hole=0.45,
        marker=dict(colors=colors, line=dict(color=NAVY, width=2)),
        textinfo="label+percent", textfont=dict(color=NAVY, size=12),
    ))
    fig_pie.update_layout(**dark_chart_layout(height=380, showlegend=False, margin=dict(t=10, b=10)))
    st.plotly_chart(fig_pie, use_container_width=True)
    st.caption("Average share of the delivered cost across the forward curve at this usage level. The orange "
               "slice is the one number RFP can't finalize until you connect.")

with table_col:
    st.subheader("Next 3 Months — NYMEX & Basis")
    quote_time_str = dt.datetime.fromisoformat(nymex_fetched_at).strftime("%b %d, %Y %H:%M UTC") if nymex_fetched_at else "n/a"
    st.caption(f"NYMEX quotes as of {quote_time_str} · basis as of report date {basis_result['as_of']}")
    next3_cols = st.columns(3)
    for i in range(min(3, len(stack_df))):
        row = stack_df.iloc[i]
        combined = row["NYMEX"] + row["Basis"]
        live_tag = "🟢" if row["nymex_is_live"] else "🟡"
        with next3_cols[i]:
            st.markdown(f"""<div class="rfp-card">
                <div style="color:{SUBTEXT}; font-size:0.85rem;">{row['contract_month']} {live_tag}</div>
                <div class="rfp-stat" style="font-size:1.3rem;">${combined:.2f}<span style="font-size:0.85rem; color:{SUBTEXT} !important;">/Dth</span></div>
                <div class="rfp-stat-label">NYMEX ${row['NYMEX']:.2f} + Basis ${row['Basis']:+.2f}</div>
            </div>""", unsafe_allow_html=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Tariff transparency + data sources
# ---------------------------------------------------------------------------
st.header("The Real Data Behind This Calculator")

tab1, tab2, tab3 = st.tabs(["Forward Curve Detail", f"{utility_key} Tariff", "Data Sources"])

with tab1:
    display_df = chart_df[["contract_month", "NYMEX", "Basis", "Transportation",
                            "Local Delivery, Advisory & Taxes", "Utility Bundled Cost"]].copy()
    for col in display_df.columns[1:]:
        display_df[col] = display_df[col].map(lambda v: f"${v:.3f}")
    st.dataframe(display_df, hide_index=True, use_container_width=True)
    st.caption(
        f"All figures \\$/Dth. Your {fixed_term_months}-month Core Price averages the NYMEX + Basis + "
        f"Transportation columns above over the first {fixed_term_months} rows: \\${core_price:.3f}/Dth. "
        f"Add the estimated Local Delivery, Advisory & Taxes bucket (\\${opaque_bucket:.3f}/Dth) for the "
        f"full indicative total of \\${total_fixed_price:.3f}/Dth."
    )

with tab2:
    st.caption(
        "⚠️ The tables below are shown in the utility's own **native \\$/therm units**, exactly as published "
        "in their tariff sheets — deliberately NOT converted to Dth, so you can check these numbers directly "
        "against the source document. Everywhere else in this calculator uses \\$/Dth (1 Dth ≈ 10 therms)."
    )
    if utility_key == "SoCalGas":
        df_t = pd.DataFrame([{"Tier": t.label, "Bundled Core Rate (¢/therm)": t.rate_cents_per_therm}
                              for t in SOCALGAS_CORE_G10.core_tiers])
        df_tr = pd.DataFrame([{"Tier": t.label, "Transport-Only Rate (¢/therm)": t.rate_cents_per_therm}
                               for t in SOCALGAS_CORE_G10.transport_only_tiers])
        cc1, cc2 = st.columns(2)
        cc1.write("**Bundled (Schedule G-10, GN-10)**")
        cc1.dataframe(df_t, hide_index=True, use_container_width=True)
        cc2.write("**Transport-only (Schedule G-10, GT-10 / CAT)**")
        cc2.dataframe(df_tr, hide_index=True, use_container_width=True)
        st.caption(f"Local Delivery: 49.315¢/meter/day flat customer charge. "
                   f"Source: {SOCALGAS_CORE_G10.source_note}, effective {SOCALGAS_CORE_G10.effective_date}. "
                   f"[{SOCALGAS_CORE_G10.source_url}]({SOCALGAS_CORE_G10.source_url})")
    else:
        df_p = pd.DataFrame([
            {"Band": "First 4,000 therms/mo", "Summer ($/therm)": PGE_GNR1.transport_summer_first4000, "Winter ($/therm)": PGE_GNR1.transport_winter_first4000},
            {"Band": "Excess over 4,000 therms/mo", "Summer ($/therm)": PGE_GNR1.transport_summer_excess, "Winter ($/therm)": PGE_GNR1.transport_winter_excess},
        ])
        st.write(f"**Transportation — Procurement: ${PGE_GNR1.procurement_dollars_per_therm:.5f}/therm**")
        st.dataframe(df_p, hide_index=True, use_container_width=True)
        st.caption(f"Local Delivery: tiered daily customer charge by Average Daily Usage (ADU). "
                   f"Source: {PGE_GNR1.source_note}, effective {PGE_GNR1.effective_date}. "
                   f"[{PGE_GNR1.source_url}]({PGE_GNR1.source_url})")

with tab3:
    st.markdown(f"""
    - **NYMEX forward strip**: live via yfinance, per-contract-month tickers (e.g. `NGV26.NYM`), falling back to
      the settle price embedded in the Rogue Basis feed if a live quote isn't available for that contract.
      Treated as \\$/Dth (1 Dth ≈ 1 MMBtu, the native unit of the feed).
    - **Forward basis**: [Rogue Basis Scraper feed]({LIVE_BASIS_URL}), ticker `{ticker}`
      ({TICKER_LABELS.get(ticker, ticker)}), as of report date {basis_result['as_of']}. Also \\$/Dth-native.
    - **Transportation**: {utility_key}'s own published tariff (\\$/therm-native — see the tab above),
      converted to \\$/Dth (×10) here for consistency with the rest of the calculator.
    - **Core Price** = avg NYMEX + avg Basis + Transportation, averaged over your selected
      {fixed_term_months}-month term. Every input here is public, sourced data.
    - **Local Delivery**: {utility_key}'s own published fixed customer/meter charge (real, tariff-sourced,
      \\$/therm-native, converted to \\$/Dth) — combined into the opaque bucket below rather than broken out
      on its own, since it's shown alongside the Advisory Fee and Taxes as one number.
    - **Advisory Fee**: RFP's fee, adjustable above (default \\${ADVISORY_FEE_DEFAULT_PER_DTH:.2f}/Dth) — covers
      procurement strategy, market intelligence, and the senior-level relationship. This is where RFP's
      real, per-account pricing comes in, which is exactly why it isn't finalized on a public page.
    - **Taxes & Regulatory Fees**: adjustable above (default \\${TAXES_DEFAULT_PER_DTH:.2f}/Dth) — varies
      by exact jurisdiction and account specifics.
    - **Utility Bundled Cost (projected)**: forward market commodity (NYMEX+basis) + a WACOG premium of
      \\${wacog_premium_per_dth:+.2f}/Dth, measured from the gap between {utility_key}'s current published Cost of
      Gas and today's market commodity price. {utility_key}'s own tariff (Schedule G-CP) states this Cost of
      Gas figure already includes storage carrying costs, CPGA true-up adjustments, and Gas Cost Incentive
      Mechanism (GCIM) adjustments — i.e. it runs structurally above pure forward-market pricing, which is
      why the premium is projected forward rather than assuming a flat utility rate.
    - **Fixed pricing**: the utility's own default rate is explicitly variable, resetting monthly with the
      market by regulatory design (SoCalGas/PG&E's "Price to Compare"). Only CTAs/marketers can offer Fixed —
      confirmed via the CPUC's own CTA Cost Comparison tool, which shows real competitors (Spark Energy Gas,
      XOOM Energy, Symmetry Energy, Pacific Summit Energy) already selling Fixed-rate products in this exact
      market, typically with 12–24 month terms and early termination fees (\\$100–\\$400 in the disclosed
      examples). This prototype does not yet include an early termination fee in the estimate — add one
      before using this in a real customer-facing quote.
    """)

st.markdown("---")
st.caption(
    "Prototype built for pitch purposes. Utility tariff data sourced directly from SoCalGas and PG&E "
    "published rate schedules. NYMEX and basis data are live where available, with clearly labeled cached "
    "fallbacks otherwise. This is an indicative price only — not a binding quote. Connect with RFP Energy "
    "Solutions for your real, guaranteed number."
)
