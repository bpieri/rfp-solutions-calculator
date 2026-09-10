"""
Real utility tariff data for RFP Energy Solutions' Delivered Cost Calculator.

Every rate below is sourced from the utility's own published tariff sheets.
This is the "we HAVE to have this" detail: real tier breakpoints, real
cents-per-therm figures, real effective dates -- not illustrative numbers.

IMPORTANT: Utility procurement charges reset monthly and transportation
tariffs change periodically via CPUC advice letters. Whoever maintains this
file in production should refresh SOURCE / EFFECTIVE fields on that cadence.
"""

from dataclasses import dataclass
from typing import List


@dataclass
class Tier:
    label: str
    upper_bound_therms_per_month: float  # use float("inf") for "and above"
    rate_cents_per_therm: float


@dataclass
class UtilityTariff:
    utility: str
    citygate: str
    schedule_core: str          # bundled core procurement + transport schedule name
    schedule_transport_only: str  # CTA / transport-only schedule name
    core_procurement_cents_per_therm: float
    core_tiers: List[Tier]          # bundled total (procurement + transport), core customer
    transport_only_tiers: List[Tier]  # transport-only charge (CTA/marketer customers pay this + their own commodity)
    customer_charge_note: str
    effective_date: str
    source_url: str
    source_note: str


# ---------------------------------------------------------------------------
# SoCalGas — Schedule G-10 (Core Commercial & Industrial: GN-10 bundled / GT-10
# transport-only) and Schedule GT-NC (GT-3NC, noncore C&I transport-only).
# Source: SoCalGas Tariff Book Update, Advice Letter 6679-G, effective Aug 10,
# 2026 (procurement per Advice Letter 6672-G, effective Aug 1, 2026).
# https://www.socalgas.com/regulatory/documents/TariffBookUpdate.pdf
# ---------------------------------------------------------------------------
SOCALGAS_CORE_G10 = UtilityTariff(
    utility="SoCalGas",
    citygate="SoCal Citygate",
    schedule_core="Schedule G-10 (GN-10, bundled core)",
    schedule_transport_only="Schedule G-10 (GT-10, CAT / transport-only)",
    core_procurement_cents_per_therm=41.856,
    core_tiers=[
        Tier("Tier I (first 250 therms/mo)", 250, 194.373),
        Tier("Tier II (250 – 4,167 therms/mo)", 4167, 124.470),
        Tier("Tier III (above 4,167 therms/mo)", float("inf"), 77.600),
    ],
    transport_only_tiers=[
        Tier("Tier I (first 250 therms/mo)", 250, 152.517),
        Tier("Tier II (250 – 4,167 therms/mo)", 4167, 82.614),
        Tier("Tier III (above 4,167 therms/mo)", float("inf"), 35.744),
    ],
    customer_charge_note="49.315¢/meter/day (all customers except space-heating-only)",
    effective_date="August 10, 2026 (transport) / August 1, 2026 (procurement)",
    source_url="https://www.socalgas.com/regulatory/documents/TariffBookUpdate.pdf",
    source_note="SoCalGas Tariff Book Update, Advice Letters 6679-G & 6672-G",
)

# "Cost of Gas" component of the core procurement charge, EXCLUDING the
# 0.305c/therm brokerage fee -- this is the WACOG (weighted-average-cost-of-
# gas) figure that SoCalGas's own tariff says already includes "the carrying
# cost of storage inventory," CPGA over/under-collection true-ups, and Gas
# Cost Incentive Mechanism (GCIM) adjustments -- i.e. it runs structurally
# above a pure forward-market commodity price. Source: same tariff, Schedule
# G-CP, "Cost of Gas, per therm ... 41.551 cents."
SOCALGAS_COST_OF_GAS_CENTS_PER_THERM = 41.551

