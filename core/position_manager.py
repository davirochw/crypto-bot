"""Position re-evaluation — decide se uma posição aberta ainda vale a pena.

Roda a cada scan (60s por padrão) sobre cada posição aberta. Usa as
mesmas ferramentas que aprovaram o trade (Monte Carlo + Order Book +
contexto multi-TF) mas agora **olhando do preço atual** — não do entry.

A pergunta: "Se eu fosse abrir esse mesmo trade AGORA, com esse preço,
esse book, essa vol — eu abriria?" Se a resposta é "não", fecha.

Por que isso importa: o paper trader puro só fecha em stop/TP. Em
mercado lateral, isso significa segurar posição por horas esperando
TP que nunca vem, enquanto a vol decai e a oportunidade morre. Pior:
quando o setup VIRA (book inverte, trend flipa), você fica no caminho
do trem em vez de sair pelo preço atual (próximo ao entry) e cortar
a perda em 0.1% em vez de 0.7% no stop.

Filosofia: ser conservador nas saídas. Custo de fechar errado (deixar
de ver o TP bater) < custo de manter posição com matemática contra
(provavelmente vai pro stop). Quando em dúvida, prefere manter — só
fecha quando há sinal claro de reversão.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from core.monte_carlo import simulate as mc_simulate
from core.order_book import OrderBookFeatures, imbalance_to_drift
from core.types import MarketContext, PaperTrade, Side, Trend


@dataclass(slots=True)
class ExitDecision:
    action: str           # "KEEP" | "CLOSE"
    reason: str
    # Métricas que justificaram a decisão (úteis pro log/dashboard):
    p_tp_now: float | None = None
    ev_now_pct: float | None = None
    ob_imbalance_now: float | None = None
    macro_trend_now: str | None = None


def _opposite(side: Side) -> Trend:
    """Trend que vai CONTRA o trade (LONG temme BEARISH como inimigo)."""
    return Trend.BEARISH if side == Side.LONG else Trend.BULLISH


def evaluate(
    trade: PaperTrade,
    current_price: float,
    ctx: MarketContext,
    df_setup_tf: pd.DataFrame | None,
    ob: OrderBookFeatures | None,
    *,
    mc_enabled: bool = True,
    mc_ev_bailout: float = -0.5,
    mc_p_tp_bailout: float = 0.15,
    mc_horizon_bars: int = 24,
    mc_simulations: int = 1000,
    ob_flip_threshold: float = 0.35,
    ob_drift_scale: float = 0.15,
    trend_flip_enabled: bool = True,
    time_stale_hours: float = 0.0,   # 0 = desabilita time-stop
) -> ExitDecision:
    """Avalia se trade ainda merece ficar aberto. Não fecha nada — só decide."""

    # ---- 1) Order Book virou contra ----
    # Mais agressivo que MC porque o book reflete intenção imediata e
    # mudanças bruscas tendem a ser bem informadas (whales posicionando).
    ob_imb = ob.imbalance if ob else None
    if ob is not None:
        if trade.side == Side.LONG and ob.imbalance < -ob_flip_threshold:
            return ExitDecision(
                "CLOSE",
                f"book virou ask-heavy (imb={ob.imbalance:+.2f})",
                ob_imbalance_now=ob.imbalance,
            )
        if trade.side == Side.SHORT and ob.imbalance > ob_flip_threshold:
            return ExitDecision(
                "CLOSE",
                f"book virou bid-heavy (imb={ob.imbalance:+.2f})",
                ob_imbalance_now=ob.imbalance,
            )

    # ---- 2) Macro trend reverteu ----
    if trend_flip_enabled and ctx.macro_trend == _opposite(trade.side):
        return ExitDecision(
            "CLOSE",
            f"macro trend virou {ctx.macro_trend.value}",
            ob_imbalance_now=ob_imb,
            macro_trend_now=ctx.macro_trend.value,
        )

    # ---- 3) Time stale ----
    if time_stale_hours > 0:
        opened = trade.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        hours_open = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
        if hours_open >= time_stale_hours:
            return ExitDecision(
                "CLOSE",
                f"aberto há {hours_open:.1f}h sem TP/SL — capital travado",
                ob_imbalance_now=ob_imb,
            )

    # ---- 4) Monte Carlo do estado atual ----
    p_tp_now = ev_now = None
    if mc_enabled and df_setup_tf is not None and len(df_setup_tf) >= 30:
        # Drift do book agora (não o de quando entramos no trade).
        drift = None
        if ob is not None and ob_drift_scale > 0:
            # σ proxy: vol relativa do último candle do TF (ATR-style)
            recent = df_setup_tf.tail(1).iloc[0]
            sigma_proxy = abs(recent["high"] - recent["low"]) / max(recent["close"], 1e-9)
            drift = imbalance_to_drift(ob.imbalance, sigma_proxy, scale=ob_drift_scale)

        mc = mc_simulate(
            df_setup_tf,
            entry=current_price,  # importante: olhar do PREÇO ATUAL
            stop=trade.stop,
            take_profit=trade.take_profit,
            side=trade.side,
            simulations=mc_simulations,
            horizon_bars=mc_horizon_bars,
            drift_override=drift,
        )
        p_tp_now, ev_now = mc.p_tp, mc.expected_value_pct

        if ev_now < mc_ev_bailout:
            return ExitDecision(
                "CLOSE",
                f"MC EV virou {ev_now:+.2f}% (piso {mc_ev_bailout:+.2f}%)",
                p_tp_now=p_tp_now,
                ev_now_pct=ev_now,
                ob_imbalance_now=ob_imb,
            )
        if p_tp_now < mc_p_tp_bailout:
            return ExitDecision(
                "CLOSE",
                f"MC P(TP) caiu pra {p_tp_now:.0%} (piso {mc_p_tp_bailout:.0%})",
                p_tp_now=p_tp_now,
                ev_now_pct=ev_now,
                ob_imbalance_now=ob_imb,
            )

    return ExitDecision(
        "KEEP",
        "setup ainda válido",
        p_tp_now=p_tp_now,
        ev_now_pct=ev_now,
        ob_imbalance_now=ob_imb,
        macro_trend_now=ctx.macro_trend.value,
    )
