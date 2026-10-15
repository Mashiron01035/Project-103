"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : strategy_manager
    Project     : Project-103
    Description : Strategy manager. Coordinates multiple trading strategies, handles signal generation and strategy lifecycle.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.indicators.base_indicator import Candle, IndicatorResult
from src.indicators.factory import get_indicator_factory
from src.strategies.base_strategy import BaseStrategy, Signal
from src.utils.log_tags import STRAT

logger = logging.getLogger(__name__)


@dataclass
class StrategyMetrics:
    """Strategy performance metrics"""

    strategy_name: str
    total_signals: int = 0
    executed_signals: int = 0
    winning_signals: int = 0
    losing_signals: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_confidence: float = 0.0
    last_signal_time: Optional[int] = None

    def update_win_rate(self) -> None:
        """Update the win rate"""
        total = self.winning_signals + self.losing_signals
        self.win_rate = self.winning_signals / total if total > 0 else 0.0


class StrategyManager:
    """
    Trading strategy manager

    Manages several strategies:
    - Strategy registration and activation
    - Signal generation
    - Statistics collection
    - Coordination with indicators
    """

    def __init__(self):
        """Initialize the strategy manager"""
        self._strategies: Dict[str, BaseStrategy] = {}
        self._active_strategies: List[str] = []
        self._metrics: Dict[str, StrategyMetrics] = {}
        self._indicator_factory = get_indicator_factory()
        # True while replay_warmup() iterates historical candles.
        # Flag is read by _run_strategy to suppress metrics updates so that
        # warmup "signals" do not pollute strategy statistics.
        self._is_warmup: bool = False

        logger.info("Strategy manager initialized")

    def register_strategy(self, strategy: BaseStrategy, auto_activate: bool = True) -> None:
        """
        Register a strategy with the manager

        Args:
            strategy: Strategy instance
            auto_activate: Activate the strategy automatically

        Raises:
            ValueError: If a strategy with this name already exists
        """
        if strategy.name in self._strategies:
            raise ValueError(f"Strategy '{strategy.name}' is already registered")

        self._strategies[strategy.name] = strategy
        self._metrics[strategy.name] = StrategyMetrics(strategy_name=strategy.name)

        if auto_activate and strategy.enabled:
            self.activate_strategy(strategy.name)

        logger.info(f"Registered strategy: {strategy.name}")

    def unregister_strategy(self, strategy_name: str) -> None:
        """
        Remove a strategy from the manager

        Args:
            strategy_name: Strategy name
        """
        if strategy_name in self._active_strategies:
            self.deactivate_strategy(strategy_name)

        if strategy_name in self._strategies:
            del self._strategies[strategy_name]

        if strategy_name in self._metrics:
            del self._metrics[strategy_name]

        logger.info(f"Unregistered strategy: {strategy_name}")

    def activate_strategy(self, strategy_name: str) -> None:
        """
        Activate a strategy

        Args:
            strategy_name: Strategy name

        Raises:
            ValueError: If the strategy is not found
        """
        if strategy_name not in self._strategies:
            raise ValueError(f"Strategy '{strategy_name}' not found")

        if strategy_name not in self._active_strategies:
            self._active_strategies.append(strategy_name)
            self._strategies[strategy_name].enabled = True
            logger.info(f"Activated strategy: {strategy_name}")

    def deactivate_strategy(self, strategy_name: str) -> None:
        """
        Deactivate a strategy

        Args:
            strategy_name: Strategy name
        """
        if strategy_name in self._active_strategies:
            self._active_strategies.remove(strategy_name)
            self._strategies[strategy_name].enabled = False
            logger.info(f"Deactivated strategy: {strategy_name}")

    def get_strategy(self, strategy_name: str) -> Optional[BaseStrategy]:
        """
        Get a strategy by name

        Args:
            strategy_name: Strategy name

        Returns:
            BaseStrategy or None
        """
        return self._strategies.get(strategy_name)

    def list_strategies(self, active_only: bool = False) -> List[str]:
        """
        Get the list of strategies

        Args:
            active_only: Active strategies only

        Returns:
            List of strategy names
        """
        if active_only:
            return self._active_strategies.copy()
        return list(self._strategies.keys())

    # Margin over the indicator period. For an EMA the seed contribution decays as
    # (1-alpha)^k: at 10 periods it is already 4e-5, at 20 — 2e-9. Take 20 and
    # don't skimp: the cost difference between 5,000 and 10,000 candles is 2 ms.
    _INDICATOR_TAIL_FACTOR = 20
    _INDICATOR_TAIL_MIN = 5000

    @classmethod
    def _indicator_tail(cls, candles, indicators_config):
        """How many trailing candles are enough for the requested indicators."""
        import re

        periods = []
        keys = (
            indicators_config
            if isinstance(indicators_config, (list, tuple))
            else list(indicators_config or [])
        )
        for key in keys:
            periods.extend(int(x) for x in re.findall(r"\d+", str(key)))
        need = max(periods, default=0) * cls._INDICATOR_TAIL_FACTOR
        need = max(need, cls._INDICATOR_TAIL_MIN)
        return candles[-need:] if len(candles) > need else candles

    async def generate_signals(
        self, symbol: str, candles: List[Candle], strategy_names: Optional[List[str]] = None
    ) -> List[Signal]:
        """
        Generate signals from all active strategies

        Args:
            symbol: Trading pair
            candles: List of candles
            strategy_names: List of strategy names (if None, all active ones are used)

        Returns:
            List of generated signals
        """
        # Decide which strategies to use
        if strategy_names is None:
            strategies_to_run = self._active_strategies
        else:
            strategies_to_run = [name for name in strategy_names if name in self._active_strategies]

        # A strategy only sees its own pairs (`pairs` in strategies.yaml; empty = any).
        # There used to be no filter: every active strategy ran on every symbol in
        # the loop, so a second pair (USDJPY+) would have fed yen to gold strategies
        # and gold to the yen one. The backtest does not use this path — --symbols sets it.
        strategies_to_run = [
            name
            for name in strategies_to_run
            if self._accepts_symbol(self._strategies[name], symbol)
        ]

        if not strategies_to_run:
            logger.debug("No active strategies to run")
            return []

        # Collect all required indicators
        # Both formats are supported: a list ["ema{9}", "rsi{14}"] and a dict {"rsi": {"period": 14}}
        all_indicators_list = []
        all_indicators_dict = {}

        for strategy_name in strategies_to_run:
            strategy = self._strategies[strategy_name]
            indicators_config = strategy.get_indicators_config()

            if isinstance(indicators_config, list):
                # New format: list of strings
                all_indicators_list.extend(indicators_config)
            elif isinstance(indicators_config, dict):
                # Old format: dict
                all_indicators_dict.update(indicators_config)

        # Merge: prefer list format, add dict items if any
        if all_indicators_list:
            # Use list format (deduplicate)
            all_indicators_config = list(set(all_indicators_list))
        else:
            all_indicators_config = all_indicators_dict

        # Compute indicators once for all strategies.
        #
        # Computed on the TAIL, not on all 60,000 candles. Strategies still need the
        # full list (direction_filter builds its own 4h EMA50), but factory indicators
        # only need a generous margin over their period: EMA480 on 5,000 candles
        # equals the value on 60,000 to the sixth decimal. Saves ~135 ms every
        # minute on a VPS, and that time goes straight into the entry price.
        indicators = self._indicator_factory.calculate_multiple(
            candles=self._indicator_tail(candles, all_indicators_config),
            indicators_config=all_indicators_config,
        )

        # Run the strategies in parallel
        tasks = []
        for strategy_name in strategies_to_run:
            strategy = self._strategies[strategy_name]
            task = self._run_strategy(
                strategy=strategy, symbol=symbol, candles=candles, indicators=indicators
            )
            tasks.append(task)

        # Wait for the results
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect valid signals.
        # asyncio.gather preserves order, so results[i] belongs to
        # strategies_to_run[i] — which lets us put the strategy's position limit
        # into the metadata. The live SignalLifecycleManager needs it to apply the
        # same cap as the backtest engine (see parity below).
        signals = []
        for strategy_name, result in zip(strategies_to_run, results):
            if isinstance(result, Exception):
                logger.error(f"Strategy error ({strategy_name}): {result}")
                continue

            if result is None:
                continue

            batch = result if isinstance(result, list) else [result]
            strategy = self._strategies.get(strategy_name)
            strat_max = getattr(strategy, "max_positions", None) if strategy else None
            for sig in batch:
                if sig is None:
                    continue
                if strat_max:
                    if sig.metadata is None:
                        sig.metadata = {}
                    sig.metadata.setdefault("max_positions", int(strat_max))
                signals.append(sig)

        if signals:
            logger.info(
                f"{STRAT.GEN} {len(signals)} signals from {len(strategies_to_run)} strategies"
            )
        else:
            logger.debug(f"{STRAT.EVAL} 0 signals from {len(strategies_to_run)} strategies")

        return signals

    async def replay_warmup(
        self,
        symbol: str,
        candles: List[Candle],
        min_indicator_warmup: int = 300,
    ) -> None:
        """Replay historical candles through strategies to rebuild internal state.

        Calls generate_signals() for each candle in a sliding window starting at
        min_indicator_warmup (indicators need that many bars to stabilise). The
        returned signals are **discarded** — the goal is only to let strategies
        accumulate per-symbol state (session ranges, batch tracking, daily
        counters) so that the first live candle doesn't land on a cold cache.

        Callers must invoke `strategy.reset_warmup_state()` on every stateful
        strategy afterwards to drop transient state (batches created from
        replayed signals are never closed via notify_trade_closed, so they'd
        stay "open" forever without an explicit reset).

        Args:
            symbol: Trading pair
            candles: Historical candles (oldest first, e.g. last ~2000 minutes)
            min_indicator_warmup: Start replaying from this index so indicators
                                   like EMA(100) / BB(300) are already stable.
        """
        if not candles or len(candles) <= min_indicator_warmup:
            logger.warning(
                f"replay_warmup({symbol}): not enough candles "
                f"({len(candles) if candles else 0} <= {min_indicator_warmup})"
            )
            return

        # Skip replay iteration if all active strategies implement reset_warmup_state
        # (they discard the built state right after, so iterating is wasted work).
        # Indicators are stateless and recompute from candles each analyze() call,
        # so the long candle list itself is what matters — not pre-running analyze().
        active_strategies = [
            self._strategies[name] for name in self._active_strategies if name in self._strategies
        ]
        all_resettable = active_strategies and all(
            hasattr(s, "reset_warmup_state") for s in active_strategies
        )
        if all_resettable:
            logger.info(
                f"replay_warmup({symbol}): skipping iteration "
                f"({len(active_strategies)} active strategies all implement "
                f"reset_warmup_state; state would be cleared anyway). "
                f"Indicators recompute from full candle list on first live call."
            )
            return

        self._is_warmup = True
        try:
            logger.info(
                f"replay_warmup({symbol}): replaying "
                f"{len(candles) - min_indicator_warmup} candles"
            )
            for i in range(min_indicator_warmup, len(candles)):
                window = candles[: i + 1]
                try:
                    _ = await self.generate_signals(symbol=symbol, candles=window)
                except Exception as e:
                    # One bad candle shouldn't abort the whole warmup
                    logger.debug(f"replay_warmup step {i} error: {e}")
            logger.info(f"replay_warmup({symbol}): complete")
        finally:
            self._is_warmup = False

    @staticmethod
    def _accepts_symbol(strategy: BaseStrategy, symbol: str) -> bool:
        pairs = (getattr(strategy, "config", None) or {}).get("pairs") or []
        return not pairs or symbol in pairs

    async def _run_strategy(
        self,
        strategy: BaseStrategy,
        symbol: str,
        candles: List[Candle],
        indicators: Dict[str, IndicatorResult],
    ) -> Optional[Signal]:
        """
        Run a single strategy

        Args:
            strategy: Strategy
            symbol: Symbol
            candles: Candles
            indicators: Indicators

        Returns:
            Signal or None
        """
        try:
            # Keep only the indicators this strategy needs
            strategy_indicators_config = strategy.get_indicators_config()

            # Support both list and dict formats
            if isinstance(strategy_indicators_config, list):
                indicator_keys = strategy_indicators_config
            else:
                indicator_keys = list(strategy_indicators_config.keys())

            strategy_indicators = {
                name: indicators[name] for name in indicator_keys if name in indicators
            }

            # Analysis
            signal = await strategy.analyze(
                symbol=symbol, candles=candles, indicators=strategy_indicators
            )

            # If a signal was created
            if signal is not None:
                # Grid strategies may return a list of signals
                signals = signal if isinstance(signal, list) else [signal]
                validated = []

                for s in signals:
                    if not strategy.validate_signal(s):
                        logger.debug(f"Signal from {strategy.name} failed validation")
                        # Release level so it's not permanently blocked
                        if hasattr(strategy, "notify_signal_rejected"):
                            metadata = getattr(s, "metadata", None)
                            strategy.notify_signal_rejected(s.symbol, metadata)
                        continue

                    # Update metrics (skip during warmup replay)
                    if not self._is_warmup:
                        metrics = self._metrics[strategy.name]
                        metrics.total_signals += 1
                        metrics.avg_confidence = (
                            metrics.avg_confidence * (metrics.total_signals - 1) + s.confidence
                        ) / metrics.total_signals
                        metrics.last_signal_time = s.timestamp

                        logger.info(
                            f"{STRAT.GEN} {strategy.name} {s.side} {s.symbol} "
                            f"@ {s.entry_price} conf={s.confidence:.2f}"
                        )
                    validated.append(s)

                if not validated:
                    return None
                return validated if len(validated) > 1 else validated[0]

        except Exception as e:
            logger.error(f"Error running strategy {strategy.name}: {e}", exc_info=True)
            return None

    def record_signal_execution(
        self, strategy_name: str, executed: bool, pnl: Optional[float] = None
    ) -> None:
        """
        Record the result of a signal execution

        Args:
            strategy_name: Strategy name
            executed: Whether the signal was executed
            pnl: Profit/loss (if known)
        """
        if strategy_name not in self._metrics:
            return

        metrics = self._metrics[strategy_name]

        if executed:
            metrics.executed_signals += 1

        if pnl is not None:
            metrics.total_pnl += pnl
            if pnl > 0:
                metrics.winning_signals += 1
            else:
                metrics.losing_signals += 1
            metrics.update_win_rate()

    def get_metrics(self, strategy_name: Optional[str] = None) -> Dict[str, StrategyMetrics]:
        """
        Get strategy metrics

        Args:
            strategy_name: Name of a specific strategy (if None, all strategies)

        Returns:
            Dict of metrics
        """
        if strategy_name:
            return {strategy_name: self._metrics.get(strategy_name)}
        return self._metrics.copy()

    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all strategies

        Returns:
            Dict with statistics
        """
        total_signals = sum(m.total_signals for m in self._metrics.values())
        total_executed = sum(m.executed_signals for m in self._metrics.values())
        total_wins = sum(m.winning_signals for m in self._metrics.values())
        total_losses = sum(m.losing_signals for m in self._metrics.values())
        total_pnl = sum(m.total_pnl for m in self._metrics.values())

        overall_win_rate = (
            total_wins / (total_wins + total_losses) if (total_wins + total_losses) > 0 else 0.0
        )

        return {
            "total_strategies": len(self._strategies),
            "active_strategies": len(self._active_strategies),
            "total_signals": total_signals,
            "executed_signals": total_executed,
            "execution_rate": (total_executed / total_signals if total_signals > 0 else 0.0),
            "total_wins": total_wins,
            "total_losses": total_losses,
            "overall_win_rate": overall_win_rate,
            "total_pnl": total_pnl,
            "strategies": {
                name: {
                    "enabled": self._strategies[name].enabled,
                    "total_signals": metrics.total_signals,
                    "win_rate": metrics.win_rate,
                    "total_pnl": metrics.total_pnl,
                }
                for name, metrics in self._metrics.items()
            },
        }

    def reset_metrics(self, strategy_name: Optional[str] = None) -> None:
        """
        Reset metrics

        Args:
            strategy_name: Strategy name (if None, all strategies)
        """
        if strategy_name:
            if strategy_name in self._metrics:
                self._metrics[strategy_name] = StrategyMetrics(strategy_name=strategy_name)
                self._strategies[strategy_name].reset_statistics()
        else:
            for name in self._metrics.keys():
                self._metrics[name] = StrategyMetrics(strategy_name=name)
                self._strategies[name].reset_statistics()

        logger.info("Strategy metrics reset")

    def __repr__(self) -> str:
        return (
            f"StrategyManager("
            f"strategies={len(self._strategies)}, "
            f"active={len(self._active_strategies)})"
        )
