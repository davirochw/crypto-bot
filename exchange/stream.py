"""Lightweight Binance Futures WebSocket (kline aggTrade-free) stream.

Used to get fast intra-bar updates without hammering the REST API. Optional —
the engine works fine on REST polling for the v1 copilot.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import websockets

from core.logger import logger

WS_URL = "wss://fstream.binance.com/stream?streams={streams}"


class KlineStream:
    def __init__(self, symbols: list[str], interval: str = "1m") -> None:
        self.symbols = [s.lower() for s in symbols]
        self.interval = interval
        self._stop_event = asyncio.Event()

    @property
    def _stream_path(self) -> str:
        return "/".join(f"{s}@kline_{self.interval}" for s in self.symbols)

    async def stop(self) -> None:
        self._stop_event.set()

    async def listen(
        self,
        on_kline: Callable[[dict], None] | None = None,
    ) -> AsyncIterator[dict]:
        url = WS_URL.format(streams=self._stream_path)
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("WS connected ({} symbols, {})", len(self.symbols), self.interval)
                    backoff = 1.0
                    async for raw in ws:
                        if self._stop_event.is_set():
                            break
                        msg = json.loads(raw)
                        data = msg.get("data", {}).get("k")
                        if not data or not data.get("x"):
                            continue
                        kline = {
                            "symbol": data["s"],
                            "open_time": data["t"],
                            "close_time": data["T"],
                            "open": float(data["o"]),
                            "high": float(data["h"]),
                            "low": float(data["l"]),
                            "close": float(data["c"]),
                            "volume": float(data["v"]),
                            "interval": data["i"],
                        }
                        if on_kline:
                            on_kline(kline)
                        yield kline
            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
                logger.warning("WS disconnect ({}). Reconnecting in {:.1f}s", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
