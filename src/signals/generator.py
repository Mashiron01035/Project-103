"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : generator.py
    Project     : Project-103
    Description : Signal generation logic. Analyzes market data and indicators to generate trading signals.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.core.exceptions import DataValidationError, StrategyError
from src.models.signal import Signal, SignalStatus, SignalStrength, SignalType

logger = logging.getLogger(__name__)


@dataclass
class SignalGeneratorInput:
    """Input data for signal generation."""

    symbol: str
    timeframe: str
    market_data: Dict[str, Any]  # MarketData structure
    indicators: Dict[str, Any]  # Indicator results
    strategy_config: Dict[str, Any]
    current_price: float


@dataclass
class SignalContext:
    """Context information for signal generation."""

    symbol: str
    timeframe: str
    timestamp: int
    current_price: float
    volume: float
    volatility: float
    trend_strength: float


class SignalGenerator:
    """
    Main signal generator class.

    Analyzes market data and technical indicators to generate trading signals
    based on configured strategies.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize SignalGenerator.

        Args:
            config: Generator configuration
        """
        self.config = config or {}
        self.min_confidence = self.config.get("min_confidence", 0.6)
        self.max_signals_per_hour = self.config.get("max_signals_per_hour", 10)
        self.recent_signals: List[int] = []  # Timestamps

        logger.info(f"SignalGenerator initialized with min_confidence={self.min_confidence}")

    def analyze(self, input_data: SignalGeneratorInput) -> Optional[Signal]:
        """
        Analyze data and generate trading signal.

        Args:
            input_data: Input data containing market data, indicators, and config

        Returns:
            Signal object if conditions are met, None otherwise

        Raises:
            StrategyError: If strategy analysis fails
            DataValidationError: If input data is invalid
        """
        try:
            # Validate input
            self._validate_input(input_data)

            # Check rate limiting
            if not self._check_rate_limit():
                logger.debug("Signal generation rate limit reached")
                return None

            # Create context
            context = self._create_context(input_data)

            # Analyze indicators
            signal_type, confidence, metadata = self._analyze_indicators(
                input_data.indicators, input_data.strategy_config, context
            )

            if signal_type is None or confidence < self.min_confidence:
                logger.debug(
                    f"No signal generated: type={signal_type}, " f"confidence={confidence:.2f}"
                )
                return None

            # Calculate price levels
            entry_price = input_data.current_price
            stop_loss, take_profit = self._calculate_levels(
                signal_type,
                entry_price,
                input_data.market_data,
                input_data.strategy_config,
                context,
            )

            # Calculate risk/reward ratio
            risk_reward_ratio = self._calculate_risk_reward(
                entry_price, stop_loss, take_profit, signal_type
            )

            # Calculate position size percentage
            position_size_percent = self._calculate_position_size_percent(
                confidence, context.volatility, input_data.strategy_config
            )

            # Create signal
            signal = Signal(
                timestamp=context.timestamp,
                symbol=context.symbol,
                strategy_name=input_data.strategy_config.get("name", "unknown"),
                timeframe=context.timeframe,
                # Signal parameters
                signal_type=signal_type,
                strength=self._confidence_to_strength(confidence),
                confidence=confidence,
                # Price levels
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                # Risk management
                risk_reward_ratio=risk_reward_ratio,
                position_size_percent=position_size_percent,
                # Additional data
                indicators_snapshot=input_data.indicators,
                metadata=metadata,
                # Status
                status=SignalStatus.PENDING,
            )

            # Record signal generation
            self._record_signal_generation()

            logger.info(
                f"Signal generated: {signal.symbol} {signal.signal_type.value} "
                f"@ {signal.entry_price:.4f} (confidence: {signal.confidence:.2f})"
            )

            return signal

        except Exception as e:
            logger.error(f"Error generating signal: {e}", exc_info=True)
            raise StrategyError(
                f"Failed to generate signal: {str(e)}",
                strategy_name=input_data.strategy_config.get("name"),
            )

    def _validate_input(self, input_data: SignalGeneratorInput) -> None:
        """
        Validate input data.

        Args:
            input_data: Input data to validate

        Raises:
            DataValidationError: If validation fails
        """
        if not input_data.symbol:
            raise DataValidationError("symbol", "Symbol is required")

        if not input_data.timeframe:
            raise DataValidationError("timeframe", "Timeframe is required")

        if input_data.current_price <= 0:
            raise DataValidationError(
                "current_price", f"Invalid current price: {input_data.current_price}"
            )

        if not input_data.indicators:
            raise DataValidationError("indicators", "Indicators are required")

    def _check_rate_limit(self) -> bool:
        """
        Check if signal generation is within rate limits.

        Returns:
            True if within limits, False otherwise
        """
        current_time = int(time.time() * 1000)
        hour_ago = current_time - (3600 * 1000)

        # Remove old timestamps
        self.recent_signals = [ts for ts in self.recent_signals if ts > hour_ago]

        return len(self.recent_signals) < self.max_signals_per_hour

    def _record_signal_generation(self) -> None:
        """Record signal generation for rate limiting."""
        self.recent_signals.append(int(time.time() * 1000))

    def _create_context(self, input_data: SignalGeneratorInput) -> SignalContext:
        """
        Create signal context from input data.

        Args:
            input_data: Input data

        Returns:
            SignalContext object
        """
        market_data = input_data.market_data

        return SignalContext(
            symbol=input_data.symbol,
            timeframe=input_data.timeframe,
            timestamp=int(time.time() * 1000),
            current_price=input_data.current_price,
            volume=market_data.get("volume", 0),
            volatility=market_data.get("volatility", 0),
            trend_strength=market_data.get("trend_strength", 0),
        )

    def _analyze_indicators(
        self, indicators: Dict[str, Any], strategy_config: Dict[str, Any], context: SignalContext
    ) -> tuple[Optional[SignalType], float, Dict[str, Any]]:
        """
        Analyze indicators to determine signal type and confidence.

        Args:
            indicators: Calculated indicator values
            strategy_config: Strategy configuration
            context: Signal context

        Returns:
            Tuple of (signal_type, confidence, metadata)
        """
        # This is a basic implementation - should be customized per strategy

        metadata = {
            "indicators_at_signal": indicators.copy(),
            "context": {
                "volatility": context.volatility,
                "trend_strength": context.trend_strength,
                "volume": context.volume,
            },
        }

        # Example: RSI-based signal
        rsi = indicators.get("rsi", {}).get("value")
        macd = indicators.get("macd", {})
        stoch = indicators.get("stochastic", {})

        if rsi is None:
            return None, 0.0, metadata

        # Bullish signals
        if rsi < 30:  # Oversold
            confidence = 0.7
            if macd.get("histogram", 0) > 0:
                confidence += 0.1
            if stoch.get("k", 100) < 20:
                confidence += 0.1

            metadata["reason"] = "RSI oversold with bullish indicators"
            return SignalType.LONG, min(confidence, 1.0), metadata

        # Bearish signals
        elif rsi > 70:  # Overbought
            confidence = 0.7
            if macd.get("histogram", 0) < 0:
                confidence += 0.1
            if stoch.get("k", 0) > 80:
                confidence += 0.1

            metadata["reason"] = "RSI overbought with bearish indicators"
            return SignalType.SHORT, min(confidence, 1.0), metadata

        return None, 0.0, metadata

    def _calculate_levels(
        self,
        signal_type: SignalType,
        entry_price: float,
        market_data: Dict[str, Any],
        strategy_config: Dict[str, Any],
        context: SignalContext,
    ) -> tuple[float, List[float]]:
        """
        Calculate stop loss and take profit levels.

        Args:
            signal_type: Type of signal (LONG/SHORT)
            entry_price: Entry price
            market_data: Market data
            strategy_config: Strategy configuration
            context: Signal context

        Returns:
            Tuple of (stop_loss, take_profit_levels)
        """
        # Get ATR for volatility-based levels
        atr = market_data.get("atr", entry_price * 0.02)  # Default 2%

        # Stop loss multiplier from config
        sl_multiplier = strategy_config.get("stop_loss_atr_multiplier", 1.5)
        tp_multipliers = strategy_config.get("take_profit_atr_multipliers", [2.0, 3.0])

        if signal_type == SignalType.LONG:
            stop_loss = entry_price - (atr * sl_multiplier)
            take_profit = [entry_price + (atr * mult) for mult in tp_multipliers]
        else:  # SHORT
            stop_loss = entry_price + (atr * sl_multiplier)
            take_profit = [entry_price - (atr * mult) for mult in tp_multipliers]

        return stop_loss, take_profit

    def _calculate_risk_reward(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: List[float],
        signal_type: SignalType,
    ) -> float:
        """
        Calculate risk/reward ratio.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: List of take profit prices
            signal_type: Signal type

        Returns:
            Risk/reward ratio
        """
        if not take_profit:
            return 0.0

        # Use first TP level for R:R calculation
        first_tp = take_profit[0]

        if signal_type == SignalType.LONG:
            risk = entry_price - stop_loss
            reward = first_tp - entry_price
        else:  # SHORT
            risk = stop_loss - entry_price
            reward = entry_price - first_tp

        if risk <= 0:
            return 0.0

        return reward / risk

    def _calculate_position_size_percent(
        self, confidence: float, volatility: float, strategy_config: Dict[str, Any]
    ) -> float:
        """
        Calculate recommended position size as percentage of account.

        Args:
            confidence: Signal confidence (0-1)
            volatility: Market volatility
            strategy_config: Strategy configuration

        Returns:
            Position size percentage
        """
        base_size = strategy_config.get("base_position_size_percent", 2.0)

        # Adjust for confidence
        size = base_size * confidence

        # Reduce size in high volatility
        if volatility > 0.03:  # 3% volatility
            size *= 0.7
        elif volatility > 0.05:  # 5% volatility
            size *= 0.5

        # Clamp to reasonable range
        min_size = strategy_config.get("min_position_size_percent", 0.5)
        max_size = strategy_config.get("max_position_size_percent", 5.0)

        return max(min_size, min(size, max_size))

    def _confidence_to_strength(self, confidence: float) -> SignalStrength:
        """
        Convert confidence value to signal strength enum.

        Args:
            confidence: Confidence value (0-1)

        Returns:
            SignalStrength enum
        """
        if confidence >= 0.9:
            return SignalStrength.VERY_STRONG
        elif confidence >= 0.75:
            return SignalStrength.STRONG
        elif confidence >= 0.6:
            return SignalStrength.MODERATE
        else:
            return SignalStrength.WEAK

    def validate_signal(self, signal: Signal, market_data: Dict[str, Any]) -> bool:
        """
        Validate signal before sending.

        Args:
            signal: Signal to validate
            market_data: Current market data

        Returns:
            True if valid, False otherwise
        """
        # Check if price hasn't moved too far
        current_price = market_data.get("current_price", 0)
        if current_price <= 0:
            logger.warning("Invalid current price for signal validation")
            return False

        price_change = abs(current_price - signal.entry_price) / signal.entry_price
        max_price_change = self.config.get("max_price_change_for_validation", 0.005)  # 0.5%

        if price_change > max_price_change:
            logger.warning(
                f"Signal rejected: price moved {price_change:.2%} " f"since signal generation"
            )
            return False

        # Check confidence threshold
        if signal.confidence < self.min_confidence:
            logger.warning(
                f"Signal rejected: confidence {signal.confidence:.2f} "
                f"below minimum {self.min_confidence}"
            )
            return False

        # Check risk/reward ratio
        min_rr = self.config.get("min_risk_reward_ratio", 1.5)
        if signal.risk_reward_ratio < min_rr:
            logger.warning(
                f"Signal rejected: R:R {signal.risk_reward_ratio:.2f} " f"below minimum {min_rr}"
            )
            return False

        return True

    def check_filters(self, signal: Signal, filters: List[Any]) -> bool:
        """
        Check signal against configured filters.

        Args:
            signal: Signal to check
            filters: List of filter objects

        Returns:
            True if signal passes all filters, False otherwise
        """
        for filter_obj in filters:
            if not filter_obj.check(signal):
                logger.debug(f"Signal filtered out by {filter_obj.__class__.__name__}")
                return False

        return True
