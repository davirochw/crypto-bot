"""Strategy interface. Subclasses implement `evaluate` and a couple of metadata fields.

The orchestrator calls each strategy with a MarketContext and collects
StrategyResult objects. Risk + scoring + AI commentary are added later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.types import MarketContext, Side


@dataclass(slots=True)
class StrategyResult:
    """Raw output from a strategy before scoring/risk/AI enrichment."""

    side: Side
    setup_timeframe: str
    entry: float
    reasons: list[str]
    extras: dict[str, float | None]


class Strategy(ABC):
    name: str = "base"
    setup_timeframe: str = "15m"
    requires_macro_alignment: bool = True

    @abstractmethod
    def evaluate(self, ctx: MarketContext) -> StrategyResult | None:
        """Return a result if a setup is detected, else None."""
        raise NotImplementedError

    def _macro_aligned(self, ctx: MarketContext, side: Side) -> bool:
        if not self.requires_macro_alignment:
            return True
        from core.types import Trend
        target = Trend.BULLISH if side == Side.LONG else Trend.BEARISH
        return ctx.macro_trend == target
