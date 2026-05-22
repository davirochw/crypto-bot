"""Risk management primitives: stop placement, take-profit, R:R, position sizing.

The copilot never sends orders. These helpers exist so signals carry concrete,
actionable numbers rather than vague "buy here" statements.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.settings import settings
from core.types import Side


@dataclass(slots=True)
class RiskPlan:
    side: Side
    entry: float
    stop: float
    take_profit: float
    risk_reward: float
    position_size: float       # in quote currency (USDT)
    risk_amount: float         # USDT at risk if stop hits


def atr_stop(entry: float, atr: float, side: Side, atr_mult: float | None = None) -> float:
    """Stop = entry ± (ATR * multiplier). Volatility-aware, adapts per pair."""
    mult = atr_mult if atr_mult is not None else settings.atr_stop_mult
    if side == Side.LONG:
        return entry - atr * mult
    return entry + atr * mult


def take_profit_from_rr(entry: float, stop: float, side: Side, rr: float | None = None) -> float:
    """Project TP at R:R times the risk distance."""
    rr = rr if rr is not None else settings.default_rr_ratio
    risk = abs(entry - stop)
    return entry + risk * rr if side == Side.LONG else entry - risk * rr


def position_size(
    account_balance: float,
    entry: float,
    stop: float,
    risk_percent: float | None = None,
) -> tuple[float, float]:
    """Return (position_size_in_quote, risk_amount).

    Sizes the position so a stop hit loses exactly `risk_percent` of balance.
    """
    risk_pct = risk_percent if risk_percent is not None else settings.default_risk_percent
    risk_amount = account_balance * (risk_pct / 100)
    distance_pct = abs(entry - stop) / entry
    if distance_pct <= 0:
        return 0.0, 0.0
    notional = risk_amount / distance_pct
    return round(notional, 2), round(risk_amount, 2)


def build_plan(
    side: Side,
    entry: float,
    atr: float,
    account_balance: float,
    *,
    atr_mult: float | None = None,
    rr: float | None = None,
    risk_percent: float | None = None,
) -> RiskPlan:
    stop = atr_stop(entry, atr, side, atr_mult=atr_mult)
    tp = take_profit_from_rr(entry, stop, side, rr=rr)
    notional, risk_amt = position_size(account_balance, entry, stop, risk_percent=risk_percent)
    rr_actual = abs(tp - entry) / abs(entry - stop) if entry != stop else 0.0
    return RiskPlan(
        side=side,
        entry=round(entry, 8),
        stop=round(stop, 8),
        take_profit=round(tp, 8),
        risk_reward=round(rr_actual, 2),
        position_size=notional,
        risk_amount=risk_amt,
    )


def is_setup_acceptable(rr: float, atr_pct: float) -> tuple[bool, str | None]:
    """Cheap pre-filter to skip obviously bad setups before scoring/AI."""
    if rr < 1.2:
        return False, f"R:R too low ({rr:.2f})"
    if atr_pct < 0.15:
        return False, f"Market too dead (ATR {atr_pct:.2f}% of price)"
    if atr_pct > 8.0:
        return False, f"Market too volatile (ATR {atr_pct:.2f}% of price)"
    return True, None
