import base64
import datetime as dt
import hmac
import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.tariffs import SOCALGAS_CORE_G10, PGE_GNR1, wacog_premium_dollars_per_therm
from utils.basis_data import get_basis_forward_curve, TICKER_LABELS, LIVE_BASIS_URL
from utils.nymex_data import build_nymex_forward_curve
from utils.daily_snapshot import record_and_get_prior_close
from utils.leads import (
    TAG_QUOTE_REQUESTED, get_secret, is_valid_email, make_token, submit_lead,
    tag_contact_by_email, verify_token,
)

BOOKING_URL = "https://cal.com/sean-dookie-geygqi/30min"


def _safe_eq(a: str, b: str) -> bool:
    """Constant-time string compare that never crashes on non-ASCII input."""
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


# ---------------------------------------------------------------------------
# Page config + dark theming
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="RFP Energy Solutions | Know What Your Natural Gas Should Cost",
    page_icon="🔥",
    layout="wide",
)

NAVY = "#0B1E3D"
CARD = "#132A50"
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
    """Shared Plotly dark-theme layout -- see prior versions for why this exists
    (font_color alone doesn't cascade to axis/legend sub-elements in Plotly)."""
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
        .rfp-card.total {{ border: 2px solid {GOLD}; background: rgba(242,201,76,0.08); }}
        .rfp-stat {{ font-size: 1.9rem; font-weight:800; color:{TEXT}; }}
        .rfp-stat-label {{ color:{SUBTEXT}; font-size:0.85rem; }}
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
        /* buttons: gold primary with navy text; quiet outlined secondary */
        [data-testid^="stBaseButton-primary"], [data-testid^="stBaseLinkButton-primary"] {{
            background-color: {GOLD} !important; border: none !important; }}
        [data-testid^="stBaseButton-primary"] p, [data-testid^="stBaseLinkButton-primary"] p {{
            color: {NAVY} !important; font-weight: 700 !important; }}
        [data-testid^="stBaseButton-secondary"], [data-testid^="stBaseLinkButton-secondary"] {{
            background-color: transparent !important; border: 1px solid {SUBTEXT} !important; }}
        [data-testid^="stBaseButton-secondary"] p, [data-testid^="stBaseLinkButton-secondary"] p {{
            color: {TEXT} !important; }}
        .rfp-pending {{
            background: rgba(143,163,196,0.08); border: 1px dashed {SUBTEXT};
            border-radius: 14px; padding: 1.3rem 1.6rem; margin: 1.2rem 0;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

UTILITY_OPTIONS = {
    "SoCalGas — SoCal Citygate": {"ticker": "SCB", "utility_key": "SoCalGas"},
    "PG&E — PG&E Citygate": {"ticker": "PGE", "utility_key": "PG&E"},
    "Piedmont — Transco Zone 5": {"ticker": "DKR", "utility_key": "Piedmont"},
}

# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------
# When shown inside the systeme.io page (?view=embed) the page already has the logo and headline,
# so the hero card is skipped there.
# (Streamlit reserves its own ?embed=true and hides it from the app, so we use our own ?view=embed flag.)
EMBEDDED = str(st.query_params.get("view", "")).lower() == "embed"

if not EMBEDDED:
    st.markdown(
        f"""
        <div class="rfp-hero">
            {'<img src="data:image/png;base64,' + _logo_b64 + '" style="height:56px;margin-bottom:0.4rem;">' if _logo_b64 else ''}
            <div>
            <span class="rfp-badge">PG&E</span>
            <span class="rfp-badge">SoCal</span>
            <span class="rfp-badge">Piedmont</span>
            </div>
            <h1>Know What Your Natural Gas Should Cost</h1>
            <p>Live NYMEX futures plus live forward basis — the market-based part of your price, built fresh
            every day. Everything else (transportation, delivery, and the rest) is different for every account,
            so get your real number from RFP.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# THE ONE TRIGGER: citygate dropdown drives everything below
# ---------------------------------------------------------------------------
st.header("NYMEX + Basis Calculator")
top_col1, top_col2, top_col3 = st.columns([1.3, 1, 1])
with top_col1:
    utility_label = st.selectbox("Your citygate", list(UTILITY_OPTIONS.keys()))
with top_col2:
    monthly_dth = st.number_input("Average monthly usage (Dth)", min_value=10, max_value=50000, value=500, step=10,
                                   help="1 Dth ≈ 1 MMBtu ≈ 10 therms.")
with top_col3:
    fixed_term_months = st.selectbox("Fixed price term", [6, 12, 24], index=1,
                                      help="How many forward months to average into your indicative price.")

utility_choice = UTILITY_OPTIONS[utility_label]
ticker = utility_choice["ticker"]
utility_key = utility_choice["utility_key"]

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

# NYMEX/basis are natively $/MMBtu, treated as $/Dth throughout (standard
# gas-industry convention). front_month_commodity_per_therm is an internal-
# only helper (divided by 10) purely so the WACOG comparison lines up with
# the utility's own $/therm-native published Cost of Gas figure.
front_month_commodity_per_dth = curve.iloc[0]["nymex_price"] + curve.iloc[0]["basis"]
front_month_commodity_per_therm = front_month_commodity_per_dth / 10.0
wacog_premium_per_therm = wacog_premium_dollars_per_therm(utility_key, front_month_commodity_per_therm)
wacog_premium_per_dth = wacog_premium_per_therm * 10 if wacog_premium_per_therm is not None else None
utility_comparison_available = wacog_premium_per_dth is not None

# ---------------------------------------------------------------------------
# Daily engagement: day-over-day price change + a curve-shape "Buyer Signal"
# ---------------------------------------------------------------------------
front_nymex_raw = float(curve.iloc[0]["nymex_price"])
front_basis_raw = float(curve.iloc[0]["basis"])
prior_close = record_and_get_prior_close(ticker, front_nymex_raw, front_basis_raw)

nymex_change = front_nymex_raw - prior_close["nymex"] if prior_close else None
basis_change = front_basis_raw - prior_close["basis"] if prior_close else None

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
# Build the forward curve stack -- NYMEX + Basis only, plus the utility's
# unbundled (commodity-only) comparison line where we have real data for it.
# ---------------------------------------------------------------------------
rows = []
for _, r in curve.iterrows():
    contract_date = pd.Timestamp(r["contract_date"])
    nymex_per_dth = r["nymex_price"]
    basis_per_dth = r["basis"]
    total_per_dth = nymex_per_dth + basis_per_dth
    utility_unbundled_per_dth = (total_per_dth + wacog_premium_per_dth) if utility_comparison_available else None

    rows.append({
        "contract_month": r["contract_month"],
        "contract_date": contract_date,
        "nymex_is_live": r["nymex_is_live"],
        "NYMEX": nymex_per_dth,
        "Basis": basis_per_dth,
        "Total": total_per_dth,
        "Utility Unbundled": utility_unbundled_per_dth,
    })

stack_df = pd.DataFrame(rows)

# ---------------------------------------------------------------------------
# THE INDICATIVE PRICE -- averaged over the selected term. $/Dth throughout.
# ---------------------------------------------------------------------------
term_window = stack_df.head(fixed_term_months)
avg_nymex = term_window["NYMEX"].mean()
avg_basis = term_window["Basis"].mean()
total_price = avg_nymex + avg_basis

if utility_comparison_available:
    avg_utility_unbundled = term_window["Utility Unbundled"].mean()
    savings_per_dth = avg_utility_unbundled - total_price
    savings_pct = (savings_per_dth / avg_utility_unbundled * 100) if avg_utility_unbundled else 0
    annual_savings = savings_per_dth * monthly_dth * 12
else:
    avg_utility_unbundled = savings_per_dth = savings_pct = annual_savings = None

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

# ---------------------------------------------------------------------------
# ACCESS GATE -- a free taste above this line, the full calculator below it.
#   * Everything above (inputs, live NYMEX/basis, Buyer Signal) is the "tease".
#   TWO MODES (chosen automatically by which secrets are set):
#   PASSWORD MODE  (ACCESS_PASSWORD set)  -- systeme.io owns sign-up. Members get a link
#       with ?pw=<password> (or type it). Visitors without it see a lock screen with a
#       "Get free access" button to OPTIN_URL. Checked server-side: nothing gated is sent
#       to the browser until it passes. No systeme.io API key needed.
#   EMAIL MODE     (no ACCESS_PASSWORD)   -- the in-app email form creates the contact in
#       systeme.io through its API (needs SYSTEMEIO_API_KEY). Fails OPEN if the API is down.
#   Always available:  ?admin=<ADMIN_KEY> demo bypass (no lead).  ?k=<token> signed return
#   link (email mode).  ?src=<name> traffic source (email mode).
# ---------------------------------------------------------------------------
GATE_SECRET = get_secret("GATE_SECRET")
ADMIN_KEY = get_secret("ADMIN_KEY")
ACCESS_PASSWORD = get_secret("ACCESS_PASSWORD").strip()
OPTIN_URL = get_secret("OPTIN_URL").strip()
PASSWORD_MODE = bool(ACCESS_PASSWORD)
lead_source = str(st.query_params.get("src", "direct"))[:40]

if not st.session_state.get("unlocked"):
    _token = st.query_params.get("k")
    _admin = st.query_params.get("admin")
    _pw = st.query_params.get("pw")
    _token_email = verify_token(str(_token), GATE_SECRET) if _token else None
    if _token_email:
        st.session_state.update(unlocked=True, lead_email=_token_email)
    elif ADMIN_KEY and _admin and _safe_eq(_admin, ADMIN_KEY):
        st.session_state.update(unlocked=True, lead_email="")
    elif PASSWORD_MODE and _pw and _safe_eq(str(_pw).strip(), ACCESS_PASSWORD):
        st.session_state.update(unlocked=True, lead_email="")

if PASSWORD_MODE and not st.session_state.get("unlocked"):
    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader(f"🔒 Your {fixed_term_months}-Month Price — {utility_key}")
    p1, p2, p3 = st.columns(3)
    for col, label in ((p1, f"NYMEX (avg, {fixed_term_months}-mo)"), (p2, "+ Basis (avg)"), (p3, "= Total")):
        col.markdown(
            f'''<div class="rfp-card"><div class="rfp-stat" style="filter:blur(8px);user-select:none;">$8.888<span style="font-size:1rem;">/Dth</span></div>
            <div class="rfp-stat-label">{label}</div></div>''', unsafe_allow_html=True)
    st.markdown(
        f'''
        <div class="rfp-signal-card">
            <div style="font-size:1.25rem; font-weight:800; color:{GOLD}; margin-bottom:0.4rem;">
                Free for RFP list members.</div>
            <div style="color:{TEXT}; font-size:0.95rem; line-height:1.55;">
                Sign up free and you'll get, instantly:<br>
                &bull; Your {fixed_term_months}-month <b>{utility_key}</b> price, built from live NYMEX + basis<br>
                &bull; Forward strips (6/12/24-month, winter/summer, calendar years)<br>
                &bull; The &ldquo;Your Price vs. the utility&rdquo; chart<br>
                &bull; RFP's Weekly Quote by email
            </div>
        </div>''', unsafe_allow_html=True)
    if OPTIN_URL:
        st.link_button("Get free access  →", OPTIN_URL, type="primary")
    st.markdown("**Already a member?** Enter your member password:")
    _fails = st.session_state.get("pw_fails", 0)
    if _fails >= 5:
        st.error("Too many incorrect attempts. Please use the link on your welcome page, or sign up again above.")
    else:
        with st.form("pw_form"):
            _pw_in = st.text_input("Member password", type="password")
            _pw_go = st.form_submit_button("Unlock")
        if _pw_go:
            if _safe_eq(_pw_in.strip(), ACCESS_PASSWORD):
                st.session_state.update(unlocked=True, lead_email="")
                st.rerun()
            else:
                st.session_state["pw_fails"] = _fails + 1
                time.sleep(1)
                if st.session_state["pw_fails"] >= 5:
                    st.rerun()  # re-render immediately in the locked-out state
                st.error("That password isn't right. It's on your welcome page.")
    st.stop()

if not st.session_state.get("unlocked"):
    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader(f"🔒 Your {fixed_term_months}-Month Price — {utility_key}")
    p1, p2, p3 = st.columns(3)
    for col, label in ((p1, f"NYMEX (avg, {fixed_term_months}-mo)"), (p2, "+ Basis (avg)"), (p3, "= Total")):
        col.markdown(
            f'''<div class="rfp-card"><div class="rfp-stat" style="filter:blur(8px);user-select:none;">$8.888<span style="font-size:1rem;">/Dth</span></div>
            <div class="rfp-stat-label">{label}</div></div>''', unsafe_allow_html=True)

    st.markdown(
        f'''
        <div class="rfp-signal-card">
            <div style="font-size:1.25rem; font-weight:800; color:{GOLD}; margin-bottom:0.4rem;">
                Unlock your number — free, 10 seconds.</div>
            <div style="color:{TEXT}; font-size:0.95rem; line-height:1.55;">
                You'll get, instantly:<br>
                &bull; Your {fixed_term_months}-month <b>{utility_key}</b> price, built from live NYMEX + basis<br>
                &bull; Forward strips (6/12/24-month, winter/summer, calendar years)<br>
                &bull; The &ldquo;Your Price vs. the utility&rdquo; chart<br>
                &bull; Then every Monday: your indicative quote, the basis, and what moved the market
            </div>
        </div>''', unsafe_allow_html=True)

    with st.form("gate_form"):
        g1, g2 = st.columns(2)
        gate_first = g1.text_input("First name")
        gate_email = g2.text_input("Work email")
        gate_company = st.text_input("Company (optional)")
        gate_daily = st.checkbox("Also send me RFP's daily market newsletter", value=True)
        gate_go = st.form_submit_button("Unlock my price  →", type="primary")
    st.caption(
        "By continuing you'll receive RFP's weekly indicative quote (and the daily newsletter if checked). "
        "Unsubscribe anytime. We never sell or share your email."
    )
    if gate_go:
        if not is_valid_email(gate_email):
            st.error("Please enter a valid email address.")
        elif not gate_first.strip():
            st.error("Please add your first name so we can personalize your weekly quote.")
        else:
            _result = submit_lead(
                gate_email, first_name=gate_first.strip(), company=gate_company.strip(),
                citygate=utility_key, monthly_dth=monthly_dth, term_months=fixed_term_months,
                source=lead_source, daily_optin=gate_daily,
            )
            print(f"[gate] lead captured mode={_result.get('mode')} ok={_result.get('ok')}")
            st.session_state.update(unlocked=True, lead_email=gate_email.strip().lower(), just_unlocked=True)
            if GATE_SECRET:
                st.query_params["k"] = make_token(gate_email, GATE_SECRET)
            st.rerun()
    st.stop()

if st.session_state.pop("just_unlocked", False):
    st.success("✅ You're in. Your weekly quote starts Monday. Bookmark this page — the link is personal "
               "and keeps you signed in.")


st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# THE PRICE BUILD -- NYMEX + Basis = Total. That's the whole calculation.
# ---------------------------------------------------------------------------
st.subheader(f"Your {fixed_term_months}-Month Price — {utility_key}")
st.caption("All figures in \\$/Dth (1 Dth ≈ 1 MMBtu ≈ 10 therms).")

b1, b2, b3 = st.columns(3)
b1.markdown(f"""<div class="rfp-card"><div class="rfp-stat">${avg_nymex:.3f}<span style="font-size:1rem;">/Dth</span></div>
<div class="rfp-stat-label">NYMEX (avg, {fixed_term_months}-mo)</div></div>""", unsafe_allow_html=True)
b2.markdown(f"""<div class="rfp-card plus"><div class="rfp-stat">${avg_basis:+.3f}<span style="font-size:1rem;">/Dth</span></div>
<div class="rfp-stat-label">+ Basis (avg, {fixed_term_months}-mo)</div></div>""", unsafe_allow_html=True)
b3.markdown(f"""<div class="rfp-card total"><div class="rfp-stat" style="color:{GOLD};">${total_price:.3f}<span style="font-size:1rem;">/Dth</span></div>
<div class="rfp-stat-label">= Total</div></div>""", unsafe_allow_html=True)

if utility_comparison_available:
    st.markdown(f"""<div class="rfp-card">
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
        <div>
            <div class="rfp-stat" style="color:{GREEN if savings_per_dth>=0 else RED};">${savings_per_dth:+.3f}/Dth</div>
            <div class="rfp-stat-label">vs. {utility_key}'s unbundled commodity rate (${avg_utility_unbundled:.3f}/Dth)</div>
        </div>
        <div>
            <div class="rfp-stat" style="color:{GREEN if annual_savings>=0 else RED};">${annual_savings:,.0f}</div>
            <div class="rfp-stat-label">Estimated annual $ impact at {monthly_dth:,} Dth/mo</div>
        </div>
    </div>
    </div>""", unsafe_allow_html=True)
else:
    st.markdown(f"""<div class="rfp-pending">
    <b>⏳ {utility_key} utility comparison coming soon.</b> {utility_key}'s commodity rate is published across
    several rate-class-specific schedules with a separately-adjusting monthly cost-of-gas charge, rather than
    one clean headline figure like SoCalGas and PG&E publish — we're sourcing a confirmed number before
    showing a comparison here rather than estimating one. NYMEX + Basis above is real and live either way.
    </div>""", unsafe_allow_html=True)

st.markdown(
    f"""
    <div class="rfp-cta-banner">
        <b>This is NYMEX + Basis only — the market-based part of your price.</b> Transportation, local
        delivery, and everything else is different for every account, which is exactly why RFP doesn't
        guess at it here. Connect with RFP for your real, live {fixed_term_months}-month quote.
    </div>
    """,
    unsafe_allow_html=True,
)

st.subheader("Want the real number?")
_lead_email = st.session_state.get("lead_email", "")
_api_ready = bool(get_secret("SYSTEMEIO_API_KEY"))
if st.session_state.get("quote_requested"):
    st.success(f"Got it. RFP will follow up with your live {fixed_term_months}-month {utility_key} quote. "
               "Want to move faster? Pick a time that works:")
    st.link_button("Book a 15-minute review", BOOKING_URL)
elif _lead_email or _api_ready:
    q1, q2 = st.columns(2)
    _req_email = _lead_email
    if not _lead_email:  # password-mode member: ask for the email once, only when they want a quote
        _req_email = q1.text_input("Your work email", key="req_email").strip().lower()
    if q1.button("Request my real, live quote", type="primary"):
        if not is_valid_email(_req_email):
            q1.error("Please enter a valid email address.")
        else:
            tag_contact_by_email(
                _req_email, TAG_QUOTE_REQUESTED,
                fields={"citygate": utility_key, "monthly_dth": monthly_dth, "term_months": fixed_term_months},
            )
            st.session_state["quote_requested"] = True
            st.rerun()
    q2.link_button("Book a 15-minute review", BOOKING_URL)
else:
    st.link_button("Book a 15-minute review", BOOKING_URL)

st.markdown("---")

# ---------------------------------------------------------------------------
# Forward Strips -- mirrors RFP's own "One Minute Energy Update" format
# ---------------------------------------------------------------------------
st.subheader("Forward Strips")
st.caption(f"{TICKER_LABELS.get(ticker, ticker)} all-in ($/Dth) — NYMEX + basis, same format as RFP's daily market update.")

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
    val = sub_df["Total"].mean() if not sub_df.empty else None
    col.markdown(f"""<div class="rfp-card"><div class="rfp-stat" style="font-size:1.4rem;">{f'${val:.2f}' if val else '—'}</div>
    <div class="rfp-stat-label">{label}</div></div>""", unsafe_allow_html=True)

cal_years = sorted(stack_df["cal_year"].unique())[:4]
if cal_years:
    st.write("**Calendar Year Averages**")
    cy_cols = st.columns(len(cal_years))
    for col, yr in zip(cy_cols, cal_years):
        yr_avg = stack_df[stack_df["cal_year"] == yr]["Total"].mean()
        col.markdown(f"""<div class="rfp-card"><div class="rfp-stat" style="font-size:1.3rem;">${yr_avg:.2f}</div>
        <div class="rfp-stat-label">Calendar {yr}</div></div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Your Price vs. Utility -- the chart that sells it
# ---------------------------------------------------------------------------
if utility_comparison_available:
    st.subheader(f"Your Price vs. {utility_key} — What The Utility Can't Offer")
    fig_structures = go.Figure()
    fig_structures.add_trace(go.Scatter(
        x=stack_df["contract_month"], y=[total_price] * len(stack_df),
        name=f"Your {fixed_term_months}-Month Price", mode="lines", line=dict(color=GOLD, width=3, dash="dash"),
    ))
    fig_structures.add_trace(go.Scatter(
        x=stack_df["contract_month"], y=stack_df["Utility Unbundled"],
        name=f"{utility_key} Unbundled Rate (variable)", mode="lines+markers", line=dict(color=RED, width=2, dash="dot"),
    ))
    fig_structures.update_layout(**dark_chart_layout(
        height=420, yaxis=dict(title=dict(text="$ per Dth")),
        legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40, b=20),
    ))
    st.plotly_chart(fig_structures, use_container_width=True)
    st.caption(
        f"The utility's rate is variable by regulatory design — it resets monthly with the market. Your "
        f"{fixed_term_months}-month price is locked using today's forward curve. Both lines are commodity-only "
        f"(unbundled) — transportation and delivery are excluded from both sides for a fair comparison, since "
        f"those apply the same way regardless of who supplies your gas."
    )

    st.subheader("NYMEX + Basis — Layer by Layer")
    layers = ["NYMEX", "Basis"]
    colors = ["#4FC3F7", GOLD]
    fig = go.Figure()
    for layer, color in zip(layers, colors):
        fig.add_trace(go.Scatter(
            x=stack_df["contract_month"], y=stack_df[layer], name=layer, mode="lines",
            stackgroup="one", line=dict(width=0.5, color=color), fillcolor=color,
        ))
    fig.add_trace(go.Scatter(
        x=stack_df["contract_month"], y=stack_df["Utility Unbundled"],
        name=f"{utility_key} Unbundled Rate", mode="lines+markers", line=dict(color=RED, width=3, dash="dot"),
    ))
    fig.update_layout(**dark_chart_layout(
        height=400, yaxis=dict(title=dict(text="$ per Dth")),
        legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40, b=20),
    ))
    st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Next 3 Months
# ---------------------------------------------------------------------------
st.subheader("Next 3 Months — NYMEX & Basis")
quote_time_str = dt.datetime.fromisoformat(nymex_fetched_at).strftime("%b %d, %Y %H:%M UTC") if nymex_fetched_at else "n/a"
st.caption(f"NYMEX quotes as of {quote_time_str} · basis as of report date {basis_result['as_of']}")
next3_cols = st.columns(3)
for i in range(min(3, len(stack_df))):
    row = stack_df.iloc[i]
    live_tag = "🟢" if row["nymex_is_live"] else "🟡"
    with next3_cols[i]:
        st.markdown(f"""<div class="rfp-card">
            <div style="color:{SUBTEXT}; font-size:0.85rem;">{row['contract_month']} {live_tag}</div>
            <div class="rfp-stat" style="font-size:1.3rem;">${row['Total']:.2f}<span style="font-size:0.85rem; color:{SUBTEXT} !important;">/Dth</span></div>
            <div class="rfp-stat-label">NYMEX ${row['NYMEX']:.2f} + Basis ${row['Basis']:+.2f}</div>
        </div>""", unsafe_allow_html=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Data + tariff transparency
# ---------------------------------------------------------------------------
st.header("The Real Data Behind This Calculator")

tab1, tab2, tab3 = st.tabs(["Forward Curve Detail", f"{utility_key} Tariff Reference", "Data Sources"])

with tab1:
    display_cols = ["contract_month", "NYMEX", "Basis", "Total"] + (["Utility Unbundled"] if utility_comparison_available else [])
    display_df = stack_df[display_cols].copy()
    for col in display_df.columns[1:]:
        display_df[col] = display_df[col].map(lambda v: f"${v:.3f}")
    st.dataframe(display_df, hide_index=True, use_container_width=True)

with tab2:
    st.caption(
        "Shown for context and credibility — RFP knows these real, published rates, but they aren't used "
        "in the Total above, since Transportation and Local Delivery are different for every account. "
        "Figures below are the utility's own **native $/therm units**, exactly as published in their tariff "
        "sheets, deliberately not converted to Dth."
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
        st.caption(f"Source: {SOCALGAS_CORE_G10.source_note}, effective {SOCALGAS_CORE_G10.effective_date}. "
                   f"[{SOCALGAS_CORE_G10.source_url}]({SOCALGAS_CORE_G10.source_url})")
    elif utility_key == "PG&E":
        df_p = pd.DataFrame([
            {"Band": "First 4,000 therms/mo", "Summer ($/therm)": PGE_GNR1.transport_summer_first4000, "Winter ($/therm)": PGE_GNR1.transport_winter_first4000},
            {"Band": "Excess over 4,000 therms/mo", "Summer ($/therm)": PGE_GNR1.transport_summer_excess, "Winter ($/therm)": PGE_GNR1.transport_winter_excess},
        ])
        st.write(f"**Transportation — Procurement: ${PGE_GNR1.procurement_dollars_per_therm:.5f}/therm**")
        st.dataframe(df_p, hide_index=True, use_container_width=True)
        st.caption(f"Source: {PGE_GNR1.source_note}, effective {PGE_GNR1.effective_date}. "
                   f"[{PGE_GNR1.source_url}]({PGE_GNR1.source_url})")
    else:
        st.info(
            "Piedmont's tariff reference is still being sourced. Their rate structure splits Transportation, "
            "Local Delivery, and Cost of Gas across multiple rate-class-specific schedules (Fg-3, Ag-1, Ig-4, "
            "Pg-10, and others) with a separately-adjusting monthly PGA, rather than one clean published "
            "figure per component the way SoCalGas and PG&E disclose theirs — this tab will populate once "
            "that's confirmed."
        )

with tab3:
    st.markdown(f"""
    - **NYMEX forward strip**: live via yfinance, per-contract-month tickers (e.g. `NGV26.NYM`), falling back to
      the settle price embedded in the Rogue Basis feed if a live quote isn't available for that contract.
      Treated as \\$/Dth (1 Dth ≈ 1 MMBtu, the native unit of the feed).
    - **Forward basis**: [Rogue Basis Scraper feed]({LIVE_BASIS_URL}), ticker `{ticker}`
      ({TICKER_LABELS.get(ticker, ticker)}), as of report date {basis_result['as_of']}. Also \\$/Dth-native.
    - **Total** = NYMEX + Basis, averaged over your selected {fixed_term_months}-month term. That's the whole
      calculation — no Transportation, Local Delivery, or fees are included, since those genuinely differ by
      account and RFP would rather show nothing than guess.
    {"- **Utility Unbundled Rate**: " + utility_key + "'s own published commodity-only Cost of Gas, projected forward using the gap between their current rate and today's market price (a real, measured premium — not assumed). Both sides of the comparison exclude Transportation and Local Delivery, for a fair, apples-to-apples comparison of just the shoppable part of the bill." if utility_comparison_available else "- **" + utility_key + " utility comparison**: not yet available — see the Tariff Reference tab for why."}
    """)

st.markdown("---")
st.caption(
    "Prototype built for pitch purposes. NYMEX and basis data are live where available, with clearly labeled "
    "cached fallbacks otherwise. This is indicative only — not a binding quote. Connect with RFP Energy "
    "Solutions for your real, live number."
)



# import base64
# import datetime as dt
# from pathlib import Path

# import pandas as pd
# import plotly.graph_objects as go
# import streamlit as st

# from utils.tariffs import SOCALGAS_CORE_G10, PGE_GNR1, wacog_premium_dollars_per_therm
# from utils.basis_data import get_basis_forward_curve, TICKER_LABELS, LIVE_BASIS_URL
# from utils.nymex_data import build_nymex_forward_curve
# from utils.daily_snapshot import record_and_get_prior_close

# # ---------------------------------------------------------------------------
# # Page config + dark theming
# # ---------------------------------------------------------------------------
# st.set_page_config(
#     page_title="RFP Energy Solutions | Know What Your Natural Gas Should Cost",
#     page_icon="🔥",
#     layout="wide",
# )

# NAVY = "#0B1E3D"
# CARD = "#132A50"
# CARD_BORDER = "rgba(255,255,255,0.08)"
# GOLD = "#F2C94C"
# GREEN = "#2ECC71"
# CYAN = "#2F80ED"
# RED = "#E74C3C"
# ORANGE = "#E67E22"
# TEXT = "#EAF0FA"
# SUBTEXT = "#8FA3C4"

# LOGO_PATH = Path(__file__).parent / "assets" / "rfpes-logo-white.png"
# _logo_b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode() if LOGO_PATH.exists() else None


# def dark_chart_layout(**overrides) -> dict:
#     """Shared Plotly dark-theme layout -- see prior versions for why this exists
#     (font_color alone doesn't cascade to axis/legend sub-elements in Plotly)."""
#     base = {
#         "plot_bgcolor": CARD,
#         "paper_bgcolor": NAVY,
#         "font": dict(color=TEXT),
#         "xaxis": dict(color=TEXT, gridcolor="rgba(255,255,255,0.10)", linecolor="rgba(255,255,255,0.25)",
#                       tickfont=dict(color=TEXT), title=dict(font=dict(color=TEXT))),
#         "yaxis": dict(color=TEXT, gridcolor="rgba(255,255,255,0.10)", linecolor="rgba(255,255,255,0.25)",
#                       tickfont=dict(color=TEXT), title=dict(font=dict(color=TEXT))),
#         "legend": dict(font=dict(color=TEXT)),
#     }
#     for key in ("xaxis", "yaxis", "legend"):
#         if key in overrides:
#             merged = dict(base[key])
#             merged.update(overrides.pop(key))
#             base[key] = merged
#     base.update(overrides)
#     return base


# st.markdown(
#     f"""
#     <style>
#         .stApp {{ background-color: {NAVY}; }}
#         [data-testid="stHeader"] {{ background-color: {NAVY}; }}
#         [data-testid="stSidebar"] {{ background-color: {CARD}; }}
#         html, body, .stMarkdown, .stCaption, p, span, label, .stTextInput label,
#         .stNumberInput label, .stSelectbox label, .stSlider label, .stRadio label {{ color: {TEXT} !important; }}
#         h1, h2, h3, h4 {{ color: {TEXT} !important; }}
#         .stTabs [data-baseweb="tab"] {{ color: {SUBTEXT}; }}
#         .stTabs [aria-selected="true"] {{ color: {GOLD} !important; }}
#         [data-testid="stDataFrame"] {{ background-color: {CARD}; }}
#         .rfp-hero {{
#             background: linear-gradient(135deg, {CARD} 0%, #0E2244 100%);
#             padding: 2rem 2.2rem; border-radius: 14px; color: {TEXT}; margin-bottom: 1.5rem;
#             border: 1px solid {CARD_BORDER};
#         }}
#         .rfp-hero h1 {{ color: {TEXT} !important; font-size: 2.1rem; margin: 0.8rem 0 0.3rem 0; }}
#         .rfp-hero p {{ color: {SUBTEXT}; font-size: 1.05rem; }}
#         .rfp-badge {{
#             display:inline-block; background:{GOLD}; color:{NAVY} !important; font-weight:700;
#             padding: 3px 10px; border-radius: 20px; font-size: 0.78rem; margin-right:8px;
#         }}
#         .rfp-card {{
#             background:{CARD}; border-radius: 12px; padding: 1.2rem 1.4rem;
#             box-shadow: 0 1px 4px rgba(0,0,0,0.25); margin-bottom: 1rem;
#             border: 1px solid {CARD_BORDER};
#         }}
#         .rfp-card.plus {{ border-left: 3px solid {CYAN}; }}
#         .rfp-card.total {{ border: 2px solid {GOLD}; background: rgba(242,201,76,0.08); }}
#         .rfp-stat {{ font-size: 1.9rem; font-weight:800; color:{TEXT}; }}
#         .rfp-stat-label {{ color:{SUBTEXT}; font-size:0.85rem; }}
#         .rfp-signal-card {{
#             background: linear-gradient(135deg, {CARD} 0%, #0E2244 100%);
#             border-radius: 14px; padding: 1.4rem 1.6rem; margin-bottom: 1.5rem;
#             border: 1px solid {CARD_BORDER};
#         }}
#         .rfp-cta-banner {{
#             background: linear-gradient(135deg, {ORANGE} 0%, #C0392B 100%);
#             border-radius: 14px; padding: 1.3rem 1.6rem; margin: 1.2rem 0;
#             color: white;
#         }}
#         .rfp-cta-banner b {{ color: white; }}
#         .rfp-pending {{
#             background: rgba(143,163,196,0.08); border: 1px dashed {SUBTEXT};
#             border-radius: 14px; padding: 1.3rem 1.6rem; margin: 1.2rem 0;
#         }}
#     </style>
#     """,
#     unsafe_allow_html=True,
# )

# UTILITY_OPTIONS = {
#     "SoCalGas — SoCal Citygate": {"ticker": "SCB", "utility_key": "SoCalGas"},
#     "PG&E — PG&E Citygate": {"ticker": "PGE", "utility_key": "PG&E"},
#     "Piedmont — Transco Zone 5": {"ticker": "DKR", "utility_key": "Piedmont"},
# }

# # ---------------------------------------------------------------------------
# # Hero
# # ---------------------------------------------------------------------------
# st.markdown(
#     f"""
#     <div class="rfp-hero">
#         {'<img src="data:image/png;base64,' + _logo_b64 + '" style="height:56px;margin-bottom:0.4rem;">' if _logo_b64 else ''}
#         <div>
#         <span class="rfp-badge">PG&E</span>
#         <span class="rfp-badge">SoCal</span>
#         <span class="rfp-badge">SoCal</span>
#         </div>
#         <h1>Know What Your Natural Gas Should Cost</h1>
#         <p>Live NYMEX futures plus live forward basis — the market-based part of your price, built fresh
#         every day. Everything else (transportation, delivery, and the rest) is different for every account,
#         so get your real number from RFP.</p>
#     </div>
#     """,
#     unsafe_allow_html=True,
# )

# # ---------------------------------------------------------------------------
# # THE ONE TRIGGER: citygate dropdown drives everything below
# # ---------------------------------------------------------------------------
# st.header("NYMEX + Basis Calculator")
# top_col1, top_col2, top_col3 = st.columns([1.3, 1, 1])
# with top_col1:
#     utility_label = st.selectbox("Your citygate", list(UTILITY_OPTIONS.keys()))
# with top_col2:
#     monthly_dth = st.number_input("Average monthly usage (Dth)", min_value=10, max_value=50000, value=500, step=10,
#                                    help="1 Dth ≈ 1 MMBtu ≈ 10 therms.")
# with top_col3:
#     fixed_term_months = st.selectbox("Fixed price term", [6, 12, 24], index=1,
#                                       help="How many forward months to average into your indicative price.")

# utility_choice = UTILITY_OPTIONS[utility_label]
# ticker = utility_choice["ticker"]
# utility_key = utility_choice["utility_key"]

# # ---------------------------------------------------------------------------
# # Pull live data: forward basis (Rogue Basis feed) -> forward NYMEX (yfinance)
# # ---------------------------------------------------------------------------
# basis_result = get_basis_forward_curve(ticker)
# basis_curve = basis_result["curve"]

# if basis_curve.empty:
#     st.error(f"No forward basis data found for ticker '{ticker}'. Check the data feed.")
#     st.stop()

# curve, nymex_fetched_at = build_nymex_forward_curve(basis_curve)
# curve = curve.sort_values("contract_date").reset_index(drop=True)

# # NYMEX/basis are natively $/MMBtu, treated as $/Dth throughout (standard
# # gas-industry convention). front_month_commodity_per_therm is an internal-
# # only helper (divided by 10) purely so the WACOG comparison lines up with
# # the utility's own $/therm-native published Cost of Gas figure.
# front_month_commodity_per_dth = curve.iloc[0]["nymex_price"] + curve.iloc[0]["basis"]
# front_month_commodity_per_therm = front_month_commodity_per_dth / 10.0
# wacog_premium_per_therm = wacog_premium_dollars_per_therm(utility_key, front_month_commodity_per_therm)
# wacog_premium_per_dth = wacog_premium_per_therm * 10 if wacog_premium_per_therm is not None else None
# utility_comparison_available = wacog_premium_per_dth is not None

# # ---------------------------------------------------------------------------
# # Daily engagement: day-over-day price change + a curve-shape "Buyer Signal"
# # ---------------------------------------------------------------------------
# front_nymex_raw = float(curve.iloc[0]["nymex_price"])
# front_basis_raw = float(curve.iloc[0]["basis"])
# prior_close = record_and_get_prior_close(ticker, front_nymex_raw, front_basis_raw)

# nymex_change = front_nymex_raw - prior_close["nymex"] if prior_close else None
# basis_change = front_basis_raw - prior_close["basis"] if prior_close else None

# next12 = curve.iloc[1:13]
# if len(next12) >= 3:
#     forward_avg_commodity = (next12["nymex_price"] + next12["basis"]).mean()
#     signal_gap_pct = (forward_avg_commodity - front_month_commodity_per_dth) / front_month_commodity_per_dth * 100
#     if signal_gap_pct > 4:
#         signal_label, signal_color = "🟢 Consider Locking", GREEN
#         signal_text = (f"The forward curve is pricing the next 12 months an average of {signal_gap_pct:.1f}% "
#                         f"above today's front-month price — the market expects higher prices ahead.")
#     elif signal_gap_pct < -4:
#         signal_label, signal_color = "🟡 Consider Waiting", GOLD
#         signal_text = (f"The forward curve is pricing the next 12 months an average of {abs(signal_gap_pct):.1f}% "
#                         f"below today's front-month price — the market expects softer prices ahead.")
#     else:
#         signal_label, signal_color = "⚪ Neutral", SUBTEXT
#         signal_text = (f"The forward curve is roughly flat (within {abs(signal_gap_pct):.1f}%) across the next "
#                         f"12 months — no strong signal either way today.")
# else:
#     signal_label, signal_color = "⚪ Insufficient Data", SUBTEXT
#     signal_text = "Not enough forward months in the feed to compute a signal."

# # ---------------------------------------------------------------------------
# # Build the forward curve stack -- NYMEX + Basis only, plus the utility's
# # unbundled (commodity-only) comparison line where we have real data for it.
# # ---------------------------------------------------------------------------
# rows = []
# for _, r in curve.iterrows():
#     contract_date = pd.Timestamp(r["contract_date"])
#     nymex_per_dth = r["nymex_price"]
#     basis_per_dth = r["basis"]
#     total_per_dth = nymex_per_dth + basis_per_dth
#     utility_unbundled_per_dth = (total_per_dth + wacog_premium_per_dth) if utility_comparison_available else None

#     rows.append({
#         "contract_month": r["contract_month"],
#         "contract_date": contract_date,
#         "nymex_is_live": r["nymex_is_live"],
#         "NYMEX": nymex_per_dth,
#         "Basis": basis_per_dth,
#         "Total": total_per_dth,
#         "Utility Unbundled": utility_unbundled_per_dth,
#     })

# stack_df = pd.DataFrame(rows)

# # ---------------------------------------------------------------------------
# # THE INDICATIVE PRICE -- averaged over the selected term. $/Dth throughout.
# # ---------------------------------------------------------------------------
# term_window = stack_df.head(fixed_term_months)
# avg_nymex = term_window["NYMEX"].mean()
# avg_basis = term_window["Basis"].mean()
# total_price = avg_nymex + avg_basis

# if utility_comparison_available:
#     avg_utility_unbundled = term_window["Utility Unbundled"].mean()
#     savings_per_dth = avg_utility_unbundled - total_price
#     savings_pct = (savings_per_dth / avg_utility_unbundled * 100) if avg_utility_unbundled else 0
#     annual_savings = savings_per_dth * monthly_dth * 12
# else:
#     avg_utility_unbundled = savings_per_dth = savings_pct = annual_savings = None

# # ---------------------------------------------------------------------------
# # Live data strip
# # ---------------------------------------------------------------------------
# live_nymex_tag = "🟢 live (yfinance)" if stack_df.iloc[0]["nymex_is_live"] else "🟡 cached (feed fallback)"
# live_basis_tag = "🟢 live" if basis_result["is_live"] else "🟡 cached (local snapshot)"

# nymex_delta_html = ""
# if nymex_change is not None:
#     arrow, color = ("▲", RED) if nymex_change > 0 else ("▼", GREEN) if nymex_change < 0 else ("—", SUBTEXT)
#     nymex_delta_html = f'<span style="color:{color} !important;font-weight:700;">{arrow} ${abs(nymex_change):.3f} vs. {prior_close["date"]}</span>'
# basis_delta_html = ""
# if basis_change is not None:
#     arrow, color = ("▲", RED) if basis_change > 0 else ("▼", GREEN) if basis_change < 0 else ("—", SUBTEXT)
#     basis_delta_html = f'<span style="color:{color} !important;font-weight:700;">{arrow} ${abs(basis_change):.3f} vs. {prior_close["date"]}</span>'

# c1, c2 = st.columns(2)
# c1.markdown(f"""<div class="rfp-card"><div class="rfp-stat">${stack_df.iloc[0]['NYMEX']:.2f}</div>
# <div class="rfp-stat-label">Front-month NYMEX ($/Dth) — {live_nymex_tag}</div>{nymex_delta_html}</div>""", unsafe_allow_html=True)
# c2.markdown(f"""<div class="rfp-card"><div class="rfp-stat">${stack_df.iloc[0]['Basis']:+.2f}</div>
# <div class="rfp-stat-label">{TICKER_LABELS.get(ticker, ticker)} basis ($/Dth) — {live_basis_tag}, as of {basis_result['as_of']}</div>{basis_delta_html}</div>""", unsafe_allow_html=True)

# if not prior_close:
#     st.caption("📌 First visit recorded today — come back tomorrow to see day-over-day price movement here.")

# # ---------------------------------------------------------------------------
# # Daily Buyer Signal
# # ---------------------------------------------------------------------------
# st.markdown(
#     f"""
#     <div class="rfp-signal-card">
#         <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
#             <div>
#                 <div style="color:{SUBTEXT}; font-size:0.85rem; text-transform:uppercase; letter-spacing:0.05em;">Today's RFP Buyer Signal — {utility_key}</div>
#                 <div style="font-size:1.6rem; font-weight:800; color:{signal_color};">{signal_label}</div>
#             </div>
#             <div style="max-width:520px; color:{TEXT}; font-size:0.95rem;">{signal_text}</div>
#         </div>
#     </div>
#     """,
#     unsafe_allow_html=True,
# )

# st.markdown("<br>", unsafe_allow_html=True)

# # ---------------------------------------------------------------------------
# # THE PRICE BUILD -- NYMEX + Basis = Total. That's the whole calculation.
# # ---------------------------------------------------------------------------
# st.subheader(f"Your {fixed_term_months}-Month Price — {utility_key}")
# st.caption("All figures in \\$/Dth (1 Dth ≈ 1 MMBtu ≈ 10 therms).")

# b1, b2, b3 = st.columns(3)
# b1.markdown(f"""<div class="rfp-card"><div class="rfp-stat">${avg_nymex:.3f}<span style="font-size:1rem;">/Dth</span></div>
# <div class="rfp-stat-label">NYMEX (avg, {fixed_term_months}-mo)</div></div>""", unsafe_allow_html=True)
# b2.markdown(f"""<div class="rfp-card plus"><div class="rfp-stat">${avg_basis:+.3f}<span style="font-size:1rem;">/Dth</span></div>
# <div class="rfp-stat-label">+ Basis (avg, {fixed_term_months}-mo)</div></div>""", unsafe_allow_html=True)
# b3.markdown(f"""<div class="rfp-card total"><div class="rfp-stat" style="color:{GOLD};">${total_price:.3f}<span style="font-size:1rem;">/Dth</span></div>
# <div class="rfp-stat-label">= Total</div></div>""", unsafe_allow_html=True)

# if utility_comparison_available:
#     st.markdown(f"""<div class="rfp-card">
#     <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
#         <div>
#             <div class="rfp-stat" style="color:{GREEN if savings_per_dth>=0 else RED};">${savings_per_dth:+.3f}/Dth</div>
#             <div class="rfp-stat-label">vs. {utility_key}'s unbundled commodity rate (${avg_utility_unbundled:.3f}/Dth)</div>
#         </div>
#         <div>
#             <div class="rfp-stat" style="color:{GREEN if annual_savings>=0 else RED};">${annual_savings:,.0f}</div>
#             <div class="rfp-stat-label">Estimated annual $ impact at {monthly_dth:,} Dth/mo</div>
#         </div>
#     </div>
#     </div>""", unsafe_allow_html=True)
# else:
#     st.markdown(f"""<div class="rfp-pending">
#     <b>⏳ {utility_key} utility comparison coming soon.</b> {utility_key}'s commodity rate is published across
#     several rate-class-specific schedules with a separately-adjusting monthly cost-of-gas charge, rather than
#     one clean headline figure like SoCalGas and PG&E publish — we're sourcing a confirmed number before
#     showing a comparison here rather than estimating one. NYMEX + Basis above is real and live either way.
#     </div>""", unsafe_allow_html=True)

# st.markdown(
#     f"""
#     <div class="rfp-cta-banner">
#         <b>This is NYMEX + Basis only — the market-based part of your price.</b> Transportation, local
#         delivery, and everything else is different for every account, which is exactly why RFP doesn't
#         guess at it here. Connect with RFP for your real, live {fixed_term_months}-month quote.
#     </div>
#     """,
#     unsafe_allow_html=True,
# )

# with st.expander("📩 Get my real, live quote from RFP", expanded=False):
#     st.markdown(f"""
#     **Already carrying forward what you've entered:** {utility_key}, {monthly_dth:,} Dth/mo,
#     {fixed_term_months}-month term — RFP gets a qualified, usage-sized lead, not just an email address.

#     **What you'll get weekly, by email:**
#     - Your indicative price, recalculated for your citygate and usage
#     - Front-month basis for SoCal Citygate, PG&E Citygate, and Transco Zone 5
#     - A short note on what actually moved the basis that week
#     - RFP's daily market newsletter
#     """)
#     with st.form("lead_form"):
#         name = st.text_input("Name")
#         email = st.text_input("Work email")
#         company = st.text_input("Company")
#         newsletter_optin = st.checkbox("Also send me RFP's daily market newsletter", value=True)
#         submitted = st.form_submit_button("Request my real quote")
#         if submitted:
#             st.success(f"Thanks {name or 'there'} — in production this posts to the systeme.io funnel "
#                        f"with your {utility_key} / {monthly_dth:,} Dth/mo details attached, and "
#                        f"triggers the nurture sequence + a booked-meeting link.")

# st.markdown("---")

# # ---------------------------------------------------------------------------
# # Forward Strips -- mirrors RFP's own "One Minute Energy Update" format
# # ---------------------------------------------------------------------------
# st.subheader("Forward Strips")
# st.caption(f"{TICKER_LABELS.get(ticker, ticker)} all-in ($/Dth) — NYMEX + basis, same format as RFP's daily market update.")

# stack_df["cal_year"] = stack_df["contract_date"].dt.year
# stack_df["cal_month"] = stack_df["contract_date"].dt.month
# next12_df = stack_df.head(12)
# winter_next12 = next12_df[next12_df["cal_month"].isin([11, 12, 1, 2, 3])]
# summer_next12 = next12_df[next12_df["cal_month"].isin([4, 5, 6, 7, 8, 9, 10])]

# strip_cols = st.columns(5)
# strip_defs = [
#     ("6-Month Strip", stack_df.head(6)),
#     ("12-Month Strip", stack_df.head(12)),
#     ("24-Month Strip", stack_df.head(24)),
#     ("Next Winter (Nov–Mar)", winter_next12),
#     ("Next Summer (Apr–Oct)", summer_next12),
# ]
# for col, (label, sub_df) in zip(strip_cols, strip_defs):
#     val = sub_df["Total"].mean() if not sub_df.empty else None
#     col.markdown(f"""<div class="rfp-card"><div class="rfp-stat" style="font-size:1.4rem;">{f'${val:.2f}' if val else '—'}</div>
#     <div class="rfp-stat-label">{label}</div></div>""", unsafe_allow_html=True)

# cal_years = sorted(stack_df["cal_year"].unique())[:4]
# if cal_years:
#     st.write("**Calendar Year Averages**")
#     cy_cols = st.columns(len(cal_years))
#     for col, yr in zip(cy_cols, cal_years):
#         yr_avg = stack_df[stack_df["cal_year"] == yr]["Total"].mean()
#         col.markdown(f"""<div class="rfp-card"><div class="rfp-stat" style="font-size:1.3rem;">${yr_avg:.2f}</div>
#         <div class="rfp-stat-label">Calendar {yr}</div></div>""", unsafe_allow_html=True)

# st.markdown("<br>", unsafe_allow_html=True)

# # ---------------------------------------------------------------------------
# # Your Price vs. Utility -- the chart that sells it
# # ---------------------------------------------------------------------------
# if utility_comparison_available:
#     st.subheader(f"Your Price vs. {utility_key} — What The Utility Can't Offer")
#     fig_structures = go.Figure()
#     fig_structures.add_trace(go.Scatter(
#         x=stack_df["contract_month"], y=[total_price] * len(stack_df),
#         name=f"Your {fixed_term_months}-Month Price", mode="lines", line=dict(color=GOLD, width=3, dash="dash"),
#     ))
#     fig_structures.add_trace(go.Scatter(
#         x=stack_df["contract_month"], y=stack_df["Utility Unbundled"],
#         name=f"{utility_key} Unbundled Rate (variable)", mode="lines+markers", line=dict(color=RED, width=2, dash="dot"),
#     ))
#     fig_structures.update_layout(**dark_chart_layout(
#         height=420, yaxis=dict(title=dict(text="$ per Dth")),
#         legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40, b=20),
#     ))
#     st.plotly_chart(fig_structures, use_container_width=True)
#     st.caption(
#         f"The utility's rate is variable by regulatory design — it resets monthly with the market. Your "
#         f"{fixed_term_months}-month price is locked using today's forward curve. Both lines are commodity-only "
#         f"(unbundled) — transportation and delivery are excluded from both sides for a fair comparison, since "
#         f"those apply the same way regardless of who supplies your gas."
#     )

#     st.subheader("NYMEX + Basis — Layer by Layer")
#     layers = ["NYMEX", "Basis"]
#     colors = ["#4FC3F7", GOLD]
#     fig = go.Figure()
#     for layer, color in zip(layers, colors):
#         fig.add_trace(go.Scatter(
#             x=stack_df["contract_month"], y=stack_df[layer], name=layer, mode="lines",
#             stackgroup="one", line=dict(width=0.5, color=color), fillcolor=color,
#         ))
#     fig.add_trace(go.Scatter(
#         x=stack_df["contract_month"], y=stack_df["Utility Unbundled"],
#         name=f"{utility_key} Unbundled Rate", mode="lines+markers", line=dict(color=RED, width=3, dash="dot"),
#     ))
#     fig.update_layout(**dark_chart_layout(
#         height=400, yaxis=dict(title=dict(text="$ per Dth")),
#         legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40, b=20),
#     ))
#     st.plotly_chart(fig, use_container_width=True)

# # ---------------------------------------------------------------------------
# # Next 3 Months
# # ---------------------------------------------------------------------------
# st.subheader("Next 3 Months — NYMEX & Basis")
# quote_time_str = dt.datetime.fromisoformat(nymex_fetched_at).strftime("%b %d, %Y %H:%M UTC") if nymex_fetched_at else "n/a"
# st.caption(f"NYMEX quotes as of {quote_time_str} · basis as of report date {basis_result['as_of']}")
# next3_cols = st.columns(3)
# for i in range(min(3, len(stack_df))):
#     row = stack_df.iloc[i]
#     live_tag = "🟢" if row["nymex_is_live"] else "🟡"
#     with next3_cols[i]:
#         st.markdown(f"""<div class="rfp-card">
#             <div style="color:{SUBTEXT}; font-size:0.85rem;">{row['contract_month']} {live_tag}</div>
#             <div class="rfp-stat" style="font-size:1.3rem;">${row['Total']:.2f}<span style="font-size:0.85rem; color:{SUBTEXT} !important;">/Dth</span></div>
#             <div class="rfp-stat-label">NYMEX ${row['NYMEX']:.2f} + Basis ${row['Basis']:+.2f}</div>
#         </div>""", unsafe_allow_html=True)

# st.markdown("---")

# # ---------------------------------------------------------------------------
# # Data + tariff transparency
# # ---------------------------------------------------------------------------
# st.header("The Real Data Behind This Calculator")

# tab1, tab2, tab3 = st.tabs(["Forward Curve Detail", f"{utility_key} Tariff Reference", "Data Sources"])

# with tab1:
#     display_cols = ["contract_month", "NYMEX", "Basis", "Total"] + (["Utility Unbundled"] if utility_comparison_available else [])
#     display_df = stack_df[display_cols].copy()
#     for col in display_df.columns[1:]:
#         display_df[col] = display_df[col].map(lambda v: f"${v:.3f}")
#     st.dataframe(display_df, hide_index=True, use_container_width=True)

# with tab2:
#     st.caption(
#         "Shown for context and credibility — RFP knows these real, published rates, but they aren't used "
#         "in the Total above, since Transportation and Local Delivery are different for every account. "
#         "Figures below are the utility's own **native $/therm units**, exactly as published in their tariff "
#         "sheets, deliberately not converted to Dth."
#     )
#     if utility_key == "SoCalGas":
#         df_t = pd.DataFrame([{"Tier": t.label, "Bundled Core Rate (¢/therm)": t.rate_cents_per_therm}
#                               for t in SOCALGAS_CORE_G10.core_tiers])
#         df_tr = pd.DataFrame([{"Tier": t.label, "Transport-Only Rate (¢/therm)": t.rate_cents_per_therm}
#                                for t in SOCALGAS_CORE_G10.transport_only_tiers])
#         cc1, cc2 = st.columns(2)
#         cc1.write("**Bundled (Schedule G-10, GN-10)**")
#         cc1.dataframe(df_t, hide_index=True, use_container_width=True)
#         cc2.write("**Transport-only (Schedule G-10, GT-10 / CAT)**")
#         cc2.dataframe(df_tr, hide_index=True, use_container_width=True)
#         st.caption(f"Source: {SOCALGAS_CORE_G10.source_note}, effective {SOCALGAS_CORE_G10.effective_date}. "
#                    f"[{SOCALGAS_CORE_G10.source_url}]({SOCALGAS_CORE_G10.source_url})")
#     elif utility_key == "PG&E":
#         df_p = pd.DataFrame([
#             {"Band": "First 4,000 therms/mo", "Summer ($/therm)": PGE_GNR1.transport_summer_first4000, "Winter ($/therm)": PGE_GNR1.transport_winter_first4000},
#             {"Band": "Excess over 4,000 therms/mo", "Summer ($/therm)": PGE_GNR1.transport_summer_excess, "Winter ($/therm)": PGE_GNR1.transport_winter_excess},
#         ])
#         st.write(f"**Transportation — Procurement: ${PGE_GNR1.procurement_dollars_per_therm:.5f}/therm**")
#         st.dataframe(df_p, hide_index=True, use_container_width=True)
#         st.caption(f"Source: {PGE_GNR1.source_note}, effective {PGE_GNR1.effective_date}. "
#                    f"[{PGE_GNR1.source_url}]({PGE_GNR1.source_url})")
#     else:
#         st.info(
#             "Piedmont's tariff reference is still being sourced. Their rate structure splits Transportation, "
#             "Local Delivery, and Cost of Gas across multiple rate-class-specific schedules (Fg-3, Ag-1, Ig-4, "
#             "Pg-10, and others) with a separately-adjusting monthly PGA, rather than one clean published "
#             "figure per component the way SoCalGas and PG&E disclose theirs — this tab will populate once "
#             "that's confirmed."
#         )

# with tab3:
#     st.markdown(f"""
#     - **NYMEX forward strip**: live via yfinance, per-contract-month tickers (e.g. `NGV26.NYM`), falling back to
#       the settle price embedded in the Rogue Basis feed if a live quote isn't available for that contract.
#       Treated as \\$/Dth (1 Dth ≈ 1 MMBtu, the native unit of the feed).
#     - **Forward basis**: [Rogue Basis Scraper feed]({LIVE_BASIS_URL}), ticker `{ticker}`
#       ({TICKER_LABELS.get(ticker, ticker)}), as of report date {basis_result['as_of']}. Also \\$/Dth-native.
#     - **Total** = NYMEX + Basis, averaged over your selected {fixed_term_months}-month term. That's the whole
#       calculation — no Transportation, Local Delivery, or fees are included, since those genuinely differ by
#       account and RFP would rather show nothing than guess.
#     {"- **Utility Unbundled Rate**: " + utility_key + "'s own published commodity-only Cost of Gas, projected forward using the gap between their current rate and today's market price (a real, measured premium — not assumed). Both sides of the comparison exclude Transportation and Local Delivery, for a fair, apples-to-apples comparison of just the shoppable part of the bill." if utility_comparison_available else "- **" + utility_key + " utility comparison**: not yet available — see the Tariff Reference tab for why."}
#     """)

# st.markdown("---")
# st.caption(
#     "Prototype built for pitch purposes. NYMEX and basis data are live where available, with clearly labeled "
#     "cached fallbacks otherwise. This is indicative only — not a binding quote. Connect with RFP Energy "
#     "Solutions for your real, live number."
# )
