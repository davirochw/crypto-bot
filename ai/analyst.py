"""High-level analyst: turns Signals + MarketContexts into human commentary."""

from __future__ import annotations

from ai.base import AIClient
from ai.prompts import (
    SYSTEM_ANALYST,
    SYSTEM_MARKET_BRIEF,
    USER_MARKET_BRIEF_TEMPLATE,
    USER_SETUP_TEMPLATE,
)
from core.logger import logger
from core.types import MarketContext, Signal


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _snapshots_block(ctx: MarketContext) -> str:
    lines: list[str] = []
    for tf in ("4h", "1h", "15m", "5m"):
        snap = ctx.snapshots.get(tf)
        if not snap:
            continue
        lines.append(
            f"- {tf}: close={_fmt(snap.close, 6)} trend={snap.trend.value} "
            f"RSI={_fmt(snap.rsi, 1)} MACDh={_fmt(snap.macd_hist, 4)} "
            f"EMA9/21/200={_fmt(snap.ema9, 4)}/{_fmt(snap.ema21, 4)}/{_fmt(snap.ema200, 4)} "
            f"ATR={_fmt(snap.atr, 4)} VWAP={_fmt(snap.vwap, 4)} "
            f"vol/MA={_fmt((snap.volume / snap.volume_ma) if snap.volume_ma else None, 2)}"
        )
    return "\n".join(lines) or "(no snapshots)"


def _contexts_block(contexts: dict[str, MarketContext]) -> str:
    rows: list[str] = []
    for sym, ctx in contexts.items():
        meso = ctx.snapshots.get("1h")
        if not meso:
            continue
        rows.append(
            f"- {sym}: macro={ctx.macro_trend.value} regime={ctx.regime.value} "
            f"close={_fmt(meso.close, 6)} RSI(1h)={_fmt(meso.rsi, 1)} "
            f"funding={_fmt(ctx.funding_rate, 5)}"
        )
    return "\n".join(rows) or "(no contexts)"


class AIAnalyst:
    def __init__(self, client: AIClient) -> None:
        self.client = client

    async def comment_on_signal(self, signal: Signal, ctx: MarketContext) -> str:
        if not self.client.api_key:
            return ""
        user_msg = USER_SETUP_TEMPLATE.format(
            symbol=signal.symbol,
            side=signal.side.value,
            strategy=signal.strategy,
            setup_tf=signal.timeframe,
            score=signal.score,
            rr=f"{signal.risk_reward:.2f}",
            snapshots_block=_snapshots_block(ctx),
            macro_trend=ctx.macro_trend.value,
            regime=ctx.regime.value,
            funding=_fmt(ctx.funding_rate, 5),
            oi=_fmt(ctx.open_interest, 0),
            lsr=_fmt(ctx.long_short_ratio, 2),
            reasons_block="\n".join(f"- {r}" for r in signal.reasons) or "- (none)",
        )
        try:
            return await self.client.chat(system=SYSTEM_ANALYST, user=user_msg, max_tokens=350)
        except Exception as exc:  # noqa: BLE001 — log and degrade gracefully
            logger.warning("AI commentary failed: {}", exc)
            return ""

    async def market_brief(self, contexts: dict[str, MarketContext]) -> str:
        if not self.client.api_key:
            return ""
        user_msg = USER_MARKET_BRIEF_TEMPLATE.format(
            contexts_block=_contexts_block(contexts)
        )
        try:
            return await self.client.chat(
                system=SYSTEM_MARKET_BRIEF, user=user_msg, max_tokens=300
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI market brief failed: {}", exc)
            return ""