# Noncore C&I transport-only — Schedule GT-NC (GT-3NC), for larger noncore
# customers (above the ~250,000 therm/yr core threshold).
SOCALGAS_NONCORE_GT3NC = UtilityTariff(
    utility="SoCalGas",
    citygate="SoCal Citygate",
    schedule_core="N/A — noncore customers are always transport-only",
    schedule_transport_only="Schedule GT-NC (GT-3NC, Commercial/Industrial)",
    core_procurement_cents_per_therm=0.0,  # noncore customers buy commodity from a marketer, not SoCalGas
    core_tiers=[],
    transport_only_tiers=[
        Tier("Tier I (0 – 20,833 therms/mo)", 20833, 58.154),
        Tier("Tier II (20,834 – 83,333 therms/mo)", 83333, 40.985),
        Tier("Tier III (83,334 – 166,667 therms/mo)", 166667, 30.002),
        Tier("Tier IV (above 166,667 therms/mo)", float("inf"), 22.153),
    ],
    customer_charge_note="$350/month customer charge (GT-3NC)",
    effective_date="August 1, 2026",
    source_url="https://www.socalgas.com/regulatory/documents/TariffBookUpdate.pdf",
    source_note="SoCalGas Tariff Book Update, Advice Letter 6669-G",
)


# ---------------------------------------------------------------------------
# PG&E — Schedule G-NR1 (Small Commercial, < 20,800 therms/mo core).
# Source: PG&E Gas Schedule G-NR1, Advice 5248-G, effective September 1, 2026.
# https://www.pge.com/tariffs/assets/pdf/tariffbook/GAS_SCHEDS_G-NR1.pdf
# Rates differ by season (summer Apr 1–Oct 31 / winter Nov 1–Mar 31) and by
# usage band (first 4,000 therms vs. excess) rather than the SoCalGas-style
# tiered-by-total-volume structure.
# ---------------------------------------------------------------------------
@dataclass
class PGETariff:
    utility: str
    citygate: str
    schedule_core: str
    schedule_transport_only: str
    procurement_dollars_per_therm: float
    transport_summer_first4000: float
    transport_summer_excess: float
    transport_winter_first4000: float
    transport_winter_excess: float
    effective_date: str
    source_url: str
    source_note: str


PGE_GNR1 = PGETariff(
    utility="PG&E",
    citygate="PG&E Citygate",
    schedule_core="Schedule G-NR1 (bundled core, Small Commercial < 20,800 therms/mo)",
    schedule_transport_only="Schedule G-NR1 + G-CT (Core Gas Aggregation, transport-only)",
    procurement_dollars_per_therm=0.32085,
    transport_summer_first4000=1.28920,
    transport_summer_excess=0.69518,
    transport_winter_first4000=1.51553,
    transport_winter_excess=0.81722,
    effective_date="September 1, 2026",
    source_url="https://www.pge.com/tariffs/assets/pdf/tariffbook/GAS_SCHEDS_G-NR1.pdf",
    source_note="PG&E Gas Schedule G-NR1, Advice 5248-G",
)

# PG&E's public tariff sheet does not break "Cost of Gas" out from the
# brokerage fee the way SoCalGas's does, so the full procurement charge is
# used as the WACOG proxy (the brokerage fee is small enough, ~0.25c/therm,
# that this introduces negligible error).
PGE_COST_OF_GAS_DOLLARS_PER_THERM = PGE_GNR1.procurement_dollars_per_therm


def wacog_premium_dollars_per_therm(utility: str, current_market_commodity_dollars_per_therm: float) -> float:
    """
    The gap between the utility's current published Cost of Gas (WACOG --
    smoothed, storage-carrying-cost-loaded, true-up-adjusted per the tariff's
    own methodology language) and today's actual forward-market commodity
    price (NYMEX + basis). Used to project a realistic forward utility rate
    that moves with the market instead of being held flat.
    """
    if utility == "SoCalGas":
        utility_cost_of_gas = SOCALGAS_COST_OF_GAS_CENTS_PER_THERM / 100.0
    else:
        utility_cost_of_gas = PGE_COST_OF_GAS_DOLLARS_PER_THERM
    return utility_cost_of_gas - current_market_commodity_dollars_per_therm


def bracketed_cost(therms: float, tiers: List[Tier]) -> float:
    """
    Compute total cost (in cents) for a given monthly therm volume using
    a graduated tier schedule where each tier's rate applies only to the
    volume that falls within that tier's band (SoCalGas-style tiering).
    """
    remaining = therms
    lower = 0.0
    total_cents = 0.0
    for tier in tiers:
        band = min(remaining, tier.upper_bound_therms_per_month - lower)
        if band <= 0:
            break
        total_cents += band * tier.rate_cents_per_therm
        remaining -= band
        lower = tier.upper_bound_therms_per_month
        if remaining <= 0:
            break
    return total_cents


