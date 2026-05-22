"""Market structure: pivot-based S/R, breakout detection, simple volume profile.

Pure pandas/numpy — no scipy.signal dependency for portability.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _pivot_highs(series: pd.Series, left: int, right: int) -> pd.Series:
    rolling = series.rolling(window=left + right + 1, center=True).max()
    return series.where(series == rolling)


def _pivot_lows(series: pd.Series, left: int, right: int) -> pd.Series:
    rolling = series.rolling(window=left + right + 1, center=True).min()
    return series.where(series == rolling)


def find_support_resistance(
    df: pd.DataFrame,
    lookback: int = 100,
    pivot_left: int = 3,
    pivot_right: int = 3,
) -> dict[str, float | None]:
    """Return the closest support (below price) and resistance (above) using recent pivots."""
    if len(df) < lookback:
        lookback = len(df)
    recent = df.tail(lookback)
    if recent.empty:
        return {"support": None, "resistance": None}

    highs = _pivot_highs(recent["high"], pivot_left, pivot_right).dropna()
    lows = _pivot_lows(recent["low"], pivot_left, pivot_right).dropna()
    last_close = float(recent["close"].iloc[-1])

    resistance_candidates = highs[highs > last_close]
    support_candidates = lows[lows < last_close]

    resistance = float(resistance_candidates.min()) if not resistance_candidates.empty else None
    support = float(support_candidates.max()) if not support_candidates.empty else None
    return {"support": support, "resistance": resistance}


def detect_breakout(
    df: pd.DataFrame,
    sr: dict[str, float | None],
    volume_mult: float = 1.3,
) -> bool:
    """True if the last close broke S or R AND volume > volume_ma * mult."""
    if df.empty or len(df) < 21:
        return False
    last = df.iloc[-1]
    close = float(last["close"])
    volume = float(last["volume"])
    vol_ma = float(df["volume"].rolling(20).mean().iloc[-1] or 0)
    if vol_ma <= 0 or volume < vol_ma * volume_mult:
        return False

    prev_close = float(df["close"].iloc[-2])
    if sr.get("resistance") and prev_close < sr["resistance"] <= close:
        return True
    if sr.get("support") and prev_close > sr["support"] >= close:
        return True
    return False


def simple_volume_profile(df: pd.DataFrame, bins: int = 24) -> dict[str, float]:
    """Volume-by-price (POC + value area). Coarse but useful as context."""
    if df.empty:
        return {"poc": 0.0, "vah": 0.0, "val": 0.0}
    hi, lo = float(df["high"].max()), float(df["low"].min())
    if hi <= lo:
        return {"poc": float(df["close"].iloc[-1]), "vah": hi, "val": lo}
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    weights = np.zeros(bins)
    for h, l, v in zip(df["high"], df["low"], df["volume"]):
        idx_lo = np.searchsorted(edges, l, side="right") - 1
        idx_hi = np.searchsorted(edges, h, side="left")
        idx_lo = max(0, min(bins - 1, idx_lo))
        idx_hi = max(0, min(bins - 1, idx_hi))
        if idx_hi <= idx_lo:
            weights[idx_lo] += v
        else:
            share = v / (idx_hi - idx_lo + 1)
            weights[idx_lo : idx_hi + 1] += share
    poc_idx = int(np.argmax(weights))
    total = weights.sum()
    if total <= 0:
        return {"poc": float(centers[poc_idx]), "vah": hi, "val": lo}
    sorted_idx = np.argsort(weights)[::-1]
    cumulative = 0.0
    selected: list[int] = []
    for idx in sorted_idx:
        cumulative += weights[idx]
        selected.append(int(idx))
        if cumulative / total >= 0.7:
            break
    val = float(centers[min(selected)])
    vah = float(centers[max(selected)])
    return {"poc": float(centers[poc_idx]), "vah": vah, "val": val}
