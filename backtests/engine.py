"""Simple bar-by-bar backtester.

Walks through historical OHLCV, rebuilds context per bar, evaluates strategies,
opens paper-trade-style positions and records the outcome. Single-position-per-pair
to keep the engine readable; good enough for a copilot sanity check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from core.logger import logger
from core.risk import build_plan, is_setup_acceptable
from core.scoring import compute_score
from core.settings import settings
from core.types import MarketContext, Side
from indicators.technical import build_snapshot, compute_indicators
from strategies import ALL_STRATEGIES


@dataclass
class _OpenPosition:
    side: Side
    entry: float
    stop: float
    take: float
    opened_idx: int
    strategy: str


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    bars: int
    trades: int
    wins: int
    losses: int
    net_pct: float
    winrate_pct: float
    avg_rr: float
    history: list[dict[str, Any]] = field(default_factory=list)


class Backtester:
    """Single-pair, single-timeframe backtest using all registered strategies.

    Strategies that need higher TFs degrade gracefully — they just won't fire
    when the higher-TF snapshot is missing.
    """

    def __init__(self, warmup: int = 220) -> None:
        self.warmup = warmup
        self.strategies = [cls() for cls in ALL_STRATEGIES]

    def run(
        self, symbol: str, df: pd.DataFrame, timeframe: str = "15m"
    ) -> BacktestResult:
        if len(df) < self.warmup + 30:
            raise ValueError(f"Need at least {self.warmup + 30} bars, got {len(df)}")

        full = compute_indicators(df)
        position: _OpenPosition | None = None
        history: list[dict[str, Any]] = []
        wins = losses = 0
        net_pct = 0.0
        rr_sum = 0.0

        for i in range(self.warmup, len(full)):
            window = full.iloc[: i + 1]
            bar = window.iloc[-1]

            if position is not None:
                exit_px: float | None = None
                reason = ""
                if position.side == Side.LONG:
                    if bar["low"] <= position.stop:
                        exit_px, reason = position.stop, "stop"
                    elif bar["high"] >= position.take:
                        exit_px, reason = position.take, "take"
                else:
                    if bar["high"] >= position.stop:
                        exit_px, reason = position.stop, "stop"
                    elif bar["low"] <= position.take:
                        exit_px, reason = position.take, "take"

                if exit_px is not None:
                    pnl_pct = (exit_px - position.entry) / position.entry
                    if position.side == Side.SHORT:
                        pnl_pct = -pnl_pct
                    net_pct += pnl_pct * 100
                    if pnl_pct > 0:
                        wins += 1
                    else:
                        losses += 1
                    history.append(
                        {
                            "opened_idx": position.opened_idx,
                            "closed_idx": i,
                            "side": position.side.value,
                            "strategy": position.strategy,
                            "entry": position.entry,
                            "exit": exit_px,
                            "reason": reason,
                            "pnl_pct": round(pnl_pct * 100, 4),
                        }
                    )
                    position = None

            if position is not None:
                continue

            snap = build_snapshot(symbol, timeframe, window)
            ctx = MarketContext(symbol=symbol, snapshots={timeframe: snap},
                                macro_trend=snap.trend)

            for strat in self.strategies:
                if strat.setup_timeframe != timeframe:
                    continue
                res = strat.evaluate(ctx)
                if res is None or snap.atr is None:
                    continue
                plan = build_plan(res.side, res.entry, snap.atr, account_balance=1000.0)
                ok, _ = is_setup_acceptable(plan.risk_reward, (snap.atr / snap.close) * 100)
                if not ok:
                    continue
                score, _ = compute_score(ctx, timeframe, res.side, plan.risk_reward)
                if score < settings.min_score_to_alert:
                    continue
                position = _OpenPosition(
                    side=res.side,
                    entry=plan.entry,
                    stop=plan.stop,
                    take=plan.take_profit,
                    opened_idx=i,
                    strategy=strat.name,
                )
                rr_sum += plan.risk_reward
                break

        trades = wins + losses
        avg_rr = rr_sum / trades if trades else 0.0
        winrate = (wins / trades * 100) if trades else 0.0
        logger.info(
            "Backtest {} {}: trades={} winrate={:.1f}% net={:+.2f}%",
            symbol, timeframe, trades, winrate, net_pct,
        )
        return BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            bars=len(full),
            trades=trades,
            wins=wins,
            losses=losses,
            net_pct=round(net_pct, 4),
            winrate_pct=round(winrate, 2),
            avg_rr=round(avg_rr, 2),
            history=history,
        )
