"""Async Binance Futures REST client (CCXT under the hood).

Read-only: OHLCV, funding, OI, long/short ratio, order book. The copilot
never sends orders, so private endpoints are not used.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from aiohttp.resolver import ThreadedResolver
import ccxt.async_support as ccxt
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.logger import logger
from core.settings import settings


def _build_session() -> aiohttp.ClientSession:
    """Build an aiohttp session that uses Python's `socket.getaddrinfo`
    for DNS instead of `aiodns` (c-ares).

    Why: on Windows + Python 3.13, aiodns frequently fails with
    `Could not contact DNS servers` because c-ares can't read the
    system resolver config reliably. ThreadedResolver delegates to
    Python's stdlib resolver, which uses the OS DNS — same path
    `curl`, `nslookup` and your browser take. Cost: tiny (one
    threadpool dispatch per host, cached after).
    """
    connector = aiohttp.TCPConnector(
        resolver=ThreadedResolver(),
        ttl_dns_cache=300,           # cache 5min, reduces resolver pressure
        family=0,                    # let OS pick v4/v6
        ssl=True,
        limit=64,
    )
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
    return aiohttp.ClientSession(connector=connector, timeout=timeout)


class BinanceClient:
    def __init__(self) -> None:
        self._exchange = ccxt.binanceusdm(
            {
                "apiKey": settings.binance_api_key or None,
                "secret": settings.binance_api_secret or None,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        )
        if settings.binance_testnet:
            self._exchange.set_sandbox_mode(True)
        # Session is created lazily inside `_ensure_session()` because
        # `aiohttp.ThreadedResolver()` calls `asyncio.get_running_loop()`
        # in its constructor — building it in __init__ blows up when the
        # client is instantiated from sync code (e.g. main() before
        # `asyncio.run()` starts the loop).
        self._session: aiohttp.ClientSession | None = None
        self._markets_loaded = False
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> None:
        """Build + attach the aiohttp session on first async call.

        Idempotent. Called at the top of every public async method.
        """
        if self._session is not None and not self._session.closed:
            return
        self._session = _build_session()
        self._exchange.session = self._session

    async def __aenter__(self) -> "BinanceClient":
        await self.ensure_markets()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        # CCXT close() does NOT close the session we injected (it would
        # only close the one it created itself). We own this session, so
        # we close it ourselves to avoid "Unclosed client session" warns.
        try:
            await self._exchange.close()
        finally:
            if self._session is not None and not self._session.closed:
                await self._session.close()

    async def ensure_markets(self) -> None:
        """Load Binance Futures market list (symbols, precision, limits).

        Resiliente a falhas: tenta até 3× com backoff. Se o erro for
        geo-block (HTTP 451 / "restricted location" / "Eligibility"),
        loga uma mensagem amigável apontando o caminho — Testnet ou
        outra exchange — em vez de só cuspir o stack trace.
        """
        if self._markets_loaded:
            return
        async with self._lock:
            if self._markets_loaded:
                return
            await self._ensure_session()
            last_exc: Exception | None = None
            for attempt in range(1, 4):
                try:
                    await self._exchange.load_markets()
                    self._markets_loaded = True
                    logger.info(
                        "Binance markets loaded ({} symbols, testnet={}).",
                        len(self._exchange.markets),
                        settings.binance_testnet,
                    )
                    return
                except Exception as exc:  # noqa: BLE001 — queremos diagnosticar tudo
                    last_exc = exc
                    # CCXT envelopa o erro real (ConnectionError, DNS, etc.)
                    # como ExchangeNotAvailable e mostra só "GET <url>".
                    # Pegamos o __cause__ pra ver o motivo de verdade.
                    cause = exc.__cause__
                    full = f"{exc} | cause={type(cause).__name__ if cause else 'n/a'}: {cause!s}"
                    if _is_geoblock(full):
                        logger.error(
                            "Binance Futures bloqueada para esse IP "
                            "(provável restrição geográfica — Brasil/EUA/etc). "
                            "Soluções: (1) `BINANCE_TESTNET=true` no .env "
                            "(testnet costuma funcionar globalmente), "
                            "(2) usar VPN, ou (3) trocar para outra exchange "
                            "(Bybit já está no roadmap). Detalhe: {}",
                            full[:300],
                        )
                        raise
                    backoff = 2 ** attempt
                    logger.warning(
                        "load_markets() tentativa {}/3 falhou: {}. "
                        "Retentando em {}s.",
                        attempt, full[:300], backoff,
                    )
                    await asyncio.sleep(backoff)
            assert last_exc is not None
            raise last_exc

    @staticmethod
    def _to_ccxt_symbol(symbol: str) -> str:
        if "/" in symbol:
            return symbol
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"
        return symbol

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.RequestTimeout)),
    )
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        await self.ensure_markets()
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        raw = await self._exchange.fetch_ohlcv(ccxt_symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((ccxt.NetworkError, ccxt.RequestTimeout)),
    )
    async def fetch_funding_rate(self, symbol: str) -> float | None:
        try:
            await self.ensure_markets()
            data = await self._exchange.fetch_funding_rate(self._to_ccxt_symbol(symbol))
            return float(data.get("fundingRate", 0.0)) if data else None
        except ccxt.BaseError as exc:
            logger.warning("funding_rate({}) failed: {}", symbol, exc)
            return None

    async def fetch_open_interest(self, symbol: str) -> float | None:
        try:
            data = await self._exchange.fetch_open_interest(self._to_ccxt_symbol(symbol))
            return float(data.get("openInterestAmount") or data.get("openInterestValue") or 0.0)
        except ccxt.BaseError as exc:
            logger.warning("open_interest({}) failed: {}", symbol, exc)
            return None

    async def fetch_long_short_ratio(self, symbol: str) -> float | None:
        """Top-trader long/short ratio. Uses the raw Binance endpoint via ccxt."""
        try:
            base = symbol[:-4] if symbol.endswith("USDT") else symbol.split("/")[0]
            params = {"symbol": f"{base}USDT", "period": "5m", "limit": 1}
            data = await self._exchange.fapiPublicGetFuturesDataTopLongShortPositionRatio(params)
            if data:
                return float(data[0].get("longShortRatio", 0.0))
        except (ccxt.BaseError, AttributeError, KeyError, IndexError, ValueError) as exc:
            logger.debug("long_short_ratio({}) failed: {}", symbol, exc)
        return None

    async def fetch_order_book(self, symbol: str, limit: int = 50) -> dict[str, Any] | None:
        try:
            return await self._exchange.fetch_order_book(self._to_ccxt_symbol(symbol), limit=limit)
        except ccxt.BaseError as exc:
            logger.warning("order_book({}) failed: {}", symbol, exc)
            return None

    async def fetch_all_timeframes(
        self, symbol: str, timeframes: list[str], limit: int = 300
    ) -> dict[str, pd.DataFrame]:
        results = await asyncio.gather(
            *[self.fetch_ohlcv(symbol, tf, limit=limit) for tf in timeframes],
            return_exceptions=True,
        )
        out: dict[str, pd.DataFrame] = {}
        for tf, res in zip(timeframes, results):
            if isinstance(res, Exception):
                logger.error("fetch_ohlcv({}, {}) failed: {}", symbol, tf, res)
                continue
            out[tf] = res
        return out


# Strings que a Binance retorna quando o IP está em região restrita.
# Casamos só por substring porque o JSON varia entre endpoints/versão.
_GEOBLOCK_HINTS = (
    "restricted location",
    "Service unavailable from a restricted location",
    "Eligibility",
    "ineligible",
    "451",  # HTTP "Unavailable For Legal Reasons"
)


def _is_geoblock(message: str) -> bool:
    low = message.lower()
    return any(hint.lower() in low for hint in _GEOBLOCK_HINTS)
