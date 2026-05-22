"""Adaptive learning system. Analyzes past trades and adjusts strategy parameters.

Every N trades, this module:
1. Analyzes win/loss patterns per strategy
2. Identifies losing conditions (timeframes, R:R, score ranges)
3. Adjusts minimum scores, R:R ratios, and strategy weights
4. Persists learnings to JSON for continuity across restarts
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.logger import logger
from core.settings import settings
from core.types import PaperTrade, Side


@dataclass
class TradeAnalysis:
    """Single trade analysis result."""
    trade_id: str
    symbol: str
    strategy: str
    side: Side
    pnl: float
    pnl_pct: float
    risk_reward: float
    score: int
    setup_timeframe: str
    entry_time: datetime
    exit_time: datetime | None
    reason_close: str | None
    was_winner: bool
    features: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyMetrics:
    """Aggregated metrics per strategy."""
    strategy: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    winrate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_rr: float = 0.0
    avg_score: float = 0.0
    profit_factor: float = 0.0
    
    # Performance by condition
    by_timeframe: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_score_range: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_rr_range: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class LearningRecommendation:
    """Recommendation from the learning system."""
    strategy: str
    change_type: str  # 'min_score', 'rr_ratio', 'weight', 'disable_tf'
    current_value: Any
    recommended_value: Any
    reason: str
    confidence: float  # 0.0-1.0


class AdaptiveLearner:
    """Main learning engine. Analyzes trades and recommends parameter changes."""
    
    def __init__(self, checkpoint_path: Path | None = None) -> None:
        self.checkpoint_path = checkpoint_path or settings.data_dir / "learner_state.json"
        self.trade_history: list[TradeAnalysis] = []
        self.strategy_metrics: dict[str, StrategyMetrics] = {}
        self.recommendations: list[LearningRecommendation] = []
        self.adaptive_params: dict[str, Any] = {
            'strategy_min_scores': {},  # strategy -> adjusted min score
            'strategy_weights': {},     # strategy -> weight multiplier
            'disabled_timeframes': {},  # strategy -> list of bad timeframes
            'optimal_rr_ranges': {},    # strategy -> (min_rr, max_rr)
        }
        self._load_checkpoint()
    
    def _load_checkpoint(self) -> None:
        """Load previous learning state if exists."""
        if not self.checkpoint_path.exists():
            return
        try:
            with self.checkpoint_path.open('r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.adaptive_params = data.get('adaptive_params', self.adaptive_params)
            logger.info("Loaded learner checkpoint: {} trades analyzed previously", 
                       len(data.get('trade_history', [])))
        except Exception as exc:
            logger.warning("Failed to load learner checkpoint: {}", exc)
    
    def save_checkpoint(self) -> None:
        """Persist learning state to disk."""
        try:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                'adaptive_params': self.adaptive_params,
                'trade_history': [
                    {
                        'trade_id': t.trade_id,
                        'symbol': t.symbol,
                        'strategy': t.strategy,
                        'side': t.side.value,
                        'pnl': t.pnl,
                        'pnl_pct': t.pnl_pct,
                        'risk_reward': t.risk_reward,
                        'score': t.score,
                        'setup_timeframe': t.setup_timeframe,
                        'entry_time': t.entry_time.isoformat(),
                        'exit_time': t.exit_time.isoformat() if t.exit_time else None,
                        'reason_close': t.reason_close,
                        'was_winner': t.was_winner,
                        'features': t.features,
                    }
                    for t in self.trade_history[-500:]  # Keep last 500
                ],
                'last_updated': datetime.now(timezone.utc).isoformat(),
            }
            with self.checkpoint_path.open('w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info("Saved learner checkpoint")
        except Exception as exc:
            logger.error("Failed to save learner checkpoint: {}", exc)
    
    def record_trade(self, trade: PaperTrade, signal_score: int | None = None, signal_reasons: list[str] | None = None) -> None:
        """Record a closed trade for analysis."""
        if trade.pnl is None or trade.exit_price is None:
            return
        
        pnl_pct = (trade.pnl / (trade.margin or trade.size)) * 100 if trade.margin else 0.0
        
        # Usa score do trade se disponível, senão usa o passado como parâmetro
        score = trade.signal_score if trade.signal_score else (signal_score or 0)
        
        analysis = TradeAnalysis(
            trade_id=trade.id,
            symbol=trade.symbol,
            strategy=self._extract_strategy(trade),
            side=trade.side,
            pnl=trade.pnl or 0.0,
            pnl_pct=pnl_pct,
            risk_reward=0.0,  # Would need to calculate from entry/stop/take
            score=score,
            setup_timeframe=trade.setup_timeframe or "15m",
            entry_time=trade.opened_at,
            exit_time=trade.closed_at,
            reason_close=trade.reason_close,
            was_winner=trade.pnl > 0,
            features={
                'close_reason': trade.reason_close,
                'fees': trade.fees_paid or 0.0,
            }
        )
        
        self.trade_history.append(analysis)
        self._update_strategy_metrics(analysis)
        
        logger.info("Recorded trade for learning: {} {} PnL={:+.2f}", 
                   analysis.strategy, analysis.trade_id, analysis.pnl)
    
    def _extract_strategy(self, trade: PaperTrade) -> str:
        """Extract strategy name from trade metadata."""
        return trade.strategy or "unknown"
    
    def _update_strategy_metrics(self, trade: TradeAnalysis) -> None:
        """Update aggregated metrics for a strategy."""
        if trade.strategy not in self.strategy_metrics:
            self.strategy_metrics[trade.strategy] = StrategyMetrics(strategy=trade.strategy)
        
        metrics = self.strategy_metrics[trade.strategy]
        metrics.total_trades += 1
        
        if trade.was_winner:
            metrics.wins += 1
            metrics.avg_win = ((metrics.avg_win * (metrics.wins - 1)) + trade.pnl) / metrics.wins
        else:
            metrics.losses += 1
            metrics.avg_loss = ((metrics.avg_loss * (metrics.losses - 1)) + abs(trade.pnl)) / metrics.losses
        
        metrics.total_pnl += trade.pnl
        metrics.total_trades = metrics.wins + metrics.losses
        metrics.winrate = (metrics.wins / metrics.total_trades * 100) if metrics.total_trades else 0.0
        metrics.avg_pnl = metrics.total_pnl / metrics.total_trades if metrics.total_trades else 0.0
        metrics.avg_score = ((metrics.avg_score * (metrics.total_trades - 1)) + trade.score) / metrics.total_trades
        
        # Profit factor = gross wins / gross losses
        if metrics.avg_loss > 0:
            metrics.profit_factor = abs(metrics.avg_win) / metrics.avg_loss
        
        # Update timeframe breakdown
        tf = trade.setup_timeframe
        if tf not in metrics.by_timeframe:
            metrics.by_timeframe[tf] = {'trades': 0, 'wins': 0, 'pnl': 0.0}
        metrics.by_timeframe[tf]['trades'] += 1
        if trade.was_winner:
            metrics.by_timeframe[tf]['wins'] += 1
        metrics.by_timeframe[tf]['pnl'] += trade.pnl
        
        # Update score range breakdown
        score_range = self._get_score_range(trade.score)
        if score_range not in metrics.by_score_range:
            metrics.by_score_range[score_range] = {'trades': 0, 'wins': 0, 'pnl': 0.0}
        metrics.by_score_range[score_range]['trades'] += 1
        if trade.was_winner:
            metrics.by_score_range[score_range]['wins'] += 1
        metrics.by_score_range[score_range]['pnl'] += trade.pnl
    
    def _get_score_range(self, score: int) -> str:
        """Categorize score into ranges."""
        if score < 60:
            return "low (<60)"
        elif score < 75:
            return "medium (60-74)"
        elif score < 85:
            return "high (75-84)"
        else:
            return "very_high (85+)"
    
    def analyze_and_adapt(self, last_n_trades: int = 10) -> list[LearningRecommendation]:
        """Analyze last N trades and generate adaptation recommendations."""
        if len(self.trade_history) < last_n_trades:
            logger.info("Not enough trades for analysis: {} < {}", 
                       len(self.trade_history), last_n_trades)
            return []
        
        recent_trades = self.trade_history[-last_n_trades:]
        self.recommendations.clear()
        
        # Group by strategy
        by_strategy: dict[str, list[TradeAnalysis]] = {}
        for trade in recent_trades:
            if trade.strategy not in by_strategy:
                by_strategy[trade.strategy] = []
            by_strategy[trade.strategy].append(trade)
        
        # Analyze each strategy
        for strategy, trades in by_strategy.items():
            self._analyze_strategy(strategy, trades)
        
        # Save checkpoint after analysis
        self.save_checkpoint()
        
        logger.info("Generated {} learning recommendations", len(self.recommendations))
        return self.recommendations
    
    def _analyze_strategy(self, strategy: str, trades: list[TradeAnalysis]) -> None:
        """Analyze a single strategy's recent performance."""
        if not trades:
            return
        
        total = len(trades)
        wins = sum(1 for t in trades if t.was_winner)
        winrate = wins / total * 100
        total_pnl = sum(t.pnl for t in trades)
        avg_score = sum(t.score for t in trades) / total
        
        logger.info("Analyzing {}: {} trades, {:.1f}% winrate, PnL={:+.2f}, avg_score={:.1f}",
                   strategy, total, winrate, total_pnl, avg_score)
        
        # Rule 1: If winrate < 40% and losing money, increase min score
        if winrate < 40.0 and total_pnl < 0:
            current_min = settings.min_score_to_alert
            recommended_min = min(current_min + 10, 90)
            
            self.recommendations.append(LearningRecommendation(
                strategy=strategy,
                change_type='min_score',
                current_value=current_min,
                recommended_value=recommended_min,
                reason=f"Winrate {winrate:.1f}% < 40% com PnL negativo ({total_pnl:+.2f})",
                confidence=min(0.9, 0.5 + (total / 20) * 0.4),
            ))
            
            # Store adaptive param
            self.adaptive_params['strategy_min_scores'][strategy] = recommended_min
        
        # Rule 2: If winrate > 70%, can afford to lower score for more opportunities
        elif winrate > 70.0 and total_pnl > 0:
            current_min = self.adaptive_params['strategy_min_scores'].get(strategy, settings.min_score_to_alert)
            recommended_min = max(current_min - 5, 60)
            
            self.recommendations.append(LearningRecommendation(
                strategy=strategy,
                change_type='min_score',
                current_value=current_min,
                recommended_value=recommended_min,
                reason=f"Winrate {winrate:.1f}% > 70% - pode capturar mais oportunidades",
                confidence=min(0.8, 0.4 + (total / 30) * 0.4),
            ))
            
            self.adaptive_params['strategy_min_scores'][strategy] = recommended_min
        
        # Rule 3: Identify bad timeframes
        metrics = self.strategy_metrics.get(strategy)
        if metrics:
            for tf, tf_data in metrics.by_timeframe.items():
                if tf_data['trades'] >= 3:
                    tf_winrate = tf_data['wins'] / tf_data['trades'] * 100
                    if tf_winrate < 30.0 and tf_data['pnl'] < 0:
                        disabled_tfs = self.adaptive_params['disabled_timeframes'].get(strategy, [])
                        if tf not in disabled_tfs:
                            disabled_tfs.append(tf)
                            self.adaptive_params['disabled_timeframes'][strategy] = disabled_tfs
                            
                            self.recommendations.append(LearningRecommendation(
                                strategy=strategy,
                                change_type='disable_tf',
                                current_value=None,
                                recommended_value=tf,
                                reason=f"Timeframe {tf}: {tf_winrate:.1f}% winrate, PnL={tf_data['pnl']:+.2f}",
                                confidence=min(0.85, 0.5 + (tf_data['trades'] / 10) * 0.35),
                            ))
        
        # Rule 4: Adjust strategy weight based on profit factor
        if metrics and metrics.profit_factor > 0:
            current_weight = self.adaptive_params['strategy_weights'].get(strategy, 1.0)
            
            if metrics.profit_factor > 2.0:
                # High performer - increase weight
                new_weight = min(current_weight + 0.2, 2.0)
                self.recommendations.append(LearningRecommendation(
                    strategy=strategy,
                    change_type='weight',
                    current_value=current_weight,
                    recommended_value=new_weight,
                    reason=f"Profit factor {metrics.profit_factor:.2f} > 2.0 - aumentar peso",
                    confidence=min(0.75, 0.4 + (metrics.total_trades / 50) * 0.35),
                ))
                self.adaptive_params['strategy_weights'][strategy] = new_weight
                
            elif metrics.profit_factor < 0.7:
                # Poor performer - decrease weight
                new_weight = max(current_weight - 0.3, 0.3)
                self.recommendations.append(LearningRecommendation(
                    strategy=strategy,
                    change_type='weight',
                    current_value=current_weight,
                    recommended_value=new_weight,
                    reason=f"Profit factor {metrics.profit_factor:.2f} < 0.7 - reduzir peso",
                    confidence=min(0.8, 0.5 + (metrics.total_trades / 30) * 0.3),
                ))
                self.adaptive_params['strategy_weights'][strategy] = new_weight
    
    def get_adjusted_min_score(self, strategy: str) -> int:
        """Get the adapted minimum score for a strategy."""
        return int(self.adaptive_params['strategy_min_scores'].get(
            strategy, settings.min_score_to_alert
        ))
    
    def is_timeframe_disabled(self, strategy: str, timeframe: str) -> bool:
        """Check if a timeframe is disabled for a strategy."""
        disabled = self.adaptive_params['disabled_timeframes'].get(strategy, [])
        return timeframe in disabled
    
    def get_strategy_weight(self, strategy: str) -> float:
        """Get the weight multiplier for a strategy."""
        return self.adaptive_params['strategy_weights'].get(strategy, 1.0)
    
    def get_summary(self) -> dict[str, Any]:
        """Get a summary of learning state."""
        return {
            'total_trades_analyzed': len(self.trade_history),
            'strategies': {
                name: {
                    'trades': m.total_trades,
                    'winrate': m.winrate,
                    'total_pnl': m.total_pnl,
                    'profit_factor': m.profit_factor,
                    'avg_score': m.avg_score,
                }
                for name, m in self.strategy_metrics.items()
            },
            'adaptive_params': self.adaptive_params,
            'recommendations_count': len(self.recommendations),
        }


# Global instance
_learner: AdaptiveLearner | None = None


def get_learner() -> AdaptiveLearner:
    """Get or create the global learner instance."""
    global _learner
    if _learner is None:
        _learner = AdaptiveLearner()
    return _learner
