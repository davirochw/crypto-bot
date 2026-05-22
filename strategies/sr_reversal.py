"""Reversal at structural support/resistance with RSI extreme + Bollinger touch."""

from __future__ import annotations

from core.types import MarketContext, Side
from strategies.base import Strategy, StrategyResult


class SRReversalStrategy(Strategy):
    name = "sr_reversal"
    setup_timeframe = "1h"
    requires_macro_alignment = False  # counter-trend by design

    def evaluate(self, ctx: MarketContext) -> StrategyResult | None:
        snap = ctx.snapshots.get(self.setup_timeframe)
        if not snap or snap.rsi is None or snap.atr is None:
            return None

        proximity = max(snap.atr * 0.5, snap.close * 0.002)

        # Long reversal at support
        if (
            snap.support
            and abs(snap.close - snap.support) <= proximity
            and snap.rsi <= 32
            and snap.bb_lower
            and snap.close <= snap.bb_lower * 1.005
        ):
            return StrategyResult(
                side=Side.LONG,
                setup_timeframe=self.setup_timeframe,
                entry=snap.close,
                reasons=[
                    f"Touching support {snap.support:.6g}",
                    f"RSI oversold ({snap.rsi:.1f})",
                    "Price at/below lower Bollinger band",
                ],
                extras={"support": snap.support, "rsi": snap.rsi},
            )

        # Short reversal at resistance
        if (
            snap.resistance
            and abs(snap.close - snap.resistance) <= proximity
            and snap.rsi >= 68
            and snap.bb_upper
            and snap.close >= snap.bb_upper * 0.995
        ):
            return StrategyResult(
                side=Side.SHORT,
                setup_timeframe=self.setup_timeframe,
                entry=snap.close,
                reasons=[
                    f"Touching resistance {snap.resistance:.6g}",
                    f"RSI overbought ({snap.rsi:.1f})",
                    "Price at/above upper Bollinger band",
                ],
                extras={"resistance": snap.resistance, "rsi": snap.rsi},
            )

        return None
