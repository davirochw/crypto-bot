"""Probability score 0-100 for a trade setup.

Each component contributes a weighted slice. The score is a heuristic — it
encodes expert intuition about *what makes a setup likely to work*, not a
predicted win rate.
"""

from __future__ import annotations

from core.types import IndicatorSnapshot, MarketContext, Side, Trend


WEIGHTS = {
    "trend_alignment": 25,    # macro/meso/micro timeframes agreeing
    "indicator_confluence": 20,  # multiple indicators pointing same way
    "volume_confirmation": 15,
    "volatility_quality": 10,    # not too dead, not too wild
    "structure": 15,             # near S/R, breakout quality
    "rr_quality": 15,            # risk/reward attractiveness
}


def _trend_score(ctx: MarketContext, side: Side) -> float:
    target = Trend.BULLISH if side == Side.LONG else Trend.BEARISH
    aligned = 0
    total = 0
    for snap in ctx.snapshots.values():
        if snap.trend == Trend.NEUTRAL:
            continue
        total += 1
        if snap.trend == target:
            aligned += 1
    if total == 0:
        return 0.4
    return aligned / total


def _indicator_score(snap: IndicatorSnapshot, side: Side) -> float:
    hits = 0
    checks = 0

    if snap.rsi is not None:
        checks += 1
        if side == Side.LONG and 40 <= snap.rsi <= 70:
            hits += 1
        elif side == Side.SHORT and 30 <= snap.rsi <= 60:
            hits += 1

    if snap.macd_hist is not None:
        checks += 1
        if (side == Side.LONG and snap.macd_hist > 0) or (side == Side.SHORT and snap.macd_hist < 0):
            hits += 1

    if snap.ema9 and snap.ema21:
        checks += 1
        if (side == Side.LONG and snap.ema9 > snap.ema21) or (
            side == Side.SHORT and snap.ema9 < snap.ema21
        ):
            hits += 1

    if snap.ema200 and snap.close:
        checks += 1
        if (side == Side.LONG and snap.close > snap.ema200) or (
            side == Side.SHORT and snap.close < snap.ema200
        ):
            hits += 1

    if snap.vwap and snap.close:
        checks += 1
        if (side == Side.LONG and snap.close > snap.vwap) or (
            side == Side.SHORT and snap.close < snap.vwap
        ):
            hits += 1

    return hits / checks if checks else 0.0


def _volume_score(snap: IndicatorSnapshot) -> float:
    if not snap.volume_ma or snap.volume_ma <= 0:
        return 0.5
    ratio = snap.volume / snap.volume_ma
    if ratio >= 1.5:
        return 1.0
    if ratio >= 1.2:
        return 0.8
    if ratio >= 1.0:
        return 0.6
    if ratio >= 0.7:
        return 0.4
    return 0.2


def _volatility_score(snap: IndicatorSnapshot) -> float:
    if not snap.atr or snap.close <= 0:
        return 0.5
    atr_pct = snap.atr / snap.close * 100
    # Sweet spot: 0.5%-3% ATR/price. Too small = dead, too large = chaos.
    if 0.5 <= atr_pct <= 3.0:
        return 1.0
    if 0.3 <= atr_pct <= 5.0:
        return 0.6
    return 0.2


def _structure_score(snap: IndicatorSnapshot, side: Side) -> float:
    score = 0.5
    if snap.breakout:
        score = 0.9
    if snap.support and side == Side.LONG and snap.close > 0:
        dist = abs(snap.close - snap.support) / snap.close
        if dist < 0.01:
            score = max(score, 0.8)
    if snap.resistance and side == Side.SHORT and snap.close > 0:
        dist = abs(snap.close - snap.resistance) / snap.close
        if dist < 0.01:
            score = max(score, 0.8)
    return score


def _rr_score(rr: float) -> float:
    if rr >= 3:
        return 1.0
    if rr >= 2:
        return 0.85
    if rr >= 1.5:
        return 0.65
    if rr >= 1:
        return 0.45
    return 0.2


def compute_score(
    ctx: MarketContext,
    setup_timeframe: str,
    side: Side,
    risk_reward: float,
) -> tuple[int, list[str]]:
    """Return (score 0-100, list of human-readable reasons)."""
    snap = ctx.snapshots.get(setup_timeframe)
    if snap is None:
        return 0, ["no snapshot for setup timeframe"]

    parts = {
        "trend_alignment": _trend_score(ctx, side),
        "indicator_confluence": _indicator_score(snap, side),
        "volume_confirmation": _volume_score(snap),
        "volatility_quality": _volatility_score(snap),
        "structure": _structure_score(snap, side),
        "rr_quality": _rr_score(risk_reward),
    }

    score = sum(parts[k] * WEIGHTS[k] for k in WEIGHTS)
    score = max(0, min(100, round(score)))

    reasons: list[str] = []
    if parts["trend_alignment"] >= 0.7:
        reasons.append(f"Multi-timeframe trend aligned with {side.value}")
    if parts["indicator_confluence"] >= 0.6:
        reasons.append("Strong indicator confluence")
    if parts["volume_confirmation"] >= 0.8:
        reasons.append("Above-average volume confirmation")
    if parts["volatility_quality"] < 0.4:
        reasons.append("Volatility outside healthy range")
    if parts["structure"] >= 0.8:
        reasons.append("Setup at structural level (S/R or breakout)")
    if parts["rr_quality"] >= 0.85:
        reasons.append(f"Attractive R:R = {risk_reward:.2f}")

    return score, reasons
