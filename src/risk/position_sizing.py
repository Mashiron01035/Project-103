"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : position_sizing.py
    Project     : Project-103
    Description : Position sizing calculation based on risk parameters and account balance.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from src.models.signal import Signal, SignalType

logger = logging.getLogger(__name__)


class PositionSizingMethod(Enum):
    """Position sizing methods."""

    FIXED_RISK = "fixed_risk"  # Fixed % risk per trade
    FIXED_AMOUNT = "fixed_amount"  # Fixed position size
    KELLY_CRITERION = "kelly_criterion"  # Kelly formula
    PERCENT_EQUITY = "percent_equity"  # Fixed % of equity
    VOLATILITY_ADJUSTED = "volatility_adjusted"  # Adjusted for volatility


@dataclass
class PositionSizeResult:
    """Result of position size calculation."""

    size_base: float  # Position size in base currency (e.g., SOL)
    size_quote: float  # Position size in quote currency (e.g., USDT)
    risk_amount: float  # Risk amount in quote currency
    risk_percent: float  # Risk as % of account
    leverage: int  # Leverage to use
    method: PositionSizingMethod  # Method used
    metadata: Dict[str, Any]  # Additional info


class PositionSizer:
    """
    Calculates optimal position size based on risk parameters.

    Supports multiple position sizing methods and adjusts for
    volatility, account balance, and risk limits.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize PositionSizer.

        Args:
            config: Position sizing configuration
        """
        self.config = config or {}

        # Default parameters
        self.method = PositionSizingMethod(self.config.get("method", "fixed_risk"))
        self.max_risk_per_trade = self.config.get("max_risk_per_trade", 0.01)  # 1%
        self.max_position_percent = self.config.get("max_position_percent", 0.10)  # 10%
        self.min_position_size_usdt = self.config.get("min_position_size_usdt", 10.0)
        self.max_leverage = self.config.get("max_leverage", 10)
        self.default_leverage = self.config.get("default_leverage", 1)

        logger.info(f"PositionSizer initialized with method={self.method.value}")

    def calculate(
        self,
        signal: Signal,
        account_balance: float,
        volatility: float = 0.0,
        win_rate: Optional[float] = None,
    ) -> PositionSizeResult:
        """
        Calculate position size for a signal.

        Args:
            signal: Trading signal
            account_balance: Current account balance (USDT)
            volatility: Market volatility (0-1)
            win_rate: Historical win rate (0-1, for Kelly)

        Returns:
            PositionSizeResult object
        """
        # Validate entry price to prevent division by zero
        if not signal.entry_price or signal.entry_price <= 0:
            logger.error(
                f"Invalid entry_price {signal.entry_price} for {signal.symbol}, cannot calculate position size"
            )
            return PositionSizeResult(
                size_base=0.0,
                size_quote=0.0,
                risk_amount=0.0,
                risk_percent=0.0,
                leverage=self.default_leverage,
                method=self.method,
                metadata={"error": "invalid_entry_price"},
            )

        # Validate account balance
        if account_balance <= 0:
            logger.error(
                f"Invalid account_balance {account_balance}, cannot calculate position size"
            )
            return PositionSizeResult(
                size_base=0.0,
                size_quote=0.0,
                risk_amount=0.0,
                risk_percent=0.0,
                leverage=self.default_leverage,
                method=self.method,
                metadata={"error": "invalid_balance"},
            )

        metadata = {
            "signal_id": signal.id,
            "symbol": signal.symbol,
            "account_balance": account_balance,
            "volatility": volatility,
        }

        # Calculate based on method
        if self.method == PositionSizingMethod.FIXED_RISK:
            result = self._calculate_fixed_risk(signal, account_balance, metadata)
        elif self.method == PositionSizingMethod.FIXED_AMOUNT:
            result = self._calculate_fixed_amount(signal, account_balance, metadata)
        elif self.method == PositionSizingMethod.KELLY_CRITERION:
            result = self._calculate_kelly(signal, account_balance, win_rate, metadata)
        elif self.method == PositionSizingMethod.PERCENT_EQUITY:
            result = self._calculate_percent_equity(signal, account_balance, metadata)
        else:  # VOLATILITY_ADJUSTED
            result = self._calculate_volatility_adjusted(
                signal, account_balance, volatility, metadata
            )

        # Apply limits and adjustments
        result = self._apply_limits(result, account_balance)
        result = self._adjust_for_leverage(result, signal)

        logger.info(
            f"Position size calculated: {result.size_base:.4f} {signal.symbol} "
            f"({result.size_quote:.2f} USDT) with {result.risk_percent:.2f}% risk"
        )

        return result

    def _calculate_fixed_risk(
        self, signal: Signal, account_balance: float, metadata: Dict[str, Any]
    ) -> PositionSizeResult:
        """
        Calculate position size using fixed risk method.

        Risk fixed % of account per trade.

        Args:
            signal: Trading signal
            account_balance: Account balance
            metadata: Metadata dict

        Returns:
            PositionSizeResult
        """
        # Calculate risk amount
        risk_percent = min(signal.position_size_percent / 100, self.max_risk_per_trade)
        risk_amount = account_balance * risk_percent

        # Calculate price difference to stop loss
        entry = signal.entry_price
        sl = signal.stop_loss

        if signal.signal_type == SignalType.LONG:
            price_diff = entry - sl
        else:
            price_diff = sl - entry

        if price_diff <= 0:
            logger.warning(f"Invalid stop loss for signal {signal.id}")
            price_diff = entry * 0.02  # Default 2% stop

        # Position size = risk / (entry - stop_loss)
        size_base = risk_amount / price_diff
        size_quote = size_base * entry

        metadata["price_diff"] = price_diff
        metadata["calculation"] = "fixed_risk"

        return PositionSizeResult(
            size_base=size_base,
            size_quote=size_quote,
            risk_amount=risk_amount,
            risk_percent=risk_percent * 100,
            leverage=self.default_leverage,
            method=self.method,
            metadata=metadata,
        )

    def _calculate_fixed_amount(
        self, signal: Signal, account_balance: float, metadata: Dict[str, Any]
    ) -> PositionSizeResult:
        """
        Calculate position size using fixed amount method.

        Args:
            signal: Trading signal
            account_balance: Account balance
            metadata: Metadata dict

        Returns:
            PositionSizeResult
        """
        # Fixed USDT amount per trade
        fixed_amount = self.config.get("fixed_amount_usdt", 100.0)
        size_quote = min(fixed_amount, account_balance * self.max_position_percent)
        size_base = size_quote / signal.entry_price

        # Calculate actual risk
        entry = signal.entry_price
        sl = signal.stop_loss

        if signal.signal_type == SignalType.LONG:
            price_diff = entry - sl
        else:
            price_diff = sl - entry

        risk_amount = size_base * abs(price_diff)
        risk_percent = (risk_amount / account_balance) * 100

        metadata["fixed_amount"] = fixed_amount
        metadata["calculation"] = "fixed_amount"

        return PositionSizeResult(
            size_base=size_base,
            size_quote=size_quote,
            risk_amount=risk_amount,
            risk_percent=risk_percent,
            leverage=self.default_leverage,
            method=self.method,
            metadata=metadata,
        )

    def _calculate_kelly(
        self,
        signal: Signal,
        account_balance: float,
        win_rate: Optional[float],
        metadata: Dict[str, Any],
    ) -> PositionSizeResult:
        """
        Calculate position size using Kelly Criterion.

        Kelly % = W - [(1 - W) / R]
        Where W = win rate, R = average win/loss ratio

        Args:
            signal: Trading signal
            account_balance: Account balance
            win_rate: Historical win rate
            metadata: Metadata dict

        Returns:
            PositionSizeResult
        """
        if win_rate is None or win_rate <= 0:
            logger.warning("Win rate not available, using fixed risk instead")
            return self._calculate_fixed_risk(signal, account_balance, metadata)

        # Kelly formula
        w = win_rate
        r = signal.risk_reward_ratio

        if r <= 0:
            r = 2.0  # Default R:R

        kelly_percent = w - ((1 - w) / r)

        # Apply Kelly fraction (typically use half or quarter Kelly)
        kelly_fraction = self.config.get("kelly_fraction", 0.25)
        kelly_percent = max(0, kelly_percent * kelly_fraction)

        # Cap at max risk
        kelly_percent = min(kelly_percent, self.max_risk_per_trade)

        size_quote = account_balance * kelly_percent
        size_base = size_quote / signal.entry_price

        # Calculate actual risk to SL
        entry = signal.entry_price
        sl = signal.stop_loss

        if signal.signal_type == SignalType.LONG:
            price_diff = entry - sl
        else:
            price_diff = sl - entry

        actual_risk = size_base * abs(price_diff)

        metadata["kelly_percent"] = kelly_percent * 100
        metadata["win_rate"] = win_rate
        metadata["kelly_fraction"] = kelly_fraction
        metadata["calculation"] = "kelly_criterion"

        return PositionSizeResult(
            size_base=size_base,
            size_quote=size_quote,
            risk_amount=actual_risk,
            risk_percent=kelly_percent * 100,
            leverage=self.default_leverage,
            method=self.method,
            metadata=metadata,
        )

    def _calculate_percent_equity(
        self, signal: Signal, account_balance: float, metadata: Dict[str, Any]
    ) -> PositionSizeResult:
        """
        Calculate position size as fixed % of equity.

        Args:
            signal: Trading signal
            account_balance: Account balance
            metadata: Metadata dict

        Returns:
            PositionSizeResult
        """
        equity_percent = self.config.get("equity_percent", 0.05)  # 5%
        equity_percent = min(equity_percent, self.max_position_percent)

        size_quote = account_balance * equity_percent
        size_base = size_quote / signal.entry_price

        # Calculate risk
        entry = signal.entry_price
        sl = signal.stop_loss

        if signal.signal_type == SignalType.LONG:
            price_diff = entry - sl
        else:
            price_diff = sl - entry

        risk_amount = size_base * abs(price_diff)
        risk_percent = (risk_amount / account_balance) * 100

        metadata["equity_percent"] = equity_percent * 100
        metadata["calculation"] = "percent_equity"

        return PositionSizeResult(
            size_base=size_base,
            size_quote=size_quote,
            risk_amount=risk_amount,
            risk_percent=risk_percent,
            leverage=self.default_leverage,
            method=self.method,
            metadata=metadata,
        )

    def _calculate_volatility_adjusted(
        self, signal: Signal, account_balance: float, volatility: float, metadata: Dict[str, Any]
    ) -> PositionSizeResult:
        """
        Calculate position size adjusted for volatility.

        Higher volatility = smaller position size.

        Args:
            signal: Trading signal
            account_balance: Account balance
            volatility: Market volatility
            metadata: Metadata dict

        Returns:
            PositionSizeResult
        """
        # Base risk
        base_risk = self.max_risk_per_trade

        # Adjust for volatility (reduce size in high volatility)
        if volatility > 0.05:  # 5% volatility
            vol_multiplier = 0.5
        elif volatility > 0.03:  # 3% volatility
            vol_multiplier = 0.75
        else:
            vol_multiplier = 1.0

        adjusted_risk = base_risk * vol_multiplier
        risk_amount = account_balance * adjusted_risk

        # Calculate size
        entry = signal.entry_price
        sl = signal.stop_loss

        if signal.signal_type == SignalType.LONG:
            price_diff = entry - sl
        else:
            price_diff = sl - entry

        if price_diff <= 0:
            price_diff = entry * 0.02

        size_base = risk_amount / price_diff
        size_quote = size_base * entry

        metadata["volatility"] = volatility
        metadata["volatility_multiplier"] = vol_multiplier
        metadata["base_risk"] = base_risk
        metadata["adjusted_risk"] = adjusted_risk
        metadata["calculation"] = "volatility_adjusted"

        return PositionSizeResult(
            size_base=size_base,
            size_quote=size_quote,
            risk_amount=risk_amount,
            risk_percent=adjusted_risk * 100,
            leverage=self.default_leverage,
            method=self.method,
            metadata=metadata,
        )

    def _apply_limits(
        self, result: PositionSizeResult, account_balance: float
    ) -> PositionSizeResult:
        """
        Apply position size limits.

        Args:
            result: Position size result
            account_balance: Account balance

        Returns:
            Adjusted PositionSizeResult
        """
        # Check minimum position size
        if result.size_quote < self.min_position_size_usdt:
            logger.warning(
                f"Position size {result.size_quote:.2f} below minimum "
                f"{self.min_position_size_usdt:.2f}"
            )
            result.metadata["below_minimum"] = True

        # Check maximum position size
        max_size_quote = account_balance * self.max_position_percent
        if result.size_quote > max_size_quote:
            scale_factor = max_size_quote / result.size_quote
            result.size_quote = max_size_quote
            result.size_base *= scale_factor
            result.risk_amount *= scale_factor
            result.metadata["position_capped"] = True
            logger.info(f"Position size capped to {self.max_position_percent*100}% of account")

        return result

    def _adjust_for_leverage(
        self, result: PositionSizeResult, signal: Signal
    ) -> PositionSizeResult:
        """
        Adjust position size for leverage.

        Args:
            result: Position size result
            signal: Trading signal

        Returns:
            Adjusted PositionSizeResult
        """
        # Determine optimal leverage based on confidence
        if signal.confidence >= 0.9:
            leverage = min(3, self.max_leverage)
        elif signal.confidence >= 0.75:
            leverage = min(2, self.max_leverage)
        else:
            leverage = 1

        result.leverage = leverage
        result.metadata["leverage"] = leverage

        return result
