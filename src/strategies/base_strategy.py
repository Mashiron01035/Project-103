"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : base_strategy
    Project     : Project-103
    Description : Base strategy abstract class. Defines the interface and common functionality for all trading strategies.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from src.indicators.base_indicator import Candle, IndicatorResult

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    """Order side"""

    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Signal:
    """
    Trading signal produced by a strategy

    This is the main output of a strategy — a signal to enter/exit a position
    """

    # Identification
    id: str  # Unique signal ID (UUID)
    timestamp: int  # Generation time (Unix timestamp in milliseconds)
    symbol: str  # Trading pair (e.g. "SOLUSDT")
    strategy_name: str  # Strategy name
    timeframe: str  # Timeframe (e.g. "15m")

    # Core parameters
    side: str  # "BUY" or "SELL"
    confidence: float  # Signal confidence (0.0 - 1.0)

    # Price levels
    entry_price: float  # Proposed entry price
    stop_loss: float  # Stop-loss level
    take_profit: List[float]  # Take-profit levels (may be several)

    # Additional parameters
    risk_reward_ratio: float  # Risk/reward ratio
    position_size_percent: float = 1.0  # Recommended position size, % of equity

    # Indicators at signal time
    indicators_snapshot: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert the signal to a dict"""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "strategy": self.strategy_name,
            "timeframe": self.timeframe,
            "side": self.side,
            "confidence": self.confidence,
            "entry": self.entry_price,
            "sl": self.stop_loss,
            "tp": self.take_profit,
            "rr": self.risk_reward_ratio,
            "position_size_percent": self.position_size_percent,
            "indicators": self.indicators_snapshot,
            "metadata": self.metadata,
        }


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies

    Every strategy must inherit from this class
    and implement the abstract methods.

    Usage example:
        class MyStrategy(BaseStrategy):
            def __init__(self):
                super().__init__("my_strategy")

            def get_indicators_config(self) -> Dict[str, Dict[str, Any]]:
                return {
                    "rsi": {"period": 14},
                    "ema": {"period": 20}
                }

            async def analyze(self, symbol: str, candles: List[Candle],
                            indicators: Dict[str, IndicatorResult]) -> Optional[Signal]:
                # Analysis logic
                if should_buy():
                    return self._create_buy_signal(...)
                return None
    """

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the strategy

        Args:
            name: Unique strategy name
            config: Strategy configuration
        """
        self.name = name
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.timeframes = self.config.get("timeframes", ["15m"])
        self.min_confidence = self.config.get("min_confidence", 0.6)
        self.max_positions = self.config.get("max_positions", 1)

        # Rejection accounting — see reject() below
        self._rejections: Dict[str, int] = defaultdict(int)
        self._rejections_by_day: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._analyses = 0
        self._candle_ts_ms: Optional[int] = None
        self._last_report_at = 0.0

        # Statistics
        self._signals_generated = 0
        self._signals_executed = 0

        logger.info(f"Initialized strategy: {self.name}")

    # --------------------------------------------------------------- rejections
    #
    # Why not logger.debug on every rejection, as before.
    #
    # One line per rejected candle made up 86% of backtest log volume — 380 MB on
    # a three-year run, 350 thousand lines repeating the same thing with
    # different numbers. Unreadable, and the answer actually needed from the logs
    # ("which filter cuts, and how much") had to be extracted by counting with
    # grep.
    #
    # Also, the f-string in logger.debug(f"...") is ALWAYS evaluated, even with
    # DEBUG disabled. So details are passed as fields and formatted only for the
    # few samples that are actually printed.
    #
    # The per-day breakdown uses the CANDLE time, not the log write time. These
    # differ: a backtest replays three years in minutes and all records have
    # almost the same timestamp — no per-day analysis is possible from it.

    REJECT_SAMPLES = 3  # how many samples of each reason to show

    def note_candle(self, timestamp_ms: int) -> None:
        """Remember the current candle time so reject() need not take it as an argument.

        Called once at the start of analyze(); without it the per-day breakdown would
        stay empty for rejections counted deep inside the logic.
        """
        self._candle_ts_ms = timestamp_ms

    def reject(self, reason: str, timestamp_ms: Optional[int] = None, **fields: Any) -> None:
        """Count a rejection. Only the first few samples of each reason are logged."""
        self._rejections[reason] += 1
        count = self._rejections[reason]
        timestamp_ms = timestamp_ms or self._candle_ts_ms
        if timestamp_ms:
            day = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            self._rejections_by_day[day][reason] += 1
        if count <= self.REJECT_SAMPLES and logger.isEnabledFor(logging.DEBUG):
            detail = " ".join(f"{k}={v}" for k, v in fields.items())
            logger.debug(
                f"[{self.name}] rejected: {reason}"
                f"{' (' + detail + ')' if detail else ''}"
                f" [sample {count}/{self.REJECT_SAMPLES}]"
            )

    @property
    def rejections(self) -> Dict[str, int]:
        return dict(self._rejections)

    def rejection_report(self, top_days: int = 0) -> str:
        """Rejection summary — the reason logs are read at all."""
        if not self._rejections:
            return f"[{self.name}] no rejections"
        total = sum(self._rejections.values())
        rows = sorted(self._rejections.items(), key=lambda kv: -kv[1])
        parts = [
            f"[{self.name}] rejections {total:,}"
            + (f" out of {self._analyses:,} checks" if self._analyses else "")
        ]
        parts.append("  " + ", ".join(f"{r}={n:,}" for r, n in rows))
        if top_days and self._rejections_by_day:
            worst = sorted(self._rejections_by_day.items(), key=lambda kv: -sum(kv[1].values()))[
                :top_days
            ]
            for day, counts in worst:
                top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
                parts.append(f"  {day}: " + ", ".join(f"{r}={n}" for r, n in top))
        return "\n".join(parts)

    def maybe_log_rejections(self, interval_sec: float = 900.0) -> None:
        """Periodic report for live trading.

        Throttled by wall-clock time: in a backtest that means "a couple of times per
        run", which is what we want — the engine's final report gives the full
        picture there. Each strategy used to have its own such method with its own
        list of reasons, and after reasons were renamed those lists printed zeros.
        """
        import time as _time

        now = _time.time()
        if now - self._last_report_at < interval_sec:
            return
        self._last_report_at = now
        self.log_rejection_report()

    def log_rejection_report(self, top_days: int = 0) -> None:
        if self._rejections:
            logger.info(self.rejection_report(top_days))

    def reset_rejections(self) -> None:
        self._rejections.clear()
        self._rejections_by_day.clear()
        self._analyses = 0
        self._candle_ts_ms: Optional[int] = None
        self._last_report_at = 0.0

    @abstractmethod
    def get_indicators_config(self) -> Dict[str, Dict[str, Any]]:
        """
        Return the indicator configuration for the strategy

        Returns:
            Dict with the indicator configuration:
            {
                "rsi": {"period": 14},
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "ema": {"period": 20}
            }

        Example:
            >>> def get_indicators_config(self):
            ...     return {
            ...         "rsi": {"period": 14},
            ...         "ema": {"period": 20},
            ...         "atr": {"period": 14}
            ...     }
        """
        pass

    @abstractmethod
    async def analyze(
        self, symbol: str, candles: List[Candle], indicators: Dict[str, IndicatorResult]
    ) -> Optional[Signal]:
        """
        Analyze market data and generate a signal

        Args:
            symbol: Trading pair (e.g. "SOLUSDT")
            candles: List of candles to analyze
            indicators: Computed indicators

        Returns:
            Signal if an entry point is found, else None

        Example:
            >>> async def analyze(self, symbol, candles, indicators):
            ...     rsi = indicators["rsi"].values["value"]
            ...
            ...     if rsi < 30:  # Oversold
            ...         return self._create_buy_signal(
            ...             symbol=symbol,
            ...             candles=candles,
            ...             indicators=indicators,
            ...             confidence=0.75
            ...         )
            ...     return None
        """
        pass

    def validate_signal(self, signal: Signal) -> bool:
        """
        Validate a generated signal

        Args:
            signal: Signal to validate

        Returns:
            True if the signal is valid
        """
        # Check the basic parameters
        if signal.confidence < self.min_confidence:
            logger.debug(
                f"Signal confidence {signal.confidence} below minimum " f"{self.min_confidence}"
            )
            return False

        if signal.confidence < 0 or signal.confidence > 1:
            logger.error(f"Invalid confidence value: {signal.confidence}")
            return False

        if signal.entry_price <= 0:
            logger.error(f"Invalid entry price: {signal.entry_price}")
            return False

        if signal.stop_loss <= 0:
            logger.error(f"Invalid stop loss: {signal.stop_loss}")
            return False

        # TP validation: skip if strategy uses batch close (empty TP is intentional)
        # Fan strategies use "fan_batch_id", other grid strategies may use "batch_id"
        has_batch_close = False
        if signal.metadata:
            has_batch_close = signal.metadata.get("fan_batch_id") or signal.metadata.get("batch_id")
        if not has_batch_close:
            if not signal.take_profit or any(tp <= 0 for tp in signal.take_profit):
                logger.error(f"Invalid take profit levels: {signal.take_profit}")
                return False

        # Check stop-loss and take-profit logic
        if signal.side == OrderSide.BUY.value:
            if signal.stop_loss >= signal.entry_price:
                logger.error("BUY signal: stop loss must be below entry price")
                return False
            if signal.take_profit and any(tp <= signal.entry_price for tp in signal.take_profit):
                logger.error("BUY signal: take profit must be above entry price")
                return False

        elif signal.side == OrderSide.SELL.value:
            if signal.stop_loss <= signal.entry_price:
                logger.error("SELL signal: stop loss must be above entry price")
                return False
            if signal.take_profit and any(tp >= signal.entry_price for tp in signal.take_profit):
                logger.error("SELL signal: take profit must be below entry price")
                return False

        # Check the risk:reward ratio (skip for batch close strategies — they manage R:R at batch level)
        if not has_batch_close and signal.risk_reward_ratio < 0.1:
            logger.warning(f"Low risk:reward ratio: {signal.risk_reward_ratio}")
            return False

        return True

    def notify_signal_rejected(
        self, symbol: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Called when a signal generated by this strategy was rejected downstream.

        Override in subclasses that track level/batch state to clean up
        optimistically added entries (e.g. active_levels in grid strategies).
        """
        pass

    def reset_warmup_state(self) -> None:
        """Called once after warmup replay to discard state built from historical candles.

        Override in stateful strategies (batches, session ranges, daily counters).
        Warmup-phase signals are ignored by the caller, but internal state set
        inside analyze() persists and must be cleared before live trading starts.
        """
        pass

    @staticmethod
    def _get_candle_index(candles: List[Candle]) -> int:
        """Absolute, monotonically-increasing candle index derived from timestamps.

        Why: backtest passes a growing slice of candles, so `len(candles)` worked
        as a per-candle counter. Live feeds a fixed-size rolling window (limit=1000),
        so `len(candles)` stays constant and any cooldown tracker that stores
        `len(candles)` as "last signal index" permanently blocks future signals.

        Timestamp-based index increases by +1 per closed candle in both modes
        (since tf_ms is inferred from the min diff between candles), preserving
        the exact semantics of `candle_index - last_signal < cooldown_candles`.
        Works across weekend gaps — index jumps by the gap size, which causes
        stale cooldowns to expire naturally instead of lingering into a new session.
        """
        if not candles:
            return 0
        ts_last = candles[-1].timestamp
        if len(candles) < 2:
            return int(ts_last // 60_000)  # assume 1m as safe fallback

        # Scan up to last 20 diffs and take the smallest positive diff — that's
        # the regular timeframe step; larger values are weekend/session gaps.
        start = max(0, len(candles) - 21)
        tf_ms = 0
        for i in range(start, len(candles) - 1):
            d = candles[i + 1].timestamp - candles[i].timestamp
            if d > 0 and (tf_ms == 0 or d < tf_ms):
                tf_ms = d
        if tf_ms <= 0:
            tf_ms = 60_000
        return int(ts_last // tf_ms)

    def _create_buy_signal(
        self,
        symbol: str,
        candles: List[Candle],
        indicators: Dict[str, IndicatorResult],
        confidence: float,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Signal:
        """
        Create a buy signal

        Args:
            symbol: Trading pair
            candles: Candles
            indicators: Indicators
            confidence: Signal confidence
            entry_price: Entry price (if None, the current price is used)
            stop_loss: Stop-loss (if None, computed from ATR)
            take_profit: Take-profit (if None, computed from R:R)
            metadata: Additional information

        Returns:
            Signal object
        """
        import uuid

        current_price = candles[-1].close

        # If no entry price is given, use the current one
        if entry_price is None:
            entry_price = current_price

        # Compute SL/TP from ATR if not given
        # Check if we explicitly want no SL (hedge mode)
        explicit_no_sl = metadata and metadata.get("no_sl", False)

        if take_profit is None or (stop_loss is None and not explicit_no_sl):
            sl, tp = self._calculate_sl_tp(
                entry_price=entry_price,
                side=OrderSide.BUY.value,
                indicators=indicators,
                candles=candles,
            )
            if stop_loss is None and not explicit_no_sl:
                stop_loss = sl
            if take_profit is None:
                take_profit = tp

        # Compute R:R (skip if no SL for hedge mode)
        if stop_loss is not None:
            risk = entry_price - stop_loss
            reward = take_profit[0] - entry_price if take_profit else 0
            rr = reward / risk if risk > 0 else 0
        else:
            rr = 0  # No R:R for hedge positions without SL

        # Snapshot of the indicators
        indicators_snapshot = {name: result.values for name, result in indicators.items()}

        # Prepare metadata with account_balance for per-strategy tracking
        final_metadata = metadata or {}
        if "account_balance" not in final_metadata:
            final_metadata["account_balance"] = self.config.get("account_balance", 0.0)

        return Signal(
            id=str(uuid.uuid4()),
            timestamp=candles[-1].timestamp,
            symbol=symbol,
            strategy_name=self.name,
            timeframe=self.timeframes[0] if self.timeframes else "15m",
            side=OrderSide.BUY.value,
            confidence=confidence,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=rr,
            indicators_snapshot=indicators_snapshot,
            metadata=final_metadata,
        )

    def _create_sell_signal(
        self,
        symbol: str,
        candles: List[Candle],
        indicators: Dict[str, IndicatorResult],
        confidence: float,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Signal:
        """
        Create a sell signal

        Same as _create_buy_signal, but for SHORT positions
        """
        import uuid

        current_price = candles[-1].close

        if entry_price is None:
            entry_price = current_price

        # Compute SL/TP
        # Check if we explicitly want no SL (hedge mode)
        explicit_no_sl = metadata and metadata.get("no_sl", False)

        if take_profit is None or (stop_loss is None and not explicit_no_sl):
            sl, tp = self._calculate_sl_tp(
                entry_price=entry_price,
                side=OrderSide.SELL.value,
                indicators=indicators,
                candles=candles,
            )
            if stop_loss is None and not explicit_no_sl:
                stop_loss = sl
            if take_profit is None:
                take_profit = tp

        # Compute R:R (skip if no SL for hedge mode)
        if stop_loss is not None:
            risk = stop_loss - entry_price
            reward = entry_price - take_profit[0] if take_profit else 0
            rr = reward / risk if risk > 0 else 0
        else:
            rr = 0  # No R:R for hedge positions without SL

        # Snapshot of the indicators
        indicators_snapshot = {name: result.values for name, result in indicators.items()}

        # Prepare metadata with account_balance for per-strategy tracking
        final_metadata = metadata or {}
        if "account_balance" not in final_metadata:
            final_metadata["account_balance"] = self.config.get("account_balance", 0.0)

        return Signal(
            id=str(uuid.uuid4()),
            timestamp=candles[-1].timestamp,
            symbol=symbol,
            strategy_name=self.name,
            timeframe=self.timeframes[0] if self.timeframes else "15m",
            side=OrderSide.SELL.value,
            confidence=confidence,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=rr,
            indicators_snapshot=indicators_snapshot,
            metadata=final_metadata,
        )

    def _calculate_sl_tp(
        self,
        entry_price: float,
        side: str,
        indicators: Dict[str, IndicatorResult],
        candles: List[Candle],
    ) -> tuple[float, List[float]]:
        """
        Compute stop-loss and take-profit from ATR

        Args:
            entry_price: Entry price
            side: Trade side (BUY/SELL)
            indicators: Computed indicators
            candles: Candles

        Returns:
            (stop_loss, [take_profit_levels])
        """
        # Get ATR if available (supported formats: "atr{14}", "atr")
        atr_value = None
        for key, result in indicators.items():
            if key.startswith("atr"):
                if hasattr(result, "values"):
                    atr_value = result.values.get("value")
                    if atr_value is not None:
                        break

        # Without ATR, use a percentage of the price
        if atr_value is None or atr_value == 0:
            atr_value = entry_price * 0.015  # 1.5% of the price by default

        # Multiplier from the config
        atr_multiplier = self.config.get("atr_multiplier", 2.0)

        # Minimum and maximum SL in percent
        min_sl_percent = self.config.get("min_sl_percent", 0.5)  # 0.5%
        max_sl_percent = self.config.get("max_sl_percent", 3.0)  # 3%

        # Risk:Reward from the config
        target_rr = self.config.get("target_rr", 1.5)

        # SL distance from ATR
        sl_distance = atr_value * atr_multiplier

        # Apply the minimum and maximum limits
        min_sl_distance = entry_price * (min_sl_percent / 100)
        max_sl_distance = entry_price * (max_sl_percent / 100)

        sl_distance = max(sl_distance, min_sl_distance)  # Not below the minimum
        sl_distance = min(sl_distance, max_sl_distance)  # Not above the maximum

        if side == OrderSide.BUY.value:
            # For BUY: SL below, TP above
            stop_loss = entry_price - sl_distance
            risk = sl_distance

            # Several TP levels
            take_profit = [
                entry_price + (risk * target_rr),  # Main TP
                entry_price + (risk * target_rr * 1.5),  # Additional TP
            ]
        else:
            # For SELL: SL above, TP below
            stop_loss = entry_price + sl_distance
            risk = sl_distance

            take_profit = [
                entry_price - (risk * target_rr),
                entry_price - (risk * target_rr * 1.5),
            ]

        return stop_loss, take_profit

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get strategy statistics

        Returns:
            Dict with statistics
        """
        return {
            "name": self.name,
            "enabled": self.enabled,
            "signals_generated": self._signals_generated,
            "signals_executed": self._signals_executed,
            "execution_rate": (
                self._signals_executed / self._signals_generated
                if self._signals_generated > 0
                else 0
            ),
        }

    def reset_statistics(self) -> None:
        """Reset statistics"""
        self._signals_generated = 0
        self._signals_executed = 0

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', enabled={self.enabled})"
