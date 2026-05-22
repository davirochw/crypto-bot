"""Domain types shared across modules. Pydantic models for validation + serialization."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Trend(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class MarketRegime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"


class IndicatorSnapshot(BaseModel):
    """Snapshot of all indicators on a single timeframe at a single moment."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    timeframe: str
    timestamp: datetime
    close: float
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    ema9: float | None = None
    ema21: float | None = None
    ema200: float | None = None
    vwap: float | None = None
    atr: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bb_middle: float | None = None
    volume: float = 0.0
    volume_ma: float | None = None
    support: float | None = None
    resistance: float | None = None
    breakout: bool = False
    trend: Trend = Trend.NEUTRAL


class MarketContext(BaseModel):
    """Aggregated multi-timeframe view used by strategies + AI."""

    symbol: str
    snapshots: dict[str, IndicatorSnapshot] = Field(default_factory=dict)
    funding_rate: float | None = None
    open_interest: float | None = None
    long_short_ratio: float | None = None
    regime: MarketRegime = MarketRegime.RANGING
    macro_trend: Trend = Trend.NEUTRAL
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Signal(BaseModel):
    """A trade idea emitted by a strategy. The AI/risk modules enrich it."""

    symbol: str
    side: Side
    strategy: str
    timeframe: str
    entry: float
    stop: float
    take_profit: float
    risk_reward: float
    score: int = Field(ge=0, le=100)
    # Tamanho da posição em USDT já calculado pelo risk planner (com cap
    # aplicado). 0 significa "não calculado" — defensivo, paper_trade
    # vai pular abrir a posição nesse caso pra não usar valor hardcoded.
    position_size: float = 0.0
    risk_amount: float = 0.0   # USDT em risco se o stop bater
    # Monte Carlo pré-trade (None = não rodou ou desabilitado).
    p_tp: float | None = None         # 0..1 — prob. de bater TP antes do SL
    p_sl: float | None = None         # 0..1 — prob. de bater SL antes do TP
    ev_pct: float | None = None       # EV em % do notional, líquido de taxas
    # Order Book Intelligence (None = OB desligado ou indisponível).
    ob_imbalance: float | None = None   # -1..+1, + = bid-heavy, - = ask-heavy
    ob_spread_pct: float | None = None  # spread bid/ask em % do mid
    reasons: list[str] = Field(default_factory=list)
    indicators: dict[str, float | None] = Field(default_factory=dict)
    ai_commentary: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_log(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "strategy": self.strategy,
            "timeframe": self.timeframe,
            "entry": self.entry,
            "stop": self.stop,
            "take_profit": self.take_profit,
            "rr": self.risk_reward,
            "score": self.score,
            "size": self.position_size,
            "risk": self.risk_amount,
            "p_tp": self.p_tp,
            "p_sl": self.p_sl,
            "ev_pct": self.ev_pct,
            "created_at": self.created_at.isoformat(),
        }


class PaperTrade(BaseModel):
    """Open or closed simulated trade.

    `size` é o NOTIONAL da posição (em USDT). PnL e taxas incidem sobre
    ele. Em modo alavancado, `margin` é o capital efetivamente bloqueado
    (margin = size / leverage); a perda máxima sem stop ≈ margin.
    """

    id: str
    symbol: str
    side: Side
    entry: float
    stop: float
    take_profit: float
    size: float                  # notional em USDT (margin × leverage)
    margin: float = 0.0          # USDT bloqueado da conta. 0 = sem leverage
    leverage: float = 1.0        # 1.0 = sem leverage (spot-like)
    opened_at: datetime
    closed_at: datetime | None = None
    exit_price: float | None = None
    pnl: float | None = None
    fees_paid: float = 0.0
    reason_close: str | None = None
    # Metadata para aprendizado adaptativo
    strategy: str = "unknown"    # estratégia que gerou o sinal
    signal_score: int = 0        # score do sinal original
    setup_timeframe: str = "15m" # timeframe do setup

    @property
    def is_open(self) -> bool:
        return self.closed_at is None
