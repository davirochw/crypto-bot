"""Markdown message templates for Telegram alerts."""

from __future__ import annotations

from core.types import MarketContext, Side, Signal

SIDE_EMOJI = {Side.LONG: "🟢", Side.SHORT: "🔴"}
SCORE_EMOJI = lambda s: "🔥" if s >= 85 else ("⭐" if s >= 75 else "✅")  # noqa: E731


def _fmt(value: float | None, digits: int = 6) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}g}"


def format_signal(signal: Signal) -> str:
    head = (
        f"{SCORE_EMOJI(signal.score)} *{signal.symbol}* "
        f"{SIDE_EMOJI[signal.side]} *{signal.side.value}*  "
        f"_(score {signal.score}/100)_"
    )
    body = (
        f"\n• Strategy: `{signal.strategy}` ({signal.timeframe})"
        f"\n• Entry: `{_fmt(signal.entry)}`"
        f"\n• Stop: `{_fmt(signal.stop)}`"
        f"\n• Take: `{_fmt(signal.take_profit)}`"
        f"\n• R:R: `{signal.risk_reward:.2f}`"
    )
    # Linha de Monte Carlo quando o MC rodou (probabilidades reais).
    if signal.p_tp is not None and signal.ev_pct is not None:
        body += (
            f"\n• 🎲 MC: P(TP)=`{signal.p_tp:.0%}`  "
            f"P(SL)=`{signal.p_sl or 0:.0%}`  "
            f"EV=`{signal.ev_pct:+.2f}%`"
        )
    # Linha do Order Book quando disponível — mostra para que lado o
    # livro está empurrando agora (input que alimentou o MC).
    if signal.ob_imbalance is not None:
        arrow = "🟢" if signal.ob_imbalance > 0.15 else ("🔴" if signal.ob_imbalance < -0.15 else "⚪")
        body += (
            f"\n• 📖 Book: imb=`{signal.ob_imbalance:+.2f}` {arrow}  "
            f"spread=`{signal.ob_spread_pct or 0:.3f}%`"
        )
    if signal.position_size > 0:
        body += f"\n• Size: `${signal.position_size:.2f}`"
    if signal.reasons:
        body += "\n\n*Reasons:*\n" + "\n".join(f"• {r}" for r in signal.reasons[:5])
    if signal.ai_commentary:
        body += f"\n\n*IA:*\n_{signal.ai_commentary}_"
    body += "\n\n_⚠️ Copilot only — não é recomendação de investimento._"
    return head + body


def format_market_brief(brief: str, contexts: dict[str, MarketContext]) -> str:
    head = "*📊 Resumo de Mercado*\n"
    rows: list[str] = []
    for sym, ctx in contexts.items():
        meso = ctx.snapshots.get("1h")
        if not meso:
            continue
        rows.append(
            f"• `{sym}`: {ctx.macro_trend.value.lower()} | "
            f"{ctx.regime.value.lower()} | RSI(1h) {_fmt(meso.rsi, 4)}"
        )
    body = "\n".join(rows) or "_(no data)_"
    if brief:
        body += f"\n\n*IA:*\n_{brief}_"
    return head + body
