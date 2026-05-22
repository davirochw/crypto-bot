"""In-memory paper trading. Persists state to a JSONL file for audit."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.logger import logger
from core.settings import settings
from core.types import PaperTrade, Side, Signal


class PaperTrader:
    def __init__(self, balance: float | None = None, fee_percent: float | None = None) -> None:
        self.balance = balance if balance is not None else settings.paper_initial_balance
        self.starting_balance = self.balance
        self.fee_percent = fee_percent if fee_percent is not None else settings.paper_fee_percent
        self.open_trades: dict[str, PaperTrade] = {}
        self.closed_trades: list[PaperTrade] = []
        self._journal_path: Path = settings.data_dir / "paper_trades.jsonl"

    @property
    def margin_in_use(self) -> float:
        """Soma das margens das posições abertas (USDT comprometido)."""
        return sum(t.margin for t in self.open_trades.values())

    @property
    def available_balance(self) -> float:
        """Saldo livre = equity total - margem comprometida em posições abertas."""
        return max(0.0, self.balance - self.margin_in_use)

    def open_trade(
        self,
        signal: Signal,
        size_usdt: float,
        margin: float | None = None,
        leverage: float | None = None,
    ) -> PaperTrade | None:
        """Abre posição simulada.

        - `size_usdt` é o NOTIONAL (USDT exposto ao mercado).
        - `margin` é o capital bloqueado da conta. Se None, deduz como
          `size / leverage`. Se a margem requerida > saldo disponível,
          pula a abertura (retorna None).
        - `leverage` default = 1 (spot-like).
        """
        lev = float(leverage) if leverage and leverage > 0 else 1.0
        mgn = float(margin) if margin and margin > 0 else size_usdt / lev

        if mgn > self.available_balance:
            logger.warning(
                "[PAPER] Skip {} {}: margem ${:.2f} > disponível ${:.2f} "
                "({} posições já abertas usam ${:.2f})",
                signal.symbol, signal.side.value, mgn, self.available_balance,
                len(self.open_trades), self.margin_in_use,
            )
            return None

        trade_id = uuid.uuid4().hex[:10]
        # Fee incide sobre o NOTIONAL (é assim que a Binance cobra).
        fee = size_usdt * (self.fee_percent / 100)
        self.balance -= fee
        trade = PaperTrade(
            id=trade_id,
            symbol=signal.symbol,
            side=signal.side,
            entry=signal.entry,
            stop=signal.stop,
            take_profit=signal.take_profit,
            size=size_usdt,
            margin=round(mgn, 4),
            leverage=round(lev, 2),
            opened_at=datetime.now(timezone.utc),
            fees_paid=fee,
            strategy=signal.strategy,
            signal_score=signal.score,
            setup_timeframe=signal.timeframe,
        )
        self.open_trades[trade_id] = trade
        self._journal({"event": "open", **trade.model_dump(mode="json")})
        logger.info(
            "[PAPER] OPEN {} {} {} notional=${:.2f} margin=${:.2f} lev={:.0f}x entry={:.6g}",
            trade.symbol, trade.side.value, trade_id,
            size_usdt, mgn, lev, trade.entry,
        )
        return trade

    def mark_to_market(self, prices: dict[str, float]) -> list[PaperTrade]:
        """Close trades whose stop or take-profit was crossed. Returns closed trades."""
        closed_now: list[PaperTrade] = []
        for trade_id, trade in list(self.open_trades.items()):
            price = prices.get(trade.symbol)
            if price is None:
                continue
            exit_price: float | None = None
            reason: str | None = None
            if trade.side == Side.LONG:
                if price <= trade.stop:
                    exit_price, reason = trade.stop, "stop"
                elif price >= trade.take_profit:
                    exit_price, reason = trade.take_profit, "take"
            else:
                if price >= trade.stop:
                    exit_price, reason = trade.stop, "stop"
                elif price <= trade.take_profit:
                    exit_price, reason = trade.take_profit, "take"

            if exit_price is None:
                continue

            pnl_pct = (exit_price - trade.entry) / trade.entry
            if trade.side == Side.SHORT:
                pnl_pct = -pnl_pct
            pnl = trade.size * pnl_pct
            # Cap de perda na margem (liquidação): em alavancagem, você
            # nunca perde mais que a margem alocada — a corretora liquida
            # antes. Como o stop é mais conservador que a liquidação na
            # prática, essa proteção raramente dispara, mas blinda o
            # caso de gap em cima do stop.
            if trade.margin > 0 and pnl < -trade.margin:
                pnl = -trade.margin
            exit_fee = trade.size * (self.fee_percent / 100)
            self.balance += pnl - exit_fee
            trade.exit_price = exit_price
            trade.closed_at = datetime.now(timezone.utc)
            trade.pnl = round(pnl - exit_fee, 4)
            trade.fees_paid = round(trade.fees_paid + exit_fee, 4)
            trade.reason_close = reason
            self.closed_trades.append(trade)
            del self.open_trades[trade_id]
            closed_now.append(trade)
            self._journal({"event": "close", **trade.model_dump(mode="json")})
            logger.info("[PAPER] CLOSE {} {} pnl={:.2f} ({})",
                        trade.symbol, trade.id, trade.pnl, reason)
        return closed_now

    def close_at_market(
        self,
        trade_id: str,
        current_price: float,
        reason: str,
    ) -> PaperTrade | None:
        """Fecha posição imediatamente ao preço de mercado.

        Usado pelo Position Manager quando o setup virou contra. PnL,
        taxas e devolução de margem seguem a mesma fórmula do
        `mark_to_market` — diferença é só que aqui não esperamos o
        stop/TP encostar.
        """
        trade = self.open_trades.get(trade_id)
        if trade is None:
            return None

        pnl_pct = (current_price - trade.entry) / trade.entry
        if trade.side == Side.SHORT:
            pnl_pct = -pnl_pct
        pnl = trade.size * pnl_pct
        if trade.margin > 0 and pnl < -trade.margin:
            pnl = -trade.margin  # mesma proteção de liquidação
        exit_fee = trade.size * (self.fee_percent / 100)
        self.balance += pnl - exit_fee
        trade.exit_price = current_price
        trade.closed_at = datetime.now(timezone.utc)
        trade.pnl = round(pnl - exit_fee, 4)
        trade.fees_paid = round(trade.fees_paid + exit_fee, 4)
        trade.reason_close = reason
        self.closed_trades.append(trade)
        del self.open_trades[trade_id]
        self._journal({"event": "early_close", **trade.model_dump(mode="json")})
        logger.info(
            "[PAPER] EARLY_CLOSE {} {} {} pnl={:+.2f} ({})",
            trade.symbol, trade.side.value, trade.id, trade.pnl, reason,
        )
        return trade

    def stats(self) -> dict[str, float | int]:
        # Separa trades por tipo de fechamento para análise mais precisa
        tp_wins = [t for t in self.closed_trades if (t.pnl or 0) > 0 and t.reason_close == "take"]
        sl_losses = [t for t in self.closed_trades if (t.pnl or 0) <= 0 and t.reason_close == "stop"]
        
        # Win rate tradicional (todos os trades fechados)
        wins = [t for t in self.closed_trades if (t.pnl or 0) > 0]
        losses = [t for t in self.closed_trades if (t.pnl or 0) <= 0]
        total = len(self.closed_trades)
        winrate = (len(wins) / total * 100) if total else 0.0
        
        # Win rate "real" (só conta trades que foram até TP ou SL)
        decisive_trades = [t for t in self.closed_trades if t.reason_close in ("take", "stop")]
        real_wins = [t for t in decisive_trades if (t.pnl or 0) > 0]
        real_winrate = (len(real_wins) / len(decisive_trades) * 100) if decisive_trades else 0.0
        
        # Fechamentos antecipados = qualquer coisa que NÃO seja TP ou SL
        early_closes = [t for t in self.closed_trades if t.reason_close not in ("take", "stop")]
        
        avg_win = sum(t.pnl for t in wins if t.pnl is not None) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl for t in losses if t.pnl is not None) / len(losses) if losses else 0.0
        
        # Impacto dos fechamentos antecipados
        early_pnl = sum(t.pnl for t in early_closes if t.pnl is not None)
        early_count = len(early_closes)
        
        return {
            "balance": round(self.balance, 2),
            "starting_balance": self.starting_balance,
            "available": round(self.available_balance, 2),
            "margin_in_use": round(self.margin_in_use, 2),
            "open": len(self.open_trades),
            "closed": total,
            "winrate_pct": round(winrate, 2),
            "real_winrate_pct": round(real_winrate, 2),  # Só TP/SL
            "decisive_trades": len(decisive_trades),
            "early_closes": early_count,
            "early_pnl": round(early_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "net_pnl": round(self.balance - self.starting_balance, 2),
        }

    def _journal(self, payload: dict) -> None:
        try:
            with self._journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, default=str) + "\n")
        except OSError as exc:
            logger.warning("Could not write paper-trade journal: {}", exc)
