"""Top-level orchestrator. Pulls data → builds context → runs strategies →
scores → asks AI → sends Telegram → updates paper trader.

Holds the in-memory state the dashboard reads from.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone

import pandas as pd

from ai import build_default_client
from ai.analyst import AIAnalyst
from ai.learner import AdaptiveLearner, get_learner
from alerts.formatter import format_market_brief, format_signal
from alerts.telegram import TelegramNotifier
from core.logger import logger
from core.monte_carlo import simulate as mc_simulate
from core.multi_timeframe import build_context
from core.order_book import analyze as ob_analyze, imbalance_to_drift
from core.position_manager import evaluate as evaluate_exit
from core.risk import build_plan, is_setup_acceptable
from core.scoring import compute_score
from core.settings import settings
from core.types import MarketContext, Side, Signal
from exchange.binance_client import BinanceClient
from indicators.technical import build_snapshot, compute_indicators
from paper_trade.simulator import PaperTrader
from strategies import ALL_STRATEGIES, Strategy


class CopilotEngine:
    """Long-running scanner. Async-first, single-process."""

    def __init__(
        self,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        scan_interval: int | None = None,
        enable_paper_trade: bool = True,
    ) -> None:
        self.symbols = symbols or settings.symbols
        self.timeframes = timeframes or settings.timeframes
        self.scan_interval = scan_interval or settings.scan_interval_seconds
        self.strategies: list[Strategy] = [cls() for cls in ALL_STRATEGIES]

        self.exchange = BinanceClient()
        self.notifier = TelegramNotifier()
        self.ai_client = build_default_client()
        self.analyst = AIAnalyst(self.ai_client)
        self.paper = PaperTrader() if enable_paper_trade else None
        self.learner = get_learner()  # Adaptive learning system

        # ---- Live state (read by dashboard) ----
        self.contexts: dict[str, MarketContext] = {}
        self.last_signals: deque[Signal] = deque(maxlen=50)
        self._dedupe_keys: dict[str, datetime] = {}
        self._cooldown_per_strategy = defaultdict(dict)
        self.last_scan_at: datetime | None = None
        self.scan_count = 0
        self._running = False
        
        # ---- Adaptive learning state ----
        self._trades_since_last_analysis = 0
        self._learning_analysis_interval = 10  # Analyze every N trades

    async def stop(self) -> None:
        self._running = False

    async def close(self) -> None:
        await asyncio.gather(
            self.exchange.close(),
            self.notifier.close(),
            self.ai_client.close(),
            return_exceptions=True,
        )

    async def scan_symbol(self, symbol: str) -> tuple[MarketContext | None, list[Signal]]:
        try:
            tf_data = await self.exchange.fetch_all_timeframes(symbol, self.timeframes)
        except Exception as exc:  # noqa: BLE001
            logger.error("fetch_all_timeframes({}) failed: {}", symbol, exc)
            return None, []

        snapshots = {}
        # `tf_frames` mantém o OHLCV cru por TF para o Monte Carlo —
        # `snapshots` só guarda o último valor de cada indicador.
        tf_frames: dict[str, pd.DataFrame] = {}
        for tf, df in tf_data.items():
            if df is None or df.empty:
                continue
            with_ind = compute_indicators(df)
            snapshots[tf] = build_snapshot(symbol, tf, with_ind)
            tf_frames[tf] = with_ind
        if not snapshots:
            return None, []

        funding, oi, lsr, book_raw = await asyncio.gather(
            self.exchange.fetch_funding_rate(symbol),
            self.exchange.fetch_open_interest(symbol),
            self.exchange.fetch_long_short_ratio(symbol),
            (self.exchange.fetch_order_book(symbol, limit=settings.ob_depth_levels)
             if settings.ob_enabled else asyncio.sleep(0, result=None)),
            return_exceptions=False,
        )
        ctx = build_context(symbol, snapshots,
                            funding_rate=funding, open_interest=oi, long_short_ratio=lsr)
        self.contexts[symbol] = ctx

        # Order Book features — usado depois pra dar drift ao MC.
        ob_features = ob_analyze(book_raw, range_pct=settings.ob_range_pct) if book_raw else None

        # ===== Re-avaliação de posições abertas DESTE símbolo =====
        # Antes de procurar entradas novas, decide se as posições que já
        # estão abertas pra esse par continuam fazendo sentido. Se o
        # setup virou (book/trend/MC), fecha no preço atual em vez de
        # esperar bater o stop — perde menos.
        self._reevaluate_open_positions(symbol, ctx, ob_features, tf_frames)

        signals: list[Signal] = []
        for strat in self.strategies:
            res = strat.evaluate(ctx)
            if res is None:
                continue
            setup_snap = ctx.snapshots.get(res.setup_timeframe)
            if not setup_snap or not setup_snap.atr:
                continue

            # Saldo de referência para o sizing: usa o saldo ATUAL do
            # paper (que diminui após perdas/aumenta após ganhos). Sem
            # paper trader habilitado cai no balance inicial das settings
            # — só serve pra preencher o Signal, não abre posição real.
            balance = self.paper.balance if self.paper else settings.paper_initial_balance

            plan = build_plan(res.side, res.entry, setup_snap.atr, account_balance=balance)
            atr_pct = setup_snap.atr / setup_snap.close * 100
            ok, why_not = is_setup_acceptable(plan.risk_reward, atr_pct)
            if not ok:
                logger.debug("Skip {} {} {}: {}", symbol, strat.name, res.side.value, why_not)
                continue

            # Filtro econômico: distância TP-entry pequena demais é morte
            # por mil cortes — taxa+slippage come o lucro mesmo se acertar.
            tp_pct = abs(plan.take_profit - plan.entry) / plan.entry * 100
            if tp_pct < settings.min_tp_pct:
                logger.debug(
                    "Skip {} {} {}: TP a {:.2f}% < piso {:.2f}% (taxas comem o lucro)",
                    symbol, strat.name, res.side.value, tp_pct, settings.min_tp_pct,
                )
                continue

            score, score_reasons = compute_score(ctx, res.setup_timeframe, res.side, plan.risk_reward)
            
            # Use adaptive minimum score per strategy if available
            adaptive_min_score = self.learner.get_adjusted_min_score(strat.name)
            min_score = adaptive_min_score
            
            if score < min_score:
                logger.debug(
                    "Skip {} {} {}: score={} < adaptive_min={}",
                    symbol, strat.name, res.side.value, score, min_score
                )
                continue

            # ===== Monte Carlo pré-trade (com drift do Order Book) =====
            # Roda 2k caminhos GBM a partir do entry. Drift é injetado
            # pelo imbalance do book quando OB_ENABLED — book bid-heavy
            # ajuda LONGs, ask-heavy ajuda SHORTs. P(TP) reflete isso.
            p_tp = p_sl = ev_pct = None
            ob_imb = ob_features.imbalance if ob_features else None
            ob_spread = ob_features.spread_pct if ob_features else None

            if settings.mc_enabled:
                df_setup = tf_frames.get(res.setup_timeframe)
                if df_setup is not None and len(df_setup) >= 30:
                    # Drift derivado do book: positivo = pressão de alta,
                    # útil pra LONG; negativo penaliza LONG e ajuda SHORT.
                    drift_override = None
                    if ob_features and settings.ob_drift_scale > 0:
                        # σ é estimada lá dentro do MC; aqui aproximamos pelo
                        # ATR pct do snapshot pra calcular drift ANTES de
                        # chamar. Aproximação válida porque ATR e std dos
                        # log-returns ficam na mesma ordem de grandeza.
                        sigma_proxy = (setup_snap.atr / setup_snap.close) if setup_snap.atr else 0.005
                        drift_override = imbalance_to_drift(
                            ob_features.imbalance,
                            sigma_per_bar=sigma_proxy,
                            scale=settings.ob_drift_scale,
                        )

                    mc = mc_simulate(
                        df_setup,
                        entry=plan.entry,
                        stop=plan.stop,
                        take_profit=plan.take_profit,
                        side=res.side,
                        simulations=settings.mc_simulations,
                        horizon_bars=settings.mc_horizon_bars,
                        drift_override=drift_override,
                    )
                    p_tp, p_sl, ev_pct = mc.p_tp, mc.p_sl, mc.expected_value_pct

                    if ev_pct <= settings.mc_min_ev_pct:
                        logger.info(
                            "MC veto {} {} {}: EV={:+.2f}% (piso {:+.2f}%) — "
                            "P(TP)={:.0%} P(SL)={:.0%} OB_imb={}",
                            symbol, strat.name, res.side.value,
                            ev_pct, settings.mc_min_ev_pct, p_tp, p_sl,
                            f"{ob_imb:+.2f}" if ob_imb is not None else "n/a",
                        )
                        continue
                    if p_tp < settings.mc_min_p_tp:
                        logger.info(
                            "MC veto {} {} {}: P(TP)={:.0%} < piso {:.0%} "
                            "(EV ainda era {:+.2f}%)",
                            symbol, strat.name, res.side.value,
                            p_tp, settings.mc_min_p_tp, ev_pct,
                        )
                        continue
                    mc_reason = f"MC: P(TP)={p_tp:.0%}, EV={ev_pct:+.2f}% (n={mc.sample_paths})"
                    if ob_features and settings.ob_drift_scale > 0:
                        mc_reason += f" | OB {ob_features.direction_hint} imb={ob_imb:+.2f}"
                    score_reasons.append(mc_reason)

            # Sizing:
            #   (a) modo MARGEM FIXA + LEVERAGE — usado quando o usuário
            #       define `paper_margin_per_trade > 0`. Cada trade
            #       aloca uma margem absoluta e o notional é margem × lev.
            #   (b) modo DINÂMICO — sizing por % de risco e cap_pct.
            if settings.paper_margin_per_trade > 0:
                margin = settings.paper_margin_per_trade
                lev = max(settings.paper_leverage, 1.0)
                sized = round(margin * lev, 2)
            else:
                max_size = max(balance * settings.paper_max_position_pct, 0.0)
                sized = round(min(plan.position_size, max_size), 2) if max_size > 0 else 0.0

            signal = Signal(
                symbol=symbol,
                side=res.side,
                strategy=strat.name,
                timeframe=res.setup_timeframe,
                entry=plan.entry,
                stop=plan.stop,
                take_profit=plan.take_profit,
                risk_reward=plan.risk_reward,
                score=score,
                position_size=sized,
                risk_amount=plan.risk_amount,
                p_tp=p_tp,
                p_sl=p_sl,
                ev_pct=ev_pct,
                ob_imbalance=ob_imb,
                ob_spread_pct=ob_spread,
                reasons=res.reasons + score_reasons,
                indicators={
                    k: v for k, v in (res.extras or {}).items() if isinstance(v, (int, float))
                },
            )
            if self._is_duplicate(signal):
                continue
            signals.append(signal)

        return ctx, signals

    def _reevaluate_open_positions(
        self,
        symbol: str,
        ctx: MarketContext,
        ob_features,
        tf_frames: dict[str, pd.DataFrame],
    ) -> None:
        """Para cada posição aberta deste símbolo, decide se fecha agora."""
        if not (self.paper and settings.exit_reeval_enabled):
            return

        # Pega posições abertas só deste símbolo (em geral 0-3).
        open_for_sym = [
            (tid, t) for tid, t in self.paper.open_trades.items()
            if t.symbol == symbol
        ]
        if not open_for_sym:
            return

        # Preço atual = último close do TF mais granular disponível
        # (5m > 15m > 1h > 4h). Mesma lógica do mark_to_market.
        current_price = None
        for tf in ("5m", "15m", "1h", "4h"):
            snap = ctx.snapshots.get(tf)
            if snap:
                current_price = snap.close
                break
        if current_price is None:
            return

        for tid, trade in open_for_sym:
            # `or` em DataFrame dá ValueError (truthiness ambíguo); usa
            # condicional explícita. Prefere 15m, cai pra qualquer um.
            df_setup = tf_frames.get("15m")
            if df_setup is None or df_setup.empty:
                df_setup = next(
                    (d for d in tf_frames.values() if d is not None and not d.empty),
                    None,
                )
            decision = evaluate_exit(
                trade,
                current_price=current_price,
                ctx=ctx,
                df_setup_tf=df_setup,
                ob=ob_features,
                mc_enabled=settings.mc_enabled,
                mc_ev_bailout=settings.exit_mc_ev_bailout,
                mc_p_tp_bailout=settings.exit_mc_p_tp_bailout,
                mc_horizon_bars=settings.exit_mc_horizon_bars,
                mc_simulations=max(500, settings.mc_simulations // 2),
                ob_flip_threshold=settings.exit_ob_flip_threshold,
                ob_drift_scale=settings.ob_drift_scale,
                trend_flip_enabled=settings.exit_trend_flip_enabled,
                time_stale_hours=settings.exit_time_stale_hours,
            )
            if decision.action == "CLOSE":
                closed_trade = self.paper.close_at_market(tid, current_price, decision.reason)
                # Registra trade fechado antecipadamente para aprendizado
                if closed_trade and settings.exit_reeval_enabled:
                    self.learner.record_trade(closed_trade)
                    self._trades_since_last_analysis += 1
                    
                    # Check if we should analyze and adapt
                    if self._trades_since_last_analysis >= self._learning_analysis_interval:
                        self._run_learning_analysis()
                        self._trades_since_last_analysis = 0
            else:
                # KEEP — log em DEBUG (não polui INFO) com métricas atuais.
                logger.debug(
                    "[REEVAL] keep {} {} {}: {} (P(TP)={}, EV={})",
                    trade.symbol, trade.side.value, trade.id, decision.reason,
                    f"{decision.p_tp_now:.0%}" if decision.p_tp_now is not None else "n/a",
                    f"{decision.ev_now_pct:+.2f}%" if decision.ev_now_pct is not None else "n/a",
                )

    def _is_duplicate(self, signal: Signal, cooldown_minutes: int = 30) -> bool:
        key = f"{signal.symbol}:{signal.strategy}:{signal.side.value}"
        last = self._dedupe_keys.get(key)
        now = datetime.now(timezone.utc)
        if last and (now - last).total_seconds() < cooldown_minutes * 60:
            return True
        self._dedupe_keys[key] = now
        return False

    async def emit_signal(self, signal: Signal, ctx: MarketContext) -> None:
        signal.ai_commentary = await self.analyst.comment_on_signal(signal, ctx)
        text = format_signal(signal)
        await self.notifier.send(text)
        logger.bind(event="signal").info("signal: {}", signal.to_log())
        self.last_signals.appendleft(signal)
        if self.paper:
            # Skip se sizing zerou (saldo esgotado / cálculo inválido) —
            # melhor não abrir posição do que abrir uma com $0 ou fake.
            if signal.position_size <= 0:
                logger.warning(
                    "[PAPER] Skip {} {}: position_size=0 (saldo {:.2f} USDT pode ter esgotado)",
                    signal.symbol, signal.side.value, self.paper.balance,
                )
                return
            # Em modo margem-fixa: margin e leverage vêm direto do
            # settings; o paper trader rejeita se margem disponível
            # for insuficiente (todas as 10 vagas ocupadas, p.ex.).
            if settings.paper_margin_per_trade > 0:
                self.paper.open_trade(
                    signal,
                    size_usdt=signal.position_size,
                    margin=settings.paper_margin_per_trade,
                    leverage=settings.paper_leverage,
                )
            else:
                self.paper.open_trade(signal, size_usdt=signal.position_size)

    async def update_paper_marks(self) -> None:
        if not self.paper:
            return
        prices: dict[str, float] = {}
        for sym, ctx in self.contexts.items():
            for tf in ("5m", "15m", "1h", "4h"):
                snap = ctx.snapshots.get(tf)
                if snap:
                    prices[sym] = snap.close
                    break
        
        # Capture closed trades before marking to market
        closed_trades = []
        if self.paper.open_trades:
            # Check which trades will be closed
            for trade_id, trade in list(self.paper.open_trades.items()):
                price = prices.get(trade.symbol)
                if price is not None:
                    should_close = False
                    if trade.side == Side.LONG:
                        if price <= trade.stop or price >= trade.take_profit:
                            should_close = True
                    else:
                        if price >= trade.stop or price <= trade.take_profit:
                            should_close = True
                    if should_close:
                        closed_trades.append(trade)
        
        self.paper.mark_to_market(prices)
        
        # Record closed trades for learning
        for trade in closed_trades:
            self.learner.record_trade(trade)
            self._trades_since_last_analysis += 1
            
            # Check if we should analyze and adapt
            if self._trades_since_last_analysis >= self._learning_analysis_interval:
                self._run_learning_analysis()
                self._trades_since_last_analysis = 0
    
    def _run_learning_analysis(self) -> None:
        """Run adaptive learning analysis on recent trades."""
        recommendations = self.learner.analyze_and_adapt(last_n_trades=self._learning_analysis_interval)
        
        if recommendations:
            logger.info("=== Adaptive Learning: {} recommendations ===", len(recommendations))
            for rec in recommendations:
                logger.info(
                    "[{}] {}: {} → {} (conf: {:.0%}) - {}",
                    rec.strategy, rec.change_type, rec.current_value, 
                    rec.recommended_value, rec.confidence, rec.reason
                )
            
            # Apply recommendations automatically
            self._apply_learning_recommendations(recommendations)
    
    def _apply_learning_recommendations(self, recommendations: list) -> None:
        """Apply learning recommendations to adjust strategy behavior."""
        # Recommendations are already applied in the learner's adaptive_params
        # Here we could add additional logic like notifying via Telegram
        if self.notifier.is_configured:
            summary = self.learner.get_summary()
            msg = f"🧠 Aprendizado Adaptativo\n\n"
            msg += f"Trades analisados: {summary['total_trades_analyzed']}\n"
            for strat, metrics in summary['strategies'].items():
                msg += f"\n{strat}:\n"
                msg += f"  Winrate: {metrics['winrate']:.1f}%\n"
                msg += f"  PnL: ${metrics['total_pnl']:+.2f}\n"
                msg += f"  Profit Factor: {metrics['profit_factor']:.2f}"
            # Send asynchronously (fire and forget)
            asyncio.create_task(self.notifier.send(msg))

    async def scan_once(self) -> list[Signal]:
        results = await asyncio.gather(*(self.scan_symbol(s) for s in self.symbols))
        all_signals: list[Signal] = []
        for ctx, signals in results:
            if ctx is None:
                continue
            for signal in signals:
                await self.emit_signal(signal, ctx)
                all_signals.append(signal)
        await self.update_paper_marks()
        self.last_scan_at = datetime.now(timezone.utc)
        self.scan_count += 1
        return all_signals

    async def run_forever(self) -> None:
        """Loop principal do scanner.

        Resiliente: nem `ensure_markets` nem `scan_once` derrubam o engine.
        Isso é importante porque o dashboard roda na mesma asyncio loop —
        se a engine crashar, o uvicorn cai junto (cancelado pelo
        FIRST_EXCEPTION em `main.py`). Aqui só paramos quando alguém
        chamar `stop()` explicitamente.
        """
        self._running = True
        logger.info(
            "Copilot online: {} symbols × {} TFs every {}s. AI={}, Telegram={}",
            len(self.symbols), len(self.timeframes), self.scan_interval,
            settings.ai_provider, "ON" if self.notifier.is_configured else "OFF",
        )

        # Tenta carregar markets uma vez aqui só pra logar o status cedo;
        # se falhar, segue — `scan_once` vai tentar de novo no ciclo.
        # Geo-block (raise dentro do helper) é re-levantado pra ficar
        # visível, mas mesmo nesse caso a gente não derruba: capturamos.
        try:
            await self.exchange.ensure_markets()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Falha ao carregar markets no boot ({}). "
                "Engine continua tentando a cada {}s — dashboard segue online.",
                type(exc).__name__, self.scan_interval,
            )

        while self._running:
            try:
                signals = await self.scan_once()
                logger.info("Scan #{} done. New signals: {}", self.scan_count, len(signals))
            except Exception as exc:  # noqa: BLE001
                logger.exception("scan_once failed: {}", exc)
            await asyncio.sleep(self.scan_interval)

    async def market_brief_now(self) -> str:
        if not self.contexts:
            return ""
        brief = await self.analyst.market_brief(self.contexts)
        text = format_market_brief(brief, self.contexts)
        await self.notifier.send(text)
        return text
