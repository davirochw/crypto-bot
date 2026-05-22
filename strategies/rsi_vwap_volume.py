"""RSI + VWAP + Volume: scalp pullback entries.

Long: macro/meso bullish, price reclaims VWAP from below with RSI>50 and volume>avg.
Short: mirror.
"""

from __future__ import annotations

from core.types import MarketContext, Side, Trend
from strategies.base import Strategy, StrategyResult


class RSIVWAPVolumeStrategy(Strategy):
    name = "rsi_vwap_volume"
    setup_timeframe = "5m"
    requires_macro_alignment = True

    def evaluate(self, ctx: MarketContext) -> StrategyResult | None:
        snap = ctx.snapshots.get(self.setup_timeframe)
        if not snap or snap.vwap is None or snap.rsi is None:
            return None

        meso = ctx.snapshots.get("1h")
        if not meso or meso.trend == Trend.NEUTRAL:
            return None

        volume_ok = (snap.volume_ma or 0) > 0 and snap.volume > snap.volume_ma * 1.1

        # Long: above VWAP, RSI between 50-70, momentum
        if (
            meso.trend == Trend.BULLISH
            and snap.close > snap.vwap
            and 50 <= snap.rsi <= 70
            and volume_ok
            and self._macro_aligned(ctx, Side.LONG)
        ):
            return StrategyResult(
                side=Side.LONG,
                setup_timeframe=self.setup_timeframe,
                entry=snap.close,
                reasons=[
                    "Price above VWAP (institutional bias up)",
                    f"RSI {snap.rsi:.1f} in healthy bullish zone",
                    "Volume above moving average",
                ],
                extras={"rsi": snap.rsi, "vwap": snap.vwap},
            )

        # Short: below VWAP, RSI between 30-50
        if (
            meso.trend == Trend.BEARISH
            and snap.close < snap.vwap
            and 30 <= snap.rsi <= 50
            and volume_ok
            and self._macro_aligned(ctx, Side.SHORT)
        ):
            return StrategyResult(
                side=Side.SHORT,
                setup_timeframe=self.setup_timeframe,
                entry=snap.close,
                reasons=[
                    "Price below VWAP (institutional bias down)",
                    f"RSI {snap.rsi:.1f} in healthy bearish zone",
                    "Volume above moving average",
                ],
                extras={"rsi": snap.rsi, "vwap": snap.vwap},
            )

        return None
