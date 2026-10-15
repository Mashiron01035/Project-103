"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : take_profit_calculator.py
    Project     : Project-103
    Description : Take profit calculation with multiple target levels and partial exit strategies.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from src.models.signal import SignalType

logger = logging.getLogger(__name__)


class TakeProfitMethod(Enum):
    """Take profit calculation methods."""

    RISK_REWARD = "risk_reward"  # Based on R:R ratio
    ATR_BASED = "atr_based"  # Based on ATR multiples
    PERCENTAGE = "percentage"  # Fixed percentage targets
    FIBONACCI = "fibonacci"  # Fibonacci extension levels
    RESISTANCE = "resistance"  # Key resistance levels


@dataclass
class TakeProfitLevel:
    """Single take profit level."""

    price: float
    percent_to_close: float  # Percentage of position to close at this level
    risk_reward_ratio: float  # R:R at this level
    method: str  # Method used to calculate


class TakeProfitCalculator:
    """
    Calculates take profit levels using various methods.

    Supports multiple TP levels with partial position closing
    and dynamic adjustment based on market conditions.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize TakeProfitCalculator.

        Args:
            config: Calculator configuration
        """
        self.config = config or {}

        # Default parameters
        self.method = TakeProfitMethod(self.config.get("method", "risk_reward"))
        self.num_levels = self.config.get("num_levels", 2)
        self.default_rr_ratios = self.config.get("default_rr_ratios", [2.0, 3.0])
        self.partial_close_percents = self.config.get(
            "partial_close_percents", [50.0, 50.0]  # Close 50% at TP1, 50% at TP2
        )

        logger.info(
            f"TakeProfitCalculator initialized with method={self.method.value}, "
            f"levels={self.num_levels}"
        )

    def calculate(
        self,
        entry_price: float,
        stop_loss: float,
        signal_type: SignalType,
        market_data: Dict[str, Any],
        target_rr_ratios: Optional[List[float]] = None,
        resistance_levels: Optional[List[float]] = None,
    ) -> List[TakeProfitLevel]:
        """
        Calculate take profit levels.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            signal_type: LONG or SHORT
            market_data: Current market data
            target_rr_ratios: Target R:R ratios (optional)
            resistance_levels: Resistance levels (optional)

        Returns:
            List of TakeProfitLevel objects
        """
        if self.method == TakeProfitMethod.RISK_REWARD:
            return self._calculate_risk_reward(
                entry_price, stop_loss, signal_type, target_rr_ratios or self.default_rr_ratios
            )
        elif self.method == TakeProfitMethod.ATR_BASED:
            return self._calculate_atr_based(
                entry_price, stop_loss, signal_type, market_data.get("atr", 0)
            )
        elif self.method == TakeProfitMethod.PERCENTAGE:
            return self._calculate_percentage(entry_price, signal_type)
        elif self.method == TakeProfitMethod.FIBONACCI:
            return self._calculate_fibonacci(entry_price, stop_loss, signal_type)
        else:  # RESISTANCE
            return self._calculate_resistance(
                entry_price, stop_loss, signal_type, resistance_levels or []
            )

    def _calculate_risk_reward(
        self, entry_price: float, stop_loss: float, signal_type: SignalType, rr_ratios: List[float]
    ) -> List[TakeProfitLevel]:
        """
        Calculate TP levels based on risk/reward ratios.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            signal_type: Signal type
            rr_ratios: Target R:R ratios

        Returns:
            List of TP levels
        """
        # Calculate risk (distance to stop loss)
        if signal_type == SignalType.LONG:
            risk = entry_price - stop_loss
        else:
            risk = stop_loss - entry_price

        if risk <= 0:
            logger.error(f"Invalid risk: {risk}")
            return []

        levels = []
        close_percents = self._get_close_percents(len(rr_ratios))

        for i, rr in enumerate(rr_ratios):
            reward = risk * rr

            if signal_type == SignalType.LONG:
                tp_price = entry_price + reward
            else:
                tp_price = entry_price - reward

            levels.append(
                TakeProfitLevel(
                    price=tp_price,
                    percent_to_close=close_percents[i],
                    risk_reward_ratio=rr,
                    method="risk_reward",
                )
            )

            logger.debug(
                f"TP{i+1}: price={tp_price:.4f}, R:R={rr:.2f}, " f"close={close_percents[i]:.0f}%"
            )

        return levels

    def _calculate_atr_based(
        self, entry_price: float, stop_loss: float, signal_type: SignalType, atr: float
    ) -> List[TakeProfitLevel]:
        """
        Calculate TP levels based on ATR multiples.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            signal_type: Signal type
            atr: Average True Range

        Returns:
            List of TP levels
        """
        if atr <= 0:
            logger.warning("ATR not available, using R:R method")
            return self._calculate_risk_reward(
                entry_price, stop_loss, signal_type, self.default_rr_ratios
            )

        # ATR multiples for TP levels
        atr_multiples = self.config.get("atr_multiples", [2.0, 3.0, 4.0])
        close_percents = self._get_close_percents(len(atr_multiples))

        levels = []

        for i, mult in enumerate(atr_multiples[: self.num_levels]):
            reward = atr * mult

            if signal_type == SignalType.LONG:
                tp_price = entry_price + reward
            else:
                tp_price = entry_price - reward

            # Calculate R:R for this level
            if signal_type == SignalType.LONG:
                risk = entry_price - stop_loss
            else:
                risk = stop_loss - entry_price

            rr = (reward / risk) if risk > 0 else 0

            levels.append(
                TakeProfitLevel(
                    price=tp_price,
                    percent_to_close=close_percents[i],
                    risk_reward_ratio=rr,
                    method="atr_based",
                )
            )

        return levels

    def _calculate_percentage(
        self, entry_price: float, signal_type: SignalType
    ) -> List[TakeProfitLevel]:
        """
        Calculate TP levels as percentage from entry.

        Args:
            entry_price: Entry price
            signal_type: Signal type

        Returns:
            List of TP levels
        """
        # Percentage targets
        percentages = self.config.get("tp_percentages", [0.03, 0.05, 0.08])  # 3%, 5%, 8%
        close_percents = self._get_close_percents(len(percentages))

        levels = []

        for i, pct in enumerate(percentages[: self.num_levels]):
            reward = entry_price * pct

            if signal_type == SignalType.LONG:
                tp_price = entry_price + reward
            else:
                tp_price = entry_price - reward

            levels.append(
                TakeProfitLevel(
                    price=tp_price,
                    percent_to_close=close_percents[i],
                    risk_reward_ratio=0,  # Not applicable for percentage method
                    method="percentage",
                )
            )

        return levels

    def _calculate_fibonacci(
        self, entry_price: float, stop_loss: float, signal_type: SignalType
    ) -> List[TakeProfitLevel]:
        """
        Calculate TP levels using Fibonacci extensions.

        Common extensions: 1.618, 2.618, 4.236

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            signal_type: Signal type

        Returns:
            List of TP levels
        """
        # Calculate risk
        if signal_type == SignalType.LONG:
            risk = entry_price - stop_loss
        else:
            risk = stop_loss - entry_price

        # Fibonacci extension ratios
        fib_ratios = [1.618, 2.618, 4.236]
        close_percents = self._get_close_percents(len(fib_ratios))

        levels = []

        for i, fib in enumerate(fib_ratios[: self.num_levels]):
            reward = risk * fib

            if signal_type == SignalType.LONG:
                tp_price = entry_price + reward
            else:
                tp_price = entry_price - reward

            levels.append(
                TakeProfitLevel(
                    price=tp_price,
                    percent_to_close=close_percents[i],
                    risk_reward_ratio=fib,
                    method="fibonacci",
                )
            )

        return levels

    def _calculate_resistance(
        self,
        entry_price: float,
        stop_loss: float,
        signal_type: SignalType,
        resistance_levels: List[float],
    ) -> List[TakeProfitLevel]:
        """
        Calculate TP levels based on resistance levels.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            signal_type: Signal type
            resistance_levels: Key resistance levels

        Returns:
            List of TP levels
        """
        if not resistance_levels:
            logger.warning("No resistance levels provided, using R:R method")
            return self._calculate_risk_reward(
                entry_price, stop_loss, signal_type, self.default_rr_ratios
            )

        # Calculate risk
        if signal_type == SignalType.LONG:
            risk = entry_price - stop_loss
            # Get resistances above entry
            valid_levels = sorted([r for r in resistance_levels if r > entry_price])
        else:
            risk = stop_loss - entry_price
            # Get supports below entry
            valid_levels = sorted([s for s in resistance_levels if s < entry_price], reverse=True)

        if not valid_levels:
            logger.warning("No valid resistance levels found, using R:R method")
            return self._calculate_risk_reward(
                entry_price, stop_loss, signal_type, self.default_rr_ratios
            )

        # Use first N levels
        target_levels = valid_levels[: self.num_levels]
        close_percents = self._get_close_percents(len(target_levels))

        levels = []

        for i, tp_price in enumerate(target_levels):
            # Calculate R:R for this level
            if signal_type == SignalType.LONG:
                reward = tp_price - entry_price
            else:
                reward = entry_price - tp_price

            rr = (reward / risk) if risk > 0 else 0

            levels.append(
                TakeProfitLevel(
                    price=tp_price,
                    percent_to_close=close_percents[i],
                    risk_reward_ratio=rr,
                    method="resistance",
                )
            )

        return levels

    def _get_close_percents(self, num_levels: int) -> List[float]:
        """
        Get position close percentages for each level.

        Args:
            num_levels: Number of TP levels

        Returns:
            List of close percentages
        """
        if num_levels == 1:
            return [100.0]
        elif num_levels == 2:
            return [50.0, 50.0]
        elif num_levels == 3:
            return [33.3, 33.3, 33.4]
        else:
            # Equal distribution
            base_percent = 100.0 / num_levels
            return [base_percent] * num_levels

    def should_move_stop_to_breakeven(
        self,
        entry_price: float,
        current_price: float,
        first_tp: float,
        signal_type: SignalType,
        threshold_percent: float = 0.5,
    ) -> bool:
        """
        Check if stop should be moved to breakeven.

        Typically when price reaches certain % toward first TP.

        Args:
            entry_price: Entry price
            current_price: Current price
            first_tp: First take profit level
            signal_type: Signal type
            threshold_percent: % of distance to TP (0-1)

        Returns:
            True if should move to breakeven
        """
        if signal_type == SignalType.LONG:
            distance_to_tp = first_tp - entry_price
            distance_traveled = current_price - entry_price

            if distance_to_tp <= 0:
                return False

            progress = distance_traveled / distance_to_tp

        else:  # SHORT
            distance_to_tp = entry_price - first_tp
            distance_traveled = entry_price - current_price

            if distance_to_tp <= 0:
                return False

            progress = distance_traveled / distance_to_tp

        should_move = progress >= threshold_percent

        if should_move:
            logger.debug(f"Move to breakeven triggered: {progress:.1%} progress to TP1")

        return should_move

    def get_next_target(
        self,
        current_price: float,
        remaining_targets: List[TakeProfitLevel],
        signal_type: SignalType,
    ) -> Optional[TakeProfitLevel]:
        """
        Get the next target level to be reached.

        Args:
            current_price: Current price
            remaining_targets: Remaining TP levels
            signal_type: Signal type

        Returns:
            Next TP level or None
        """
        if not remaining_targets:
            return None

        if signal_type == SignalType.LONG:
            # For LONG, find nearest target above current price
            valid_targets = [t for t in remaining_targets if t.price > current_price]
            if valid_targets:
                return min(valid_targets, key=lambda t: t.price)
        else:  # SHORT
            # For SHORT, find nearest target below current price
            valid_targets = [t for t in remaining_targets if t.price < current_price]
            if valid_targets:
                return max(valid_targets, key=lambda t: t.price)

        return None
