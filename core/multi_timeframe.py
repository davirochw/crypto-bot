"""Multi-timeframe analyzer: aggregates per-TF snapshots into a market context.

The macro_trend is taken from the highest timeframe; regime is derived from
volatility and EMA spread on the meso timeframe.
"""

from __future__ import annotations

from core.types import IndicatorSnapshot, MarketContext, MarketRegime, Trend


TF_ORDER = ["4h", "1h", "15m", "5m"]  # macro -> micro


def derive_regime(snap: IndicatorSnapshot) -> MarketRegime:
    if not snap.atr or snap.close <= 0:
        return MarketRegime.RANGING
    atr_pct = snap.atr / snap.close * 100

    if atr_pct > 4.0:
        return MarketRegime.VOLATILE

    if snap.ema9 and snap.ema21 and snap.ema200:
        spread = abs(snap.ema9 - snap.ema21) / snap.close * 100
        above_200 = snap.close > snap.ema200
        below_200 = snap.close < snap.ema200
        if spread > 0.4 and (above_200 or below_200):
            return MarketRegime.TRENDING

    return MarketRegime.RANGING


def derive_macro_trend(snapshots: dict[str, IndicatorSnapshot]) -> Trend:
    for tf in TF_ORDER:
        snap = snapshots.get(tf)
        if snap and snap.trend != Trend.NEUTRAL:
            return snap.trend
    return Trend.NEUTRAL


def build_context(
    symbol: str,
    snapshots: dict[str, IndicatorSnapshot],
    *,
    funding_rate: float | None = None,
    open_interest: float | None = None,
    long_short_ratio: float | None = None,
) -> MarketContext:
    meso_tf = "1h" if "1h" in snapshots else next(iter(snapshots))
    meso = snapshots[meso_tf]
    return MarketContext(
        symbol=symbol,
        snapshots=snapshots,
        funding_rate=funding_rate,
        open_interest=open_interest,
        long_short_ratio=long_short_ratio,
        regime=derive_regime(meso),
        macro_trend=derive_macro_trend(snapshots),
    )
