"""Compute classical TA indicators on an OHLCV dataframe.

Uses `ta` (pure Python, no native deps) — keeps install easy on Windows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import VolumeWeightedAveragePrice

from core.types import IndicatorSnapshot, Trend
from indicators.structure import find_support_resistance, detect_breakout


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append indicator columns to a copy of `df`. Original is not mutated."""
    if df.empty:
        return df
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    volume = out["volume"]

    out["rsi"] = RSIIndicator(close=close, window=14).rsi()

    macd = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()

    out["ema9"] = EMAIndicator(close=close, window=9).ema_indicator()
    out["ema21"] = EMAIndicator(close=close, window=21).ema_indicator()
    out["ema200"] = EMAIndicator(close=close, window=200).ema_indicator()

    try:
        out["vwap"] = VolumeWeightedAveragePrice(
            high=high, low=low, close=close, volume=volume, window=14
        ).volume_weighted_average_price()
    except (ValueError, ZeroDivisionError):
        out["vwap"] = np.nan

    out["atr"] = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

    bb = BollingerBands(close=close, window=20, window_dev=2)
    out["bb_upper"] = bb.bollinger_hband()
    out["bb_lower"] = bb.bollinger_lband()
    out["bb_middle"] = bb.bollinger_mavg()

    out["volume_ma"] = volume.rolling(window=20, min_periods=5).mean()

    return out


def _classify_trend(row: pd.Series) -> Trend:
    """EMA-based trend: price vs EMA200 and EMA9 vs EMA21."""
    if pd.isna(row.get("ema200")) or pd.isna(row.get("ema9")) or pd.isna(row.get("ema21")):
        return Trend.NEUTRAL
    above_macro = row["close"] > row["ema200"]
    fast_above_slow = row["ema9"] > row["ema21"]
    if above_macro and fast_above_slow:
        return Trend.BULLISH
    if not above_macro and not fast_above_slow:
        return Trend.BEARISH
    return Trend.NEUTRAL


def _f(v) -> float | None:
    if v is None or pd.isna(v):
        return None
    return float(v)


def build_snapshot(symbol: str, timeframe: str, df_with_indicators: pd.DataFrame) -> IndicatorSnapshot:
    """Build a single-point snapshot from the *last closed candle*."""
    last = df_with_indicators.iloc[-1]
    sr = find_support_resistance(df_with_indicators)
    breakout = detect_breakout(df_with_indicators, sr)

    return IndicatorSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=df_with_indicators.index[-1].to_pydatetime(),
        close=float(last["close"]),
        rsi=_f(last.get("rsi")),
        macd=_f(last.get("macd")),
        macd_signal=_f(last.get("macd_signal")),
        macd_hist=_f(last.get("macd_hist")),
        ema9=_f(last.get("ema9")),
        ema21=_f(last.get("ema21")),
        ema200=_f(last.get("ema200")),
        vwap=_f(last.get("vwap")),
        atr=_f(last.get("atr")),
        bb_upper=_f(last.get("bb_upper")),
        bb_lower=_f(last.get("bb_lower")),
        bb_middle=_f(last.get("bb_middle")),
        volume=float(last.get("volume", 0.0)),
        volume_ma=_f(last.get("volume_ma")),
        support=sr.get("support"),
        resistance=sr.get("resistance"),
        breakout=breakout,
        trend=_classify_trend(last),
    )
