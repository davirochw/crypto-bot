"""Entrypoint. Boots the engine and (optionally) the dashboard in the same process.

Usage:
    python main.py                # engine + dashboard
    python main.py --no-dashboard # engine only
    python main.py scan-once      # one scan cycle then exit
    python main.py backtest BTCUSDT 15m
    python main.py brief          # send AI market brief to telegram
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn

from core.engine import CopilotEngine
from core.logger import logger
from core.settings import settings
from dashboard.app import create_app


async def _run_engine_and_dashboard(engine: CopilotEngine, with_dashboard: bool) -> None:
    tasks: list[asyncio.Task] = [asyncio.create_task(engine.run_forever(), name="engine")]

    if with_dashboard:
        app = create_app(engine)
        config = uvicorn.Config(
            app,
            host=settings.dashboard_host,
            port=settings.dashboard_port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(server.serve(), name="dashboard"))
        logger.info("Dashboard at http://{}:{}", settings.dashboard_host, settings.dashboard_port)

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            if exc := t.exception():
                logger.error("Task {} crashed: {}", t.get_name(), exc)
        for t in pending:
            t.cancel()
    finally:
        await engine.close()


async def _scan_once() -> None:
    engine = CopilotEngine()
    try:
        signals = await engine.scan_once()
        logger.info("scan-once produced {} signals", len(signals))
        for s in signals:
            logger.info("  {} {} {} score={} entry={} stop={} take={}",
                        s.symbol, s.side.value, s.strategy, s.score, s.entry, s.stop, s.take_profit)
    finally:
        await engine.close()


async def _market_brief() -> None:
    engine = CopilotEngine()
    try:
        await engine.scan_once()
        text = await engine.market_brief_now()
        logger.info("Market brief sent: {} chars", len(text))
    finally:
        await engine.close()


async def _backtest(symbol: str, timeframe: str, limit: int) -> None:
    from backtests.engine import Backtester
    from exchange.binance_client import BinanceClient

    async with BinanceClient() as client:
        df = await client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    bt = Backtester().run(symbol, df, timeframe=timeframe)
    print(f"\n=== Backtest {bt.symbol} {bt.timeframe} ({bt.bars} bars) ===")
    print(f"Trades   : {bt.trades} (W:{bt.wins} / L:{bt.losses})")
    print(f"Winrate  : {bt.winrate_pct:.2f}%")
    print(f"Avg R:R  : {bt.avg_rr:.2f}")
    print(f"Net P&L  : {bt.net_pct:+.2f}%\n")


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crypto Copilot")
    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="run engine (default)")
    run.add_argument("--no-dashboard", action="store_true")

    sub.add_parser("scan-once", help="single scan, then exit")
    sub.add_parser("brief", help="generate + send AI market brief")

    bt = sub.add_parser("backtest", help="backtest a single pair/TF")
    bt.add_argument("symbol")
    bt.add_argument("timeframe", nargs="?", default="15m")
    bt.add_argument("--limit", type=int, default=1000)

    return p.parse_args()


def main() -> int:
    args = _parse()
    cmd = args.cmd or "run"

    try:
        if cmd == "scan-once":
            asyncio.run(_scan_once())
        elif cmd == "brief":
            asyncio.run(_market_brief())
        elif cmd == "backtest":
            asyncio.run(_backtest(args.symbol.upper(), args.timeframe, args.limit))
        else:
            engine = CopilotEngine()
            asyncio.run(_run_engine_and_dashboard(engine, with_dashboard=not getattr(args, "no_dashboard", False)))
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