def pge_bundled_cost(therms: float, tariff: PGETariff, season: str) -> float:
    """PG&E G-NR1 bundled cost (procurement + transport) in dollars, by season."""
    first_band = min(therms, 4000)
    excess = max(therms - 4000, 0)
    if season == "summer":
        transport = first_band * tariff.transport_summer_first4000 + excess * tariff.transport_summer_excess
    else:
        transport = first_band * tariff.transport_winter_first4000 + excess * tariff.transport_winter_excess
    procurement = therms * tariff.procurement_dollars_per_therm
    return transport + procurement


def pge_transport_only_cost(therms: float, tariff: PGETariff, season: str) -> float:
    """PG&E G-NR1 transportation-only charge (no PG&E procurement), in dollars."""
    first_band = min(therms, 4000)
    excess = max(therms - 4000, 0)
    if season == "summer":
        return first_band * tariff.transport_summer_first4000 + excess * tariff.transport_summer_excess
    return first_band * tariff.transport_winter_first4000 + excess * tariff.transport_winter_excess


def season_for_month(month_num: int) -> str:
    """SoCalGas/PG&E convention: winter = Dec–Mar (SoCalGas) / Nov–Mar (PG&E).
    We use PG&E's Nov 1–Mar 31 definition consistently since it's the one
    that actually varies the transportation rate in this model."""
    return "winter" if month_num in (11, 12, 1, 2, 3) else "summer"


# ---------------------------------------------------------------------------
# "Local Delivery" layer -- the utility's fixed customer/meter charge for
# physically connecting to and delivering through the local distribution
# system, as distinct from the volumetric "Transportation" charge above.
# Both figures are pulled directly from the published tariff sheets.
# ---------------------------------------------------------------------------

# SoCalGas G-10: flat 49.315¢/meter/day for all non-space-heating customers.
SOCALGAS_CUSTOMER_CHARGE_CENTS_PER_DAY = 49.315

# PG&E G-NR1: customer charge is tiered by Average Daily Usage (ADU), i.e.
# monthly therms / days in period. Source: PG&E Schedule G-NR1, Advice
# 5248-G, effective Sept 1, 2026.
PGE_CUSTOMER_CHARGE_TIERS = [
    # (adu_upper_bound_therms_per_day, dollars_per_day)
    (5.0, 0.27048),
    (16.0, 0.52106),
    (41.0, 0.95482),
    (123.0, 1.66489),
    (float("inf"), 2.14936),
]


def local_delivery_dollars_per_therm(utility: str, monthly_therms: float, days_in_month: int = 30) -> float:
    """
    Converts the utility's fixed daily customer/meter charge into an
    equivalent $/therm figure at the given monthly usage, so it can be
    stacked alongside the volumetric layers in the cost-per-therm chart.
    """
    if monthly_therms <= 0:
        return 0.0

    if utility == "SoCalGas":
        monthly_dollars = (SOCALGAS_CUSTOMER_CHARGE_CENTS_PER_DAY / 100.0) * days_in_month
    else:  # PG&E
        adu = monthly_therms / days_in_month
        rate = PGE_CUSTOMER_CHARGE_TIERS[-1][1]
        for upper, daily_rate in PGE_CUSTOMER_CHARGE_TIERS:
            if adu <= upper:
                rate = daily_rate
                break
        monthly_dollars = rate * days_in_month

    return monthly_dollars / monthly_therms


# ---------------------------------------------------------------------------
# Utility "brokerage fee" -- shown as CONTEXT ONLY. SoCalGas states directly
# in its own tariff that it buys gas "with no markup," so this fee is a tiny
# break-even administrative pass-through for a regulated no-margin utility --
# NOT a comparable figure to a competitive marketer's real margin. Sizing
# RFP's fee off this number (as an earlier version of this model did)
# understated a realistic marketer margin by roughly 20-50x. Kept here for
# display/comparison purposes only.
# ---------------------------------------------------------------------------
SOCALGAS_BROKERAGE_FEE_CENTS_PER_THERM = 0.305
PGE_BROKERAGE_FEE_CENTS_PER_THERM = 0.25  # CPUC D.10-12-032, effective 2011 -- re-verify before client use


def utility_brokerage_fee_cents_per_therm(utility: str) -> float:
    return SOCALGAS_BROKERAGE_FEE_CENTS_PER_THERM if utility == "SoCalGas" else PGE_BROKERAGE_FEE_CENTS_PER_THERM
