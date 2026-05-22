"""Monte Carlo pré-trade — simula N caminhos de preço para estimar a
probabilidade real de o TP/SL bater antes do horizonte, e o valor
esperado (EV) líquido do setup.

Modelo: Geometric Brownian Motion (GBM)
    S_{t+1} = S_t * exp((μ − σ²/2)·dt + σ·√dt·Z),   Z ~ N(0,1)

μ (drift) e σ (vol) calibrados nos log-returns recentes do timeframe
do setup. Como o operador é o stop ou o take, simulamos bar-a-bar e
detectamos a primeira fronteira atingida — sem isso o GBM "salta" e
subestima a probabilidade de stop.

Saída: P(TP), P(SL), P(timeout), retorno médio dos caminhos vencedores,
e EV em pontos % do notional já descontando taxas round-trip.

Por que isso ajuda: setups com R:R=2 podem ter P(TP) < 33% (= breakeven)
quando o stop está em zona de ruído. O score heurístico não pega isso —
o MC sim. Roda em ~15ms pra 2.000 caminhos × 48 bars (vetorizado).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.types import Side


@dataclass(slots=True)
class MCResult:
    p_tp: float                 # 0..1
    p_sl: float                 # 0..1
    p_timeout: float            # 0..1 (nem TP nem SL no horizonte)
    expected_value_pct: float   # EV em % do notional, descontado de taxas
    sample_paths: int
    horizon_bars: int
    drift_per_bar: float        # μ usado (informativo)
    vol_per_bar: float          # σ usada (informativo)

    def as_dict(self) -> dict[str, float]:
        return {
            "p_tp": round(self.p_tp, 4),
            "p_sl": round(self.p_sl, 4),
            "p_timeout": round(self.p_timeout, 4),
            "ev_pct": round(self.expected_value_pct, 4),
        }


def _log_returns(close: pd.Series, max_window: int = 200) -> np.ndarray:
    """Log-returns dos últimos `max_window` candles."""
    arr = close.tail(max_window).to_numpy(dtype=np.float64)
    if arr.size < 5:
        return np.array([], dtype=np.float64)
    return np.diff(np.log(arr))


def simulate(
    df: pd.DataFrame,
    *,
    entry: float,
    stop: float,
    take_profit: float,
    side: Side,
    simulations: int = 2000,
    horizon_bars: int = 48,
    fee_round_trip_pct: float = 0.08,
    use_drift: bool = False,
    drift_override: float | None = None,
    rng: np.random.Generator | None = None,
) -> MCResult:
    """Simula `simulations` caminhos e retorna métricas agregadas.

    Args:
        df: OHLCV do timeframe do setup, ordenado por tempo.
        entry/stop/take_profit: níveis do plano de trade.
        side: LONG ou SHORT (determina qual fronteira é TP/SL).
        simulations: nº de caminhos. 2000 é o sweet-spot vel/precisão.
        horizon_bars: máximo de candles à frente. 48 × 15m = 12h.
        fee_round_trip_pct: taxa total estimada (taker open + close).
            0.08% bate com Binance Futures taker (0.04% × 2).
        use_drift: se False, drift=0 (random walk neutro). Drift estimado
            no histórico recente é estatisticamente fraco e tende a
            superestimar a vantagem direcional do sinal — manter False
            é mais conservador. Vire True só pra estudo.
        drift_override: se fornecido, sobrescreve o μ calculado. Usado
            pelo Order Book Intelligence pra injetar um drift derivado
            do imbalance do livro. Tem precedência sobre `use_drift`.
        rng: gerador opcional pra testes determinísticos.

    Returns:
        MCResult com probabilidades e EV.
    """
    if df.empty or simulations <= 0 or horizon_bars <= 0:
        return MCResult(0.0, 0.0, 1.0, 0.0, 0, 0, 0.0, 0.0)

    rets = _log_returns(df["close"])
    if rets.size < 5:
        return MCResult(0.0, 0.0, 1.0, 0.0, 0, 0, 0.0, 0.0)

    sigma = float(np.std(rets, ddof=1))
    if sigma <= 0 or not np.isfinite(sigma):
        return MCResult(0.0, 0.0, 1.0, 0.0, 0, 0, 0.0, 0.0)

    if drift_override is not None:
        mu = float(drift_override)
    elif use_drift:
        mu = float(np.mean(rets))
    else:
        mu = 0.0

    rng = rng or np.random.default_rng()
    # Matriz (sim × bars) de log-returns aleatórios. Vetorização total.
    z = rng.standard_normal(size=(simulations, horizon_bars))
    step_logret = (mu - 0.5 * sigma * sigma) + sigma * z
    # Caminhos = entry * exp(cumsum(log-returns)) — preço bar-a-bar.
    paths = entry * np.exp(np.cumsum(step_logret, axis=1))

    if side == Side.LONG:
        hit_tp = paths >= take_profit
        hit_sl = paths <= stop
        win_pct = (take_profit - entry) / entry * 100
        loss_pct = (stop - entry) / entry * 100  # negativo
    else:
        hit_tp = paths <= take_profit
        hit_sl = paths >= stop
        win_pct = (entry - take_profit) / entry * 100
        loss_pct = (entry - stop) / entry * 100  # negativo

    # Primeira ocorrência de cada fronteira em cada caminho.
    # argmax retorna 0 se nada é True; combinamos com any() pra distinguir.
    first_tp = np.where(hit_tp.any(axis=1), hit_tp.argmax(axis=1), horizon_bars + 1)
    first_sl = np.where(hit_sl.any(axis=1), hit_sl.argmax(axis=1), horizon_bars + 1)

    tp_wins = first_tp < first_sl                              # TP primeiro
    sl_loses = first_sl < first_tp                             # SL primeiro
    timeouts = ~(tp_wins | sl_loses)                           # nenhum

    p_tp = float(tp_wins.sum()) / simulations
    p_sl = float(sl_loses.sum()) / simulations
    p_to = float(timeouts.sum()) / simulations

    # EV (em % do notional) descontando taxa total estimada.
    # Timeouts são tratados como 0 PnL (mais conservador que marcar a
    # MtM no fim do horizonte, que dependeria do drift estimado).
    ev_pct = (p_tp * win_pct + p_sl * loss_pct) - fee_round_trip_pct

    return MCResult(
        p_tp=p_tp,
        p_sl=p_sl,
        p_timeout=p_to,
        expected_value_pct=ev_pct,
        sample_paths=simulations,
        horizon_bars=horizon_bars,
        drift_per_bar=mu,
        vol_per_bar=sigma,
    )
