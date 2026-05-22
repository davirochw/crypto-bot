"""Centralized configuration loaded from `.env` via Pydantic Settings.

Single source of truth for runtime configuration. Importing
`settings` from anywhere returns the same validated instance.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- General ----
    app_env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"
    timezone: str = "America/Sao_Paulo"

    # ---- Binance ----
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = False

    # ---- AI ----
    ai_provider: Literal["openrouter", "groq"] = "openrouter"
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4.5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # ---- Telegram ----
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = True

    # ---- Engine ----
    # NoDecode: pydantic-settings normalmente faz json.loads no valor cru
    # do .env quando o field é list[…]. Como a gente quer aceitar a forma
    # CSV ("BTCUSDT,ETHUSDT"), desliga o JSON parser e deixa o
    # field_validator(mode='before') abaixo cuidar do split.
    symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            # Majors (5)
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
            # Layer-1 estabelecidos (5)
            "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "TRXUSDT",
            # DeFi / Oracle (3)
            "LINKUSDT", "UNIUSDT", "AAVEUSDT",
            # L2 + L1 novos (4)
            "NEARUSDT", "APTUSDT", "SUIUSDT", "ARBUSDT",
            # Resto: clássico + AI volátil (3)
            "OPUSDT", "LTCUSDT", "TAOUSDT",
        ]
    )
    timeframes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["4h", "1h", "15m", "5m"]
    )
    scan_interval_seconds: int = 60
    min_score_to_alert: int = 70

    # ---- Risk ----
    default_risk_percent: float = 1.0
    default_rr_ratio: float = 2.0
    # ATR multiplier para o stop. 1.5 era apertado demais em TFs curtos
    # (5m/15m): ATR típico de cripto major no 15m fica em 0.25–0.35%,
    # então 1.5 ATR = ~0.4% de stop e TP de ~0.8% — as taxas (0.08%
    # round-trip taker) comem 10% do TP. 2.5 ATR dá stop de ~0.7% e
    # TP de ~1.4%, R:R líquido fica próximo do nominal.
    atr_stop_mult: float = 2.5
    # Piso ABSOLUTO para a distância TP-entry, em % do preço. Sinais
    # com TP mais perto que isso são descartados — não compensam taxa
    # + slippage. 0.8% = ~10× a taxa round-trip da Binance Futures.
    min_tp_pct: float = 0.8

    # ---- Monte Carlo pré-trade ----
    # Antes de emitir sinal, simula N caminhos de preço (GBM calibrado
    # pela vol histórica) e calcula P(TP), P(SL), EV. Filtra sinais
    # com EV ≤ MC_MIN_EV_PCT. Desabilite com MC_ENABLED=false.
    mc_enabled: bool = True
    mc_simulations: int = 2000        # 2000 é o sweet-spot vel/precisão
    mc_horizon_bars: int = 48         # candles à frente (48 × 15m = 12h)
    mc_min_ev_pct: float = 0.0        # piso de EV (% do notional) p/ emitir
    mc_min_p_tp: float = 0.30         # piso de P(TP) — descarta lottery tickets

    # ---- Order Book Intelligence ----
    # Lê o livro público da Binance, calcula imbalance bid/ask, e
    # converte em drift do GBM. Quando o book está direcional, o MC
    # incorpora isso e P(TP) sobe pra trades alinhados com a pressão
    # do book (e cai pros contra). Desligado = MC volta a random walk.
    ob_enabled: bool = True
    ob_depth_levels: int = 50         # níveis do book a baixar (mais = mais lento)
    ob_range_pct: float = 1.0         # janela ao redor do mid pra somar volume (±1%)
    ob_drift_scale: float = 0.15      # sensibilidade imbalance→drift (0 = desliga)

    # ---- Position Re-evaluation (Exit Manager) ----
    # A cada scan, reavalia cada posição aberta. Se MC do estado ATUAL
    # virou negativo, ou o book inverteu, ou a tendência macro flipou,
    # fecha antecipadamente no preço de mercado em vez de esperar
    # encostar no stop. Princípio: cortar perda pequena > segurar
    # esperando milagre.
    # 
    # ATENÇÃO: Se você está perdendo muito dinheiro com fechamentos
    # antecipados que não chegam ao TP, aumente os thresholds abaixo
    # para ser MENOS agressivo nas saídas antecipadas.
    exit_reeval_enabled: bool = True
    exit_mc_ev_bailout: float = -1.5         # EV abaixo disso = fecha (mais negativo = menos agressivo)
    exit_mc_p_tp_bailout: float = 0.08       # P(TP) abaixo disso = fecha (menor = menos agressivo)
    exit_mc_horizon_bars: int = 24           # horizonte do MC de saída (menor = mais conservador)
    exit_ob_flip_threshold: float = 0.50     # imbalance contra trade ≥ isso = fecha (maior = menos agressivo)
    exit_trend_flip_enabled: bool = True     # fecha se macro trend reverteu
    exit_time_stale_hours: float = 0.0       # 0 = desabilita time-stop; 8 = fecha após 8h

    # ---- Dashboard ----
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000

    # ---- Paper trade ----
    paper_initial_balance: float = 1000.0
    paper_fee_percent: float = 0.04
    # Cap de tamanho de posição como fração do SALDO ATUAL do paper.
    # Aplicado apenas quando o sizing é dinâmico (margin_per_trade=0).
    # Quando margem é fixa, esse cap é ignorado (margem absoluta vence).
    paper_max_position_pct: float = 0.5

    # ---- Margem fixa + Leverage ----
    # Modelo de Binance Futures: cada trade aloca uma MARGEM fixa, e o
    # notional da posição = margem × leverage. PnL e taxas incidem sobre
    # o NOTIONAL; perda máxima antes da liquidação ≈ margem.
    #
    # paper_margin_per_trade=2.0 + paper_leverage=10.0 → cada trade
    # aloca $2 da conta e abre posição de $20 notional. Com saldo $20
    # cabem 10 trades simultâneos teóricos.
    #
    # Se paper_margin_per_trade=0, volta ao modelo dinâmico (sizing por
    # % de risco / cap_pct).
    paper_margin_per_trade: float = 2.0
    paper_leverage: float = 10.0

    # ---- Paths ----
    project_root: Path = PROJECT_ROOT
    logs_dir: Path = PROJECT_ROOT / "logs"
    data_dir: Path = PROJECT_ROOT / "data"
    config_dir: Path = PROJECT_ROOT / "config"

    @field_validator("symbols", "timeframes", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [s.strip().upper() if "USDT" in s.upper() else s.strip()
                    for s in v.split(",") if s.strip()]
        return v

    @property
    def ai_api_key(self) -> str:
        return self.openrouter_api_key if self.ai_provider == "openrouter" else self.groq_api_key

    @property
    def ai_base_url(self) -> str:
        return self.openrouter_base_url if self.ai_provider == "openrouter" else self.groq_base_url

    @property
    def ai_model(self) -> str:
        return self.openrouter_model if self.ai_provider == "openrouter" else self.groq_model


@lru_cache(maxsize=1)
def _build_settings() -> Settings:
    s = Settings()
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


settings: Settings = _build_settings()
