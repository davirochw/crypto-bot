"""Breakout with volume: enter on confirmed S/R break with volume expansion."""

from __future__ import annotations

from core.types import MarketContext, Side
from strategies.base import Strategy, StrategyResult


class BreakoutVolumeStrategy(Strategy):
    name = "breakout_volume"
    setup_timeframe = "15m"
    requires_macro_alignment = False  # breakouts can lead reversals

    def evaluate(self, ctx: MarketContext) -> StrategyResult | None:
        snap = ctx.snapshots.get(self.setup_timeframe)
        if not snap or not snap.breakout:
            return None

        if snap.resistance and snap.close >= snap.resistance:
            return StrategyResult(
                side=Side.LONG,
                setup_timeframe=self.setup_timeframe,
                entry=snap.close,
                reasons=[
                    f"Breakout above resistance {snap.resistance:.6g}",
                    "Volume expansion confirmed",
                ],
                extras={"resistance": snap.resistance, "volume_ratio": (snap.volume / (snap.volume_ma or snap.volume))},
            )

        if snap.support and snap.close <= snap.support:
            return StrategyResult(
                side=Side.SHORT,
                setup_timeframe=self.setup_timeframe,
                entry=snap.close,
                reasons=[
                    f"Breakdown below support {snap.support:.6g}",
                    "Volume expansion confirmed",
                ],
                extras={"support": snap.support, "volume_ratio": (snap.volume / (snap.volume_ma or snap.volume))},
            )

        return None
