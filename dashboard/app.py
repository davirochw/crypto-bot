"""FastAPI dashboard. Reads the live engine state — does not own it.

Embeds in the same process as the engine so we can avoid IPC. Uvicorn runs in
its own task; the engine runs in another.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.engine import CopilotEngine
from core.settings import settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def create_app(engine: CopilotEngine) -> FastAPI:
    app = FastAPI(title="Crypto Copilot Dashboard", version="0.1.0")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        # Starlette ≥0.30 espera `request` como primeiro arg posicional
        # (a forma legada `TemplateResponse(name, {"request": …})` deixou
        # de funcionar — o dict acaba sendo interpretado como o nome do
        # template e Jinja2 tenta usá-lo como cache key).
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "symbols": engine.symbols,
                "ai_provider": settings.ai_provider,
                "min_score": settings.min_score_to_alert,
            },
        )

    @app.get("/api/status")
    async def status():
        return {
            "running": engine._running,
            "scan_count": engine.scan_count,
            "last_scan_at": engine.last_scan_at.isoformat() if engine.last_scan_at else None,
            "ai_provider": settings.ai_provider,
            "telegram_enabled": engine.notifier.is_configured,
            "min_score_to_alert": settings.min_score_to_alert,
            "symbols": engine.symbols,
            "timeframes": engine.timeframes,
        }

    @app.get("/api/contexts")
    async def contexts():
        out = []
        for sym, ctx in engine.contexts.items():
            meso = ctx.snapshots.get("1h") or next(iter(ctx.snapshots.values()), None)
            if not meso:
                continue
            out.append(
                {
                    "symbol": sym,
                    "macro_trend": ctx.macro_trend.value,
                    "regime": ctx.regime.value,
                    "close": meso.close,
                    "rsi_1h": meso.rsi,
                    "atr": meso.atr,
                    "funding": ctx.funding_rate,
                    "open_interest": ctx.open_interest,
                    "long_short_ratio": ctx.long_short_ratio,
                    "fetched_at": ctx.fetched_at.isoformat(),
                }
            )
        return out

    @app.get("/api/signals")
    async def signals():
        return [
            {
                **s.model_dump(mode="json"),
                "side": s.side.value,
            }
            for s in list(engine.last_signals)
        ]

    @app.get("/api/paper")
    async def paper():
        if not engine.paper:
            return {"enabled": False}
        return {
            "enabled": True,
            "stats": engine.paper.stats(),
            "open_trades": [t.model_dump(mode="json") for t in engine.paper.open_trades.values()],
            "closed_trades": [
                t.model_dump(mode="json") for t in engine.paper.closed_trades[-20:]
            ],
        }

    @app.post("/api/market_brief")
    async def market_brief():
        text = await engine.market_brief_now()
        return {"sent": bool(text), "text": text}

    return app
