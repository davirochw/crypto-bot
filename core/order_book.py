"""Order Book Intelligence — extrai sinais quantitativos do livro de
ordens público da Binance Futures.

Idéia central: o estado do order book contém informação direcional que
nem indicador técnico (RSI/MACD) nem candle isolado capturam. Especificamente:

- **Imbalance** dos primeiros N níveis indica pressão compradora/vendedora
  imediata. Quando bid_vol >> ask_vol, há mais demanda esperando que
  oferta — o preço tem viés de alta no curto prazo.

- **Spread** estreito + boa depth dos dois lados = mercado líquido,
  estimativas de execução confiáveis. Spread alargado = stress, mais
  slippage esperado.

- **Walls** (clusters anormais de volume num nível só) funcionam como
  suporte/resistência efêmeros — preço tende a respeitar até serem
  consumidas ou retiradas.

Saída chave: `imbalance_to_drift()` mapeia o desequilíbrio do livro
para um drift por candle que pode ser plugado no Monte Carlo, fazendo
o GBM deixar de ser random walk neutro e incorporar a pressão atual
do book. Em mercado lateral isso costuma puxar P(TP) em +10-15 pp
quando o book está claramente direcional.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OrderBookFeatures:
    mid: float                  # (best_bid + best_ask) / 2
    spread_pct: float           # spread em % do mid
    imbalance: float            # (bid - ask) / (bid + ask) em ±N% do mid, [-1..+1]
    bid_depth_usdt: float       # volume total bid em USDT na janela
    ask_depth_usdt: float       # volume total ask em USDT na janela
    bid_wall: tuple[float, float] | None   # (preço, volume) da maior parede bid
    ask_wall: tuple[float, float] | None   # (preço, volume) da maior parede ask

    @property
    def direction_hint(self) -> str:
        if self.imbalance > 0.25:
            return "BID-HEAVY"
        if self.imbalance < -0.25:
            return "ASK-HEAVY"
        return "BALANCED"


def _depth_in_range(
    levels: list[list[float]], mid: float, range_pct: float, side: str
) -> tuple[float, list[tuple[float, float]]]:
    """Soma o volume em USDT até `range_pct` distância do mid.
    Retorna (volume_total, [(price, size), ...]) só dos níveis na janela.
    """
    if not levels:
        return 0.0, []
    limit_high = mid * (1 + range_pct / 100)
    limit_low = mid * (1 - range_pct / 100)
    total = 0.0
    selected: list[tuple[float, float]] = []
    for level in levels:
        # CCXT format: [price, size]
        if len(level) < 2:
            continue
        price = float(level[0])
        size = float(level[1])
        if side == "bid" and price < limit_low:
            break  # bids vêm decrescentes, podemos cortar
        if side == "ask" and price > limit_high:
            break
        if side == "bid" and price > mid:
            continue
        if side == "ask" and price < mid:
            continue
        total += price * size
        selected.append((price, size))
    return total, selected


def _detect_wall(
    levels: list[tuple[float, float]], factor: float = 3.0
) -> tuple[float, float] | None:
    """Detecta "parede": nível com volume ≥ factor × média dos demais."""
    if len(levels) < 5:
        return None
    sizes = [s for _, s in levels]
    mean = sum(sizes) / len(sizes)
    if mean <= 0:
        return None
    biggest = max(levels, key=lambda x: x[1])
    return biggest if biggest[1] >= mean * factor else None


def analyze(order_book: dict[str, Any], range_pct: float = 1.0) -> OrderBookFeatures | None:
    """Extrai features do order book.

    `range_pct` = janela ao redor do mid pra considerar a profundidade
    (1.0 = ±1%). Mais que isso polui com ordens distantes que pouco
    impactam execução imediata; menos que isso fica ruidoso demais.
    """
    if not order_book:
        return None
    bids = order_book.get("bids") or []
    asks = order_book.get("asks") or []
    if not bids or not asks:
        return None

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    if best_ask <= best_bid:
        return None
    mid = (best_bid + best_ask) / 2
    spread_pct = (best_ask - best_bid) / mid * 100

    bid_vol, bid_levels = _depth_in_range(bids, mid, range_pct, "bid")
    ask_vol, ask_levels = _depth_in_range(asks, mid, range_pct, "ask")
    if bid_vol + ask_vol <= 0:
        return None
    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)

    return OrderBookFeatures(
        mid=mid,
        spread_pct=spread_pct,
        imbalance=imbalance,
        bid_depth_usdt=bid_vol,
        ask_depth_usdt=ask_vol,
        bid_wall=_detect_wall(bid_levels),
        ask_wall=_detect_wall(ask_levels),
    )


def imbalance_to_drift(
    imbalance: float,
    sigma_per_bar: float,
    *,
    scale: float = 0.15,
    smoothing: float = 1.5,
) -> float:
    """Converte imbalance ∈ [-1,+1] em drift μ por candle pro GBM.

    Por que `tanh(imbalance × smoothing)` em vez de imbalance puro:
    proteção contra paredes spoof (book muito desequilibrado, mas
    artificial). Tanh achata os extremos sem zerar a informação.

    Por que `scale=0.15`: imbalance pleno (±1) gera drift de ±15%
    do sigma por candle. Em 48 candles, isso acumula até ±7.2% no
    preço (= boa parte do TP típico de 1.5-3%). Mais que isso e o
    drift dominaria a aleatoriedade.

    Para desligar (forçar random walk neutro): scale=0.
    """
    if scale <= 0 or sigma_per_bar <= 0:
        return 0.0
    effective = math.tanh(imbalance * smoothing)
    return effective * scale * sigma_per_bar
