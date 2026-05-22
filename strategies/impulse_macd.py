"""Impulse MACD: enter on MACD histogram flip in the direction of trend.

Long: MACD hist crosses 0 upward AND price > EMA21 AND macro bullish.
Short: mirror.
"""

from __future__ import annotations

from core.types import MarketContext, Side
from strategies.base import Strategy, StrategyResult


class ImpulseMACDStrategy(Strategy):
    name = "impulse_macd"
    setup_timeframe = "15m"
    requires_macro_alignment = True

    def evaluate(self, ctx: MarketContext) -> StrategyResult | None:
        snap = ctx.snapshots.get(self.setup_timeframe)
        if not snap or snap.macd_hist is None or snap.ema21 is None:
            return None

        prev = ctx.snapshots.get("1h")
        if not prev or prev.macd is None:
            return None

        # bullish impulse
        if (
            snap.macd_hist > 0
            and snap.close > snap.ema21
            and (snap.ema9 or 0) > (snap.ema21 or 0)
            and self._macro_aligned(ctx, Side.LONG)
        ):
            return StrategyResult(
                side=Side.LONG,
                setup_timeframe=self.setup_timeframe,
                entry=snap.close,
                reasons=[
                    "MACD histogram positive",
                    "Price above EMA21 with EMA9>EMA21",
                    "Macro trend aligned bullish",
                ],
                extras={"macd_hist": snap.macd_hist, "rsi": snap.rsi},
            )

        # bearish impulse
        if (
            snap.macd_hist < 0
            and snap.close < snap.ema21
            and (snap.ema9 or 0) < (snap.ema21 or 0)
            and self._macro_aligned(ctx, Side.SHORT)
        ):
            return StrategyResult(
                side=Side.SHORT,
                setup_timeframe=self.setup_timeframe,
                entry=snap.close,
                reasons=[
                    "MACD histogram negative",
                    "Price below EMA21 with EMA9<EMA21",
                    "Macro trend aligned bearish",
                ],
                extras={"macd_hist": snap.macd_hist, "rsi": snap.rsi},
            )

        return None
