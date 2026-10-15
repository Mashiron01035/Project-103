"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : validator.py
    Project     : Project-103
    Description : Signal validation logic. Validates signals before execution based on various criteria.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.models.signal import Signal, SignalType

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of signal validation."""

    valid: bool
    errors: List[str]
    warnings: List[str]
    metadata: Dict[str, Any]

    def __bool__(self) -> bool:
        """Allow ValidationResult to be used in boolean context."""
        return self.valid

    def add_error(self, error: str) -> None:
        """Add validation error."""
        self.errors.append(error)
        self.valid = False

    def add_warning(self, warning: str) -> None:
        """Add validation warning."""
        self.warnings.append(warning)


class SignalValidator:
    """
    Validates trading signals before execution.

    Performs comprehensive validation including:
    - Basic data validation
    - Price level validation
    - Risk/reward validation
    - Market condition validation
    - Timing validation
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize SignalValidator.

        Args:
            config: Validator configuration
        """
        self.config = config or {}

        # Validation thresholds
        self.min_confidence = self.config.get("min_confidence", 0.6)
        self.min_risk_reward = self.config.get("min_risk_reward", 1.5)
        self.max_position_size_percent = self.config.get("max_position_size_percent", 10.0)
        self.max_signal_age_seconds = self.config.get("max_signal_age_seconds", 300)  # 5 minutes
        self.min_spread_percent = self.config.get("min_spread_percent", 0.001)  # 0.1%
        self.max_slippage_percent = self.config.get("max_slippage_percent", 0.005)  # 0.5%

        logger.info("SignalValidator initialized")

    def validate(
        self, signal: Signal, market_data: Optional[Dict[str, Any]] = None
    ) -> ValidationResult:
        """
        Validate a trading signal.

        Args:
            signal: Signal to validate
            market_data: Current market data (optional)

        Returns:
            ValidationResult object
        """
        result = ValidationResult(valid=True, errors=[], warnings=[], metadata={})

        # Run all validation checks
        self._validate_basic_data(signal, result)
        self._validate_price_levels(signal, result)
        self._validate_risk_reward(signal, result)
        self._validate_position_size(signal, result)
        self._validate_signal_age(signal, result)

        if market_data:
            self._validate_market_conditions(signal, market_data, result)
            self._validate_price_movement(signal, market_data, result)

        # Log result
        if result.valid:
            logger.info(f"Signal {signal.id} validated successfully")
            if result.warnings:
                logger.warning(f"Signal {signal.id} has warnings: {', '.join(result.warnings)}")
        else:
            logger.warning(f"Signal {signal.id} validation failed: {', '.join(result.errors)}")

        return result

    def _validate_basic_data(self, signal: Signal, result: ValidationResult) -> None:
        """
        Validate basic signal data.

        Args:
            signal: Signal to validate
            result: Validation result to update
        """
        # Check required fields
        if not signal.symbol:
            result.add_error("Symbol is required")

        if not signal.strategy_name:
            result.add_error("Strategy name is required")

        if not signal.timeframe:
            result.add_error("Timeframe is required")

        # Check signal type
        if signal.signal_type not in [SignalType.LONG, SignalType.SHORT]:
            result.add_error(f"Invalid signal type: {signal.signal_type}")

        # Check confidence
        if not 0 <= signal.confidence <= 1:
            result.add_error(f"Confidence must be between 0 and 1, got {signal.confidence}")

        if signal.confidence < self.min_confidence:
            result.add_error(
                f"Confidence {signal.confidence:.2f} below minimum {self.min_confidence}"
            )

        # Check entry price
        if signal.entry_price <= 0:
            result.add_error(f"Invalid entry price: {signal.entry_price}")

    def _validate_price_levels(self, signal: Signal, result: ValidationResult) -> None:
        """
        Validate stop loss and take profit levels.

        Args:
            signal: Signal to validate
            result: Validation result to update
        """
        entry = signal.entry_price
        sl = signal.stop_loss
        tp = signal.take_profit

        # Check stop loss
        if sl <= 0:
            result.add_error(f"Invalid stop loss: {sl}")
            return

        if signal.signal_type == SignalType.LONG:
            # For LONG: SL should be below entry
            if sl >= entry:
                result.add_error(
                    f"Stop loss {sl} must be below entry price {entry} for LONG signal"
                )

            # Check TP levels
            for i, tp_level in enumerate(tp):
                if tp_level <= entry:
                    result.add_error(
                        f"Take profit level {i+1} ({tp_level}) must be above "
                        f"entry price {entry} for LONG signal"
                    )

        else:  # SHORT
            # For SHORT: SL should be above entry
            if sl <= entry:
                result.add_error(
                    f"Stop loss {sl} must be above entry price {entry} for SHORT signal"
                )

            # Check TP levels
            for i, tp_level in enumerate(tp):
                if tp_level >= entry:
                    result.add_error(
                        f"Take profit level {i+1} ({tp_level}) must be below "
                        f"entry price {entry} for SHORT signal"
                    )

        # Check TP levels are ordered
        if len(tp) > 1:
            if signal.signal_type == SignalType.LONG:
                for i in range(len(tp) - 1):
                    if tp[i] >= tp[i + 1]:
                        result.add_warning(
                            "Take profit levels should be in ascending order for LONG"
                        )
                        break
            else:
                for i in range(len(tp) - 1):
                    if tp[i] <= tp[i + 1]:
                        result.add_warning(
                            "Take profit levels should be in descending order for SHORT"
                        )
                        break

    def _validate_risk_reward(self, signal: Signal, result: ValidationResult) -> None:
        """
        Validate risk/reward ratio.

        Args:
            signal: Signal to validate
            result: Validation result to update
        """
        rr = signal.risk_reward_ratio

        if rr <= 0:
            result.add_error(f"Invalid risk/reward ratio: {rr}")
            return

        if rr < self.min_risk_reward:
            result.add_error(f"Risk/reward ratio {rr:.2f} below minimum {self.min_risk_reward}")

        # Calculate actual R:R from prices to verify
        if signal.take_profit:
            entry = signal.entry_price
            sl = signal.stop_loss
            tp = signal.take_profit[0]

            if signal.signal_type == SignalType.LONG:
                risk = entry - sl
                reward = tp - entry
            else:
                risk = sl - entry
                reward = entry - tp

            if risk > 0:
                actual_rr = reward / risk
                if abs(actual_rr - rr) > 0.1:  # Allow 0.1 difference
                    result.add_warning(
                        f"Stated R:R {rr:.2f} differs from calculated {actual_rr:.2f}"
                    )

    def _validate_position_size(self, signal: Signal, result: ValidationResult) -> None:
        """
        Validate position size.

        Args:
            signal: Signal to validate
            result: Validation result to update
        """
        size_pct = signal.position_size_percent

        if size_pct <= 0:
            result.add_error(f"Invalid position size percent: {size_pct}")
            return

        if size_pct > self.max_position_size_percent:
            result.add_error(
                f"Position size {size_pct:.2f}% exceeds maximum "
                f"{self.max_position_size_percent}%"
            )

    def _validate_signal_age(self, signal: Signal, result: ValidationResult) -> None:
        """
        Validate signal is not too old.

        Args:
            signal: Signal to validate
            result: Validation result to update
        """
        current_time = int(time.time() * 1000)
        age_ms = current_time - signal.timestamp
        age_seconds = age_ms / 1000

        if age_seconds > self.max_signal_age_seconds:
            result.add_warning(
                f"Signal is {age_seconds:.1f}s old " f"(max {self.max_signal_age_seconds}s)"
            )

        result.metadata["signal_age_seconds"] = age_seconds

    def _validate_market_conditions(
        self, signal: Signal, market_data: Dict[str, Any], result: ValidationResult
    ) -> None:
        """
        Validate signal against current market conditions.

        Args:
            signal: Signal to validate
            market_data: Current market data
            result: Validation result to update
        """
        # Check spread
        bid = market_data.get("bid", 0)
        ask = market_data.get("ask", 0)

        if bid > 0 and ask > 0:
            spread = ask - bid
            spread_percent = spread / bid

            if spread_percent > self.min_spread_percent * 10:  # 10x normal
                result.add_warning(f"Spread is very wide: {spread_percent:.4%}")

            result.metadata["spread_percent"] = spread_percent

        # Check volume
        volume = market_data.get("volume_24h", 0)
        avg_volume = market_data.get("avg_volume_24h", 0)

        if avg_volume > 0:
            volume_ratio = volume / avg_volume

            if volume_ratio < 0.5:
                result.add_warning(f"Volume is low: {volume_ratio:.1%} of average")
            elif volume_ratio > 3.0:
                result.add_warning(f"Volume is very high: {volume_ratio:.1%} of average")

            result.metadata["volume_ratio"] = volume_ratio

        # Check volatility
        volatility = market_data.get("volatility", 0)
        max_volatility = self.config.get("max_volatility", 0.1)  # 10%

        if volatility > max_volatility:
            result.add_warning(f"Volatility {volatility:.2%} exceeds maximum {max_volatility:.2%}")

        result.metadata["volatility"] = volatility

    def _validate_price_movement(
        self, signal: Signal, market_data: Dict[str, Any], result: ValidationResult
    ) -> None:
        """
        Validate price hasn't moved too far since signal generation.

        Args:
            signal: Signal to validate
            market_data: Current market data
            result: Validation result to update
        """
        current_price = market_data.get("current_price", 0)

        if current_price <= 0:
            result.add_warning("Current price not available for validation")
            return

        price_change = abs(current_price - signal.entry_price) / signal.entry_price

        if price_change > self.max_slippage_percent:
            result.add_error(
                f"Price moved {price_change:.2%} since signal generation "
                f"(max allowed: {self.max_slippage_percent:.2%})"
            )

        result.metadata["price_change_percent"] = price_change
        result.metadata["current_price"] = current_price

    def validate_bulk(
        self, signals: List[Signal], market_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, ValidationResult]:
        """
        Validate multiple signals.

        Args:
            signals: List of signals to validate
            market_data: Current market data (optional)

        Returns:
            Dictionary mapping signal IDs to validation results
        """
        results = {}

        for signal in signals:
            results[signal.id] = self.validate(signal, market_data)

        # Log summary
        valid_count = sum(1 for r in results.values() if r.valid)
        logger.info(
            f"Validated {len(signals)} signals: "
            f"{valid_count} valid, {len(signals) - valid_count} invalid"
        )

        return results

    def get_validation_summary(self, results: Dict[str, ValidationResult]) -> Dict[str, Any]:
        """
        Get summary of validation results.

        Args:
            results: Dictionary of validation results

        Returns:
            Summary statistics
        """
        total = len(results)
        valid = sum(1 for r in results.values() if r.valid)
        invalid = total - valid

        all_errors = []
        all_warnings = []

        for result in results.values():
            all_errors.extend(result.errors)
            all_warnings.extend(result.warnings)

        return {
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "valid_percent": (valid / total * 100) if total > 0 else 0,
            "total_errors": len(all_errors),
            "total_warnings": len(all_warnings),
            "unique_errors": list(set(all_errors)),
            "unique_warnings": list(set(all_warnings)),
        }
