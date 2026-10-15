"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : stop_loss_calculator.py
    Project     : Project-103
    Description : Stop loss calculation using various methods (ATR, percentage, support/resistance).

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from src.models.signal import SignalType

logger = logging.getLogger(__name__)


class StopLossMethod(Enum):
    """Stop loss calculation methods."""

    ATR_BASED = "atr_based"  # Based on Average True Range
    PERCENTAGE = "percentage"  # Fixed percentage from entry
    SUPPORT_RESISTANCE = "support_resistance"  # Key price levels
    VOLATILITY_ADJUSTED = "volatility_adjusted"  # Adjusted for volatility
    TRAILING = "trailing"  # Trailing stop loss


class StopLossCalculator:
    """
    Calculates stop loss levels using various methods.

    Provides dynamic stop loss calculation based on market conditions,
    volatility, and price action.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize StopLossCalculator.

        Args:
            config: Calculator configuration
        """
        self.config = config or {}

        # Default parameters
        self.method = StopLossMethod(self.config.get("method", "atr_based"))
        self.atr_multiplier = self.config.get("atr_multiplier", 1.5)
        self.default_percentage = self.config.get("default_percentage", 0.02)  # 2%
        self.min_stop_distance_percent = self.config.get("min_stop_distance_percent", 0.005)  # 0.5%
        self.max_stop_distance_percent = self.config.get("max_stop_distance_percent", 0.10)  # 10%

        logger.info(f"StopLossCalculator initialized with method={self.method.value}")

    def calculate(
        self,
        entry_price: float,
        signal_type: SignalType,
        market_data: Dict[str, Any],
        atr: Optional[float] = None,
        support_resistance: Optional[List[float]] = None,
    ) -> float:
        """
        Calculate stop loss level.

        Args:
            entry_price: Entry price
            signal_type: LONG or SHORT
            market_data: Current market data
            atr: Average True Range (optional)
            support_resistance: Support/resistance levels (optional)

        Returns:
            Stop loss price
        """
        if self.method == StopLossMethod.ATR_BASED:
            return self._calculate_atr_based(
                entry_price, signal_type, atr or market_data.get("atr", 0)
            )
        elif self.method == StopLossMethod.PERCENTAGE:
            return self._calculate_percentage(entry_price, signal_type)
        elif self.method == StopLossMethod.SUPPORT_RESISTANCE:
            return self._calculate_support_resistance(
                entry_price, signal_type, support_resistance or []
            )
        elif self.method == StopLossMethod.VOLATILITY_ADJUSTED:
            return self._calculate_volatility_adjusted(
                entry_price, signal_type, market_data.get("volatility", 0)
            )
        else:  # TRAILING
            return self._calculate_trailing(entry_price, signal_type, market_data)

    def _calculate_atr_based(
        self, entry_price: float, signal_type: SignalType, atr: float
    ) -> float:
        """
        Calculate stop loss based on ATR.

        Args:
            entry_price: Entry price
            signal_type: Signal type
            atr: Average True Range

        Returns:
            Stop loss price
        """
        if atr <= 0:
            # Fallback to percentage if ATR not available
            logger.warning("ATR not available, using percentage method")
            return self._calculate_percentage(entry_price, signal_type)

        stop_distance = atr * self.atr_multiplier

        if signal_type == SignalType.LONG:
            stop_loss = entry_price - stop_distance
        else:
            stop_loss = entry_price + stop_distance

        # Apply limits
        stop_loss = self._apply_limits(entry_price, stop_loss, signal_type)

        logger.debug(
            f"ATR-based SL: entry={entry_price:.4f}, " f"atr={atr:.4f}, sl={stop_loss:.4f}"
        )

        return stop_loss

    def _calculate_percentage(self, entry_price: float, signal_type: SignalType) -> float:
        """
        Calculate stop loss as percentage from entry.

        Args:
            entry_price: Entry price
            signal_type: Signal type

        Returns:
            Stop loss price
        """
        stop_distance = entry_price * self.default_percentage

        if signal_type == SignalType.LONG:
            stop_loss = entry_price - stop_distance
        else:
            stop_loss = entry_price + stop_distance

        logger.debug(
            f"Percentage SL: entry={entry_price:.4f}, "
            f"pct={self.default_percentage:.2%}, sl={stop_loss:.4f}"
        )

        return stop_loss

    def _calculate_support_resistance(
        self, entry_price: float, signal_type: SignalType, levels: List[float]
    ) -> float:
        """
        Calculate stop loss based on support/resistance levels.

        Args:
            entry_price: Entry price
            signal_type: Signal type
            levels: Support/resistance levels

        Returns:
            Stop loss price
        """
        if not levels:
            logger.warning("No support/resistance levels provided, using percentage")
            return self._calculate_percentage(entry_price, signal_type)

        # Buffer below support or above resistance
        buffer_percent = 0.002  # 0.2%

        if signal_type == SignalType.LONG:
            # Find nearest support below entry
            supports = [level for level in levels if level < entry_price]
            if supports:
                nearest_support = max(supports)
                stop_loss = nearest_support * (1 - buffer_percent)
            else:
                # No support found, use percentage
                return self._calculate_percentage(entry_price, signal_type)
        else:  # SHORT
            # Find nearest resistance above entry
            resistances = [level for level in levels if level > entry_price]
            if resistances:
                nearest_resistance = min(resistances)
                stop_loss = nearest_resistance * (1 + buffer_percent)
            else:
                # No resistance found, use percentage
                return self._calculate_percentage(entry_price, signal_type)

        # Apply limits
        stop_loss = self._apply_limits(entry_price, stop_loss, signal_type)

        logger.debug(f"S/R-based SL: entry={entry_price:.4f}, sl={stop_loss:.4f}")

        return stop_loss

    def _calculate_volatility_adjusted(
        self, entry_price: float, signal_type: SignalType, volatility: float
    ) -> float:
        """
        Calculate stop loss adjusted for volatility.

        Higher volatility = wider stop.

        Args:
            entry_price: Entry price
            signal_type: Signal type
            volatility: Market volatility (0-1)

        Returns:
            Stop loss price
        """
        # Base percentage
        base_pct = self.default_percentage

        # Adjust for volatility
        if volatility > 0.05:  # High volatility (5%+)
            adjusted_pct = base_pct * 1.5
        elif volatility > 0.03:  # Medium volatility (3%+)
            adjusted_pct = base_pct * 1.25
        else:  # Low volatility
            adjusted_pct = base_pct

        stop_distance = entry_price * adjusted_pct

        if signal_type == SignalType.LONG:
            stop_loss = entry_price - stop_distance
        else:
            stop_loss = entry_price + stop_distance

        # Apply limits
        stop_loss = self._apply_limits(entry_price, stop_loss, signal_type)

        logger.debug(
            f"Volatility-adjusted SL: entry={entry_price:.4f}, "
            f"vol={volatility:.4f}, sl={stop_loss:.4f}"
        )

        return stop_loss

    def _calculate_trailing(
        self, entry_price: float, signal_type: SignalType, market_data: Dict[str, Any]
    ) -> float:
        """
        Calculate initial trailing stop loss.

        Args:
            entry_price: Entry price
            signal_type: Signal type
            market_data: Market data

        Returns:
            Initial stop loss price
        """
        # Get trailing distance from config
        trailing_percent = self.config.get("trailing_percent", 0.03)  # 3%
        atr = market_data.get("atr", 0)

        if atr > 0:
            # Use ATR for trailing distance
            trailing_distance = atr * 2
        else:
            # Use percentage
            trailing_distance = entry_price * trailing_percent

        if signal_type == SignalType.LONG:
            stop_loss = entry_price - trailing_distance
        else:
            stop_loss = entry_price + trailing_distance

        # Apply limits
        stop_loss = self._apply_limits(entry_price, stop_loss, signal_type)

        logger.debug(f"Trailing SL: entry={entry_price:.4f}, sl={stop_loss:.4f}")

        return stop_loss

    def _apply_limits(self, entry_price: float, stop_loss: float, signal_type: SignalType) -> float:
        """
        Apply minimum and maximum stop distance limits.

        Args:
            entry_price: Entry price
            stop_loss: Calculated stop loss
            signal_type: Signal type

        Returns:
            Adjusted stop loss
        """
        if signal_type == SignalType.LONG:
            distance = entry_price - stop_loss
        else:
            distance = stop_loss - entry_price

        distance_percent = distance / entry_price

        # Check minimum distance
        if distance_percent < self.min_stop_distance_percent:
            distance = entry_price * self.min_stop_distance_percent
            if signal_type == SignalType.LONG:
                stop_loss = entry_price - distance
            else:
                stop_loss = entry_price + distance

            logger.debug("Stop loss adjusted to minimum distance")

        # Check maximum distance
        elif distance_percent > self.max_stop_distance_percent:
            distance = entry_price * self.max_stop_distance_percent
            if signal_type == SignalType.LONG:
                stop_loss = entry_price - distance
            else:
                stop_loss = entry_price + distance

            logger.debug("Stop loss adjusted to maximum distance")

        return stop_loss

    def update_trailing_stop(
        self,
        current_stop: float,
        current_price: float,
        entry_price: float,
        signal_type: SignalType,
        trailing_distance: float,
    ) -> float:
        """
        Update trailing stop loss based on current price.

        Args:
            current_stop: Current stop loss
            current_price: Current market price
            entry_price: Entry price
            signal_type: Signal type
            trailing_distance: Trailing distance

        Returns:
            Updated stop loss (or current if no update)
        """
        if signal_type == SignalType.LONG:
            # For LONG, stop trails below price as it rises
            new_stop = current_price - trailing_distance

            # Only move stop up, never down
            if new_stop > current_stop:
                logger.debug(f"Trailing stop updated: {current_stop:.4f} -> {new_stop:.4f}")
                return new_stop
        else:  # SHORT
            # For SHORT, stop trails above price as it falls
            new_stop = current_price + trailing_distance

            # Only move stop down, never up
            if new_stop < current_stop:
                logger.debug(f"Trailing stop updated: {current_stop:.4f} -> {new_stop:.4f}")
                return new_stop

        return current_stop

    def move_to_breakeven(
        self, entry_price: float, signal_type: SignalType, buffer_percent: float = 0.001
    ) -> float:
        """
        Calculate breakeven stop loss (entry price + small buffer).

        Args:
            entry_price: Entry price
            signal_type: Signal type
            buffer_percent: Small buffer above/below entry

        Returns:
            Breakeven stop loss
        """
        buffer = entry_price * buffer_percent

        if signal_type == SignalType.LONG:
            breakeven_stop = entry_price + buffer
        else:
            breakeven_stop = entry_price - buffer

        logger.debug(f"Breakeven stop: {breakeven_stop:.4f}")

        return breakeven_stop
