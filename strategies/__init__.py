"""Trading strategies. Each returns a Signal or None given a MarketContext."""

from strategies.base import Strategy, StrategyResult
from strategies.impulse_macd import ImpulseMACDStrategy
from strategies.rsi_vwap_volume import RSIVWAPVolumeStrategy
from strategies.breakout_volume import BreakoutVolumeStrategy
from strategies.sr_reversal import SRReversalStrategy

ALL_STRATEGIES: list[type[Strategy]] = [
    ImpulseMACDStrategy,
    RSIVWAPVolumeStrategy,
    BreakoutVolumeStrategy,
    SRReversalStrategy,
]

__all__ = [
    "Strategy",
    "StrategyResult",
    "ImpulseMACDStrategy",
    "RSIVWAPVolumeStrategy",
    "BreakoutVolumeStrategy",
    "SRReversalStrategy",
    "ALL_STRATEGIES",
]
