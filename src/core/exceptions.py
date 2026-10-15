"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 18.11.2025.
    File        : exceptions.py
    Project     : Project-103
    Description : Custom exception classes for the trading bot system.This module defines a hierarchy of exceptions for proper error handling.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import Any, Dict, List, Optional


class TradingBotError(Exception):
    """
    Base exception for all trading bot errors.

    Attributes:
        message: Error message
        code: Optional error code
        details: Additional error details
    """

    def __init__(
        self, message: str, code: Optional[str] = None, details: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize TradingBotError.

        Args:
            message: Error message
            code: Optional error code
            details: Additional error details
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def __str__(self) -> str:
        """String representation of the error."""
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary."""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "code": self.code,
            "details": self.details,
        }


# ==================== Configuration Exceptions ====================


class ConfigError(TradingBotError):
    """Configuration related errors."""

    pass


class ConfigValidationError(ConfigError):
    """Configuration validation errors."""

    def __init__(self, message: str, errors: List[str], config_file: Optional[str] = None):
        """
        Initialize ConfigValidationError.

        Args:
            message: Error message
            errors: List of validation errors
            config_file: Configuration file that failed validation
        """
        super().__init__(
            message,
            code="CONFIG_VALIDATION_ERROR",
            details={"errors": errors, "config_file": config_file},
        )
        self.errors = errors
        self.config_file = config_file


class ConfigNotFoundError(ConfigError):
    """Configuration file not found."""

    def __init__(self, config_file: str):
        """
        Initialize ConfigNotFoundError.

        Args:
            config_file: Missing configuration file
        """
        super().__init__(
            f"Configuration file not found: {config_file}",
            code="CONFIG_NOT_FOUND",
            details={"config_file": config_file},
        )
        self.config_file = config_file


# ==================== Exchange Exceptions ====================


class ExchangeError(TradingBotError):
    """Exchange related errors."""

    def __init__(
        self,
        message: str,
        exchange: Optional[str] = None,
        response: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Initialize ExchangeError.

        Args:
            message: Error message
            exchange: Exchange name
            response: Raw response from exchange
            **kwargs: Additional details
        """
        super().__init__(
            message,
            code="EXCHANGE_ERROR",
            details={"exchange": exchange, "response": response, **kwargs},
        )
        self.exchange = exchange
        self.response = response


class ExchangeConnectionError(ExchangeError):
    """Exchange connection errors."""

    def __init__(self, exchange: str, reason: str):
        """
        Initialize ExchangeConnectionError.

        Args:
            exchange: Exchange name
            reason: Connection failure reason
        """
        super().__init__(
            f"Failed to connect to {exchange}: {reason}", exchange=exchange, code="CONNECTION_ERROR"
        )


class ExchangeAuthenticationError(ExchangeError):
    """Exchange authentication errors."""

    def __init__(self, exchange: str):
        """
        Initialize ExchangeAuthenticationError.

        Args:
            exchange: Exchange name
        """
        super().__init__(
            f"Authentication failed for {exchange}", exchange=exchange, code="AUTH_ERROR"
        )


class ExchangeRateLimitError(ExchangeError):
    """Exchange rate limit errors."""

    def __init__(self, exchange: str, retry_after: Optional[int] = None):
        """
        Initialize ExchangeRateLimitError.

        Args:
            exchange: Exchange name
            retry_after: Seconds to wait before retry
        """
        super().__init__(
            f"Rate limit exceeded for {exchange}",
            exchange=exchange,
            code="RATE_LIMIT",
            retry_after=retry_after,
        )
        self.retry_after = retry_after


# ==================== Data Exceptions ====================


class DataError(TradingBotError):
    """Data related errors."""

    pass


class DataNotFoundError(DataError):
    """Data not found errors."""

    def __init__(self, data_type: str, identifier: str):
        """
        Initialize DataNotFoundError.

        Args:
            data_type: Type of data
            identifier: Data identifier
        """
        super().__init__(
            f"{data_type} not found: {identifier}",
            code="DATA_NOT_FOUND",
            details={"data_type": data_type, "identifier": identifier},
        )


class DataValidationError(DataError):
    """Data validation errors."""

    def __init__(self, data_type: str, reason: str):
        """
        Initialize DataValidationError.

        Args:
            data_type: Type of data
            reason: Validation failure reason
        """
        super().__init__(
            f"Invalid {data_type}: {reason}",
            code="DATA_VALIDATION_ERROR",
            details={"data_type": data_type},
        )


class InsufficientDataError(DataError):
    """Insufficient data errors."""

    def __init__(self, required: int, available: int, data_type: str = "candles"):
        """
        Initialize InsufficientDataError.

        Args:
            required: Required data points
            available: Available data points
            data_type: Type of data
        """
        super().__init__(
            f"Insufficient {data_type}: required {required}, available {available}",
            code="INSUFFICIENT_DATA",
            details={"required": required, "available": available, "data_type": data_type},
        )


class DataProviderError(DataError):
    """Data provider related errors (REST API, WebSocket collectors)."""

    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        error_code: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize DataProviderError.

        Args:
            message: Error message
            provider: Data provider name (e.g., exchange name)
            error_code: Provider-specific error code
            **kwargs: Additional details
        """
        super().__init__(
            message,
            code=error_code or "DATA_PROVIDER_ERROR",
            details={"provider": provider, **kwargs},
        )
        self.provider = provider


# ==================== Strategy Exceptions ====================


class StrategyError(TradingBotError):
    """Strategy related errors."""

    def __init__(self, message: str, strategy_name: Optional[str] = None, **kwargs):
        """
        Initialize StrategyError.

        Args:
            message: Error message
            strategy_name: Strategy name
            **kwargs: Additional details
        """
        super().__init__(
            message, code="STRATEGY_ERROR", details={"strategy": strategy_name, **kwargs}
        )
        self.strategy_name = strategy_name


class StrategyNotFoundError(StrategyError):
    """Strategy not found errors."""

    def __init__(self, strategy_name: str):
        """
        Initialize StrategyNotFoundError.

        Args:
            strategy_name: Strategy name
        """
        super().__init__(
            f"Strategy not found: {strategy_name}",
            strategy_name=strategy_name,
            code="STRATEGY_NOT_FOUND",
        )


class StrategyExecutionError(StrategyError):
    """Strategy execution errors."""

    def __init__(self, strategy_name: str, reason: str):
        """
        Initialize StrategyExecutionError.

        Args:
            strategy_name: Strategy name
            reason: Execution failure reason
        """
        super().__init__(
            f"Strategy execution failed for {strategy_name}: {reason}",
            strategy_name=strategy_name,
            code="STRATEGY_EXECUTION_ERROR",
        )


# ==================== Risk Management Exceptions ====================


class RiskError(TradingBotError):
    """Risk management related errors."""

    pass


class RiskLimitExceeded(RiskError):
    """Risk limit exceeded errors."""

    def __init__(self, limit_type: str, current_value: float, limit_value: float):
        """
        Initialize RiskLimitExceeded.

        Args:
            limit_type: Type of limit exceeded
            current_value: Current value
            limit_value: Limit value
        """
        super().__init__(
            f"{limit_type} exceeded: current={current_value}, limit={limit_value}",
            code="RISK_LIMIT_EXCEEDED",
            details={
                "limit_type": limit_type,
                "current_value": current_value,
                "limit_value": limit_value,
            },
        )


class InsufficientBalanceError(RiskError):
    """Insufficient balance errors."""

    def __init__(self, required: float, available: float, asset: str = "USDT"):
        """
        Initialize InsufficientBalanceError.

        Args:
            required: Required amount
            available: Available amount
            asset: Asset symbol
        """
        super().__init__(
            f"Insufficient {asset} balance: required={required}, available={available}",
            code="INSUFFICIENT_BALANCE",
            details={"required": required, "available": available, "asset": asset},
        )


# ==================== Order/Position Exceptions ====================


class OrderError(TradingBotError):
    """Order related errors."""

    def __init__(self, message: str, order_id: Optional[str] = None, **kwargs):
        """
        Initialize OrderError.

        Args:
            message: Error message
            order_id: Order ID
            **kwargs: Additional details
        """
        super().__init__(message, code="ORDER_ERROR", details={"order_id": order_id, **kwargs})
        self.order_id = order_id


class OrderNotFoundError(OrderError):
    """Order not found errors."""

    def __init__(self, order_id: str):
        """
        Initialize OrderNotFoundError.

        Args:
            order_id: Order ID
        """
        super().__init__(f"Order not found: {order_id}", order_id=order_id, code="ORDER_NOT_FOUND")


class OrderValidationError(OrderError):
    """Order validation errors."""

    def __init__(self, reason: str, order_details: Optional[Dict[str, Any]] = None):
        """
        Initialize OrderValidationError.

        Args:
            reason: Validation failure reason
            order_details: Order details that failed validation
        """
        super().__init__(
            f"Order validation failed: {reason}",
            code="ORDER_VALIDATION_ERROR",
            order_details=order_details,
        )


class OrderExecutionError(OrderError):
    """Order execution errors."""

    def __init__(self, order_id: str, reason: str):
        """
        Initialize OrderExecutionError.

        Args:
            order_id: Order ID
            reason: Execution failure reason
        """
        super().__init__(
            f"Order execution failed: {reason}", order_id=order_id, code="ORDER_EXECUTION_ERROR"
        )


class PositionError(TradingBotError):
    """Position related errors."""

    def __init__(self, message: str, position_id: Optional[str] = None, **kwargs):
        """
        Initialize PositionError.

        Args:
            message: Error message
            position_id: Position ID
            **kwargs: Additional details
        """
        super().__init__(
            message, code="POSITION_ERROR", details={"position_id": position_id, **kwargs}
        )
        self.position_id = position_id


class PositionNotFoundError(PositionError):
    """Position not found errors."""

    def __init__(self, position_id: str):
        """
        Initialize PositionNotFoundError.

        Args:
            position_id: Position ID
        """
        super().__init__(
            f"Position not found: {position_id}", position_id=position_id, code="POSITION_NOT_FOUND"
        )


# ==================== Indicator Exceptions ====================


class IndicatorError(TradingBotError):
    """Indicator related errors."""

    def __init__(self, message: str, indicator_name: Optional[str] = None, **kwargs):
        """
        Initialize IndicatorError.

        Args:
            message: Error message
            indicator_name: Indicator name
            **kwargs: Additional details
        """
        super().__init__(
            message, code="INDICATOR_ERROR", details={"indicator": indicator_name, **kwargs}
        )
        self.indicator_name = indicator_name


class IndicatorCalculationError(IndicatorError):
    """Indicator calculation errors."""

    def __init__(self, indicator_name: str, reason: str):
        """
        Initialize IndicatorCalculationError.

        Args:
            indicator_name: Indicator name
            reason: Calculation failure reason
        """
        super().__init__(
            f"Indicator calculation failed for {indicator_name}: {reason}",
            indicator_name=indicator_name,
            code="INDICATOR_CALCULATION_ERROR",
        )


# ==================== Database Exceptions ====================


class DatabaseError(TradingBotError):
    """Database related errors."""

    pass


class DatabaseConnectionError(DatabaseError):
    """Database connection errors."""

    def __init__(self, reason: str):
        """
        Initialize DatabaseConnectionError.

        Args:
            reason: Connection failure reason
        """
        super().__init__(f"Database connection failed: {reason}", code="DATABASE_CONNECTION_ERROR")


class DatabaseQueryError(DatabaseError):
    """Database query errors."""

    def __init__(self, query: str, reason: str):
        """
        Initialize DatabaseQueryError.

        Args:
            query: Failed query
            reason: Query failure reason
        """
        super().__init__(
            f"Database query failed: {reason}",
            code="DATABASE_QUERY_ERROR",
            details={"query": query},
        )


# ==================== Notification Exceptions ====================


class NotificationError(TradingBotError):
    """Notification related errors."""

    pass


class NotificationSendError(NotificationError):
    """Notification send errors."""

    def __init__(self, channel: str, reason: str):
        """
        Initialize NotificationSendError.

        Args:
            channel: Notification channel (telegram, email, etc.)
            reason: Send failure reason
        """
        super().__init__(
            f"Failed to send notification via {channel}: {reason}",
            code="NOTIFICATION_SEND_ERROR",
            details={"channel": channel},
        )


# ==================== System Exceptions ====================


class SystemError(TradingBotError):
    """System related errors."""

    pass


class SystemInitializationError(SystemError):
    """System initialization errors."""

    def __init__(self, component: str, reason: str):
        """
        Initialize SystemInitializationError.

        Args:
            component: Component that failed to initialize
            reason: Initialization failure reason
        """
        super().__init__(
            f"Failed to initialize {component}: {reason}",
            code="SYSTEM_INIT_ERROR",
            details={"component": component},
        )


class SystemShutdownError(SystemError):
    """System shutdown errors."""

    def __init__(self, reason: str):
        """
        Initialize SystemShutdownError.

        Args:
            reason: Shutdown failure reason
        """
        super().__init__(f"System shutdown error: {reason}", code="SYSTEM_SHUTDOWN_ERROR")


# ==================== Aliases for compatibility ====================

# Alias for ConfigError (used in main.py)
ConfigurationError = ConfigError

# Alias for ExchangeConnectionError (used in main.py)
ConnectionError = ExchangeConnectionError
