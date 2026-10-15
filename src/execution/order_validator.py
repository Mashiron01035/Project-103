"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : order_validator
    Project     : Project-103
    Description : Order validation module. This module handles order validation before sending to exchange.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationContext:
    """Context for order validation."""

    order: Dict[str, Any]  # Order to validate
    account_balance: float  # Account balance
    open_positions: List[Dict[str, Any]]  # Open positions
    active_orders: List[Dict[str, Any]]  # Active orders
    market_data: Dict[str, Any]  # Current market data
    symbol_info: Dict[str, Any]  # Symbol information

    # Risk limits
    max_positions: int = 5
    max_orders_per_symbol: int = 10
    max_risk_per_trade: float = 0.02  # 2%
    max_daily_loss: float = 0.05  # 5%

    # Market conditions
    max_spread: float = 0.002  # 0.2%
    min_volume: float = 100000  # Minimum 24h volume
    max_volatility: float = 0.10  # 10% daily volatility

    # Execution limits
    max_slippage: float = 0.005  # 0.5%
    min_liquidity_ratio: float = 2.0  # Order size vs available liquidity

    def get_total_exposure(self) -> float:
        """Calculate total exposure."""
        total = 0.0

        # Add open positions
        for pos in self.open_positions:
            total += pos.get("quantity", 0) * pos.get("entry_price", 0)

        # Add active orders
        for order in self.active_orders:
            if order.get("status") == "NEW":
                total += order.get("quantity", 0) * order.get("price", 0)

        return total


@dataclass
class ValidationResult:
    """Result of order validation."""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # Suggested adjustments
    adjusted_quantity: Optional[float] = None
    adjusted_price: Optional[float] = None
    suggested_order_type: Optional[str] = None

    # Risk metrics
    position_risk: float = 0.0
    account_risk: float = 0.0
    correlation_risk: float = 0.0

    # Market metrics
    spread: float = 0.0
    liquidity_score: float = 0.0
    volatility: float = 0.0

    def add_error(self, error: str) -> None:
        """Add validation error."""
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        """Add validation warning."""
        self.warnings.append(warning)


class ValidationError:
    """Validation error types."""

    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    POSITION_LIMIT_EXCEEDED = "POSITION_LIMIT_EXCEEDED"
    ORDER_LIMIT_EXCEEDED = "ORDER_LIMIT_EXCEEDED"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_PRICE = "INVALID_PRICE"
    MARKET_CLOSED = "MARKET_CLOSED"
    HIGH_SPREAD = "HIGH_SPREAD"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class OrderValidator:
    """Order validator class."""

    def __init__(self, exchange_type: str, config: Dict[str, Any]):
        """
        Initialize order validator.

        Args:
            exchange_type: Type of exchange (bybit, binance, etc.)
            config: Configuration
        """
        self.exchange_type = exchange_type.lower()
        self.config = config

        # Validation rules
        self.rules = self._load_validation_rules()

        # Cache for symbol info
        self.symbol_cache = {}
        self.cache_ttl = 300  # 5 minutes

        logger.info(f"OrderValidator initialized for {exchange_type}")

    def validate(self, context: ValidationContext) -> ValidationResult:
        """
        Validate order.

        Args:
            context: Validation context

        Returns:
            ValidationResult: Validation result
        """
        result = ValidationResult(is_valid=True)

        try:
            # Run validation checks
            self._validate_basic_parameters(context, result)
            self._validate_account_balance(context, result)
            self._validate_risk_limits(context, result)
            self._validate_position_limits(context, result)
            self._validate_symbol_constraints(context, result)
            self._validate_market_conditions(context, result)
            self._validate_liquidity(context, result)
            self._validate_execution_params(context, result)

            # Apply adjustments if needed
            if result.is_valid:
                self._apply_adjustments(context, result)

            # Log validation result
            if result.is_valid:
                logger.info(f"Order validation passed for {context.order.get('symbol')}")
            else:
                logger.warning(f"Order validation failed: {result.errors}")

            if result.warnings:
                logger.warning(f"Validation warnings: {result.warnings}")

        except Exception as e:
            logger.error(f"Error during validation: {e}")
            result.add_error(f"Validation error: {str(e)}")

        return result

    def validate_modification(
        self,
        original_order: Dict[str, Any],
        modifications: Dict[str, Any],
        context: ValidationContext,
    ) -> ValidationResult:
        """
        Validate order modification.

        Args:
            original_order: Original order
            modifications: Proposed modifications
            context: Validation context

        Returns:
            ValidationResult: Validation result
        """
        result = ValidationResult(is_valid=True)

        # Check if order can be modified
        if original_order.get("status") not in ["NEW", "PARTIALLY_FILLED"]:
            result.add_error(f"Cannot modify order with status {original_order.get('status')}")
            return result

        # Validate new parameters
        modified_order = original_order.copy()
        modified_order.update(modifications)
        context.order = modified_order

        # Run validation
        return self.validate(context)

    def validate_cancellation(self, order: Dict[str, Any]) -> ValidationResult:
        """
        Validate order cancellation.

        Args:
            order: Order to cancel

        Returns:
            ValidationResult: Validation result
        """
        result = ValidationResult(is_valid=True)

        # Check if order can be cancelled
        if order.get("status") in ["FILLED", "CANCELED", "REJECTED", "EXPIRED"]:
            result.add_error(f"Cannot cancel order with status {order.get('status')}")

        # Check if it's a special order type
        if order.get("metadata", {}).get("is_stop_loss"):
            result.add_warning("Cancelling stop loss order")

        if order.get("metadata", {}).get("is_take_profit"):
            result.add_warning("Cancelling take profit order")

        return result

    # ==================== Validation Methods ====================

    def _validate_basic_parameters(
        self, context: ValidationContext, result: ValidationResult
    ) -> None:
        """Validate basic order parameters."""
        order = context.order

        # Check symbol
        if not order.get("symbol"):
            result.add_error("Symbol is required")

        # Check side
        if order.get("side") not in ["BUY", "SELL"]:
            result.add_error(f"Invalid order side: {order.get('side')}")

        # Check type
        valid_types = ["MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT"]
        if order.get("type") not in valid_types:
            result.add_error(f"Invalid order type: {order.get('type')}")

        # Check quantity
        quantity = order.get("quantity", 0)
        if quantity <= 0:
            result.add_error(f"Invalid quantity: {quantity}")

        # Check price for limit orders
        if order.get("type") in ["LIMIT", "STOP_LIMIT"]:
            price = order.get("price")
            if not price or price <= 0:
                result.add_error(f"Invalid price for limit order: {price}")

        # Check stop price for stop orders
        if order.get("type") in ["STOP_MARKET", "STOP_LIMIT"]:
            stop_price = order.get("stop_price")
            if not stop_price or stop_price <= 0:
                result.add_error(f"Invalid stop price: {stop_price}")

    def _validate_account_balance(
        self, context: ValidationContext, result: ValidationResult
    ) -> None:
        """Validate account has sufficient balance."""
        order = context.order

        # Calculate required balance
        quantity = order.get("quantity", 0)
        price = order.get("price") or context.market_data.get("last_price", 0)

        if price <= 0:
            result.add_warning("Cannot validate balance without price")
            return

        required_balance = quantity * price

        # Add margin for fees
        fee_rate = self.config.get("fee_rate", 0.001)  # 0.1%
        required_balance *= 1 + fee_rate

        # Check balance
        if required_balance > context.account_balance:
            result.add_error(
                f"Insufficient balance: required {required_balance:.2f}, "
                f"available {context.account_balance:.2f}"
            )

            # Suggest adjusted quantity
            max_quantity = (context.account_balance / price) * (1 - fee_rate)
            result.adjusted_quantity = max_quantity * 0.95  # Leave 5% buffer

    def _validate_risk_limits(self, context: ValidationContext, result: ValidationResult) -> None:
        """Validate risk management limits."""
        order = context.order

        # Calculate position value
        quantity = order.get("quantity", 0)
        price = order.get("price") or context.market_data.get("last_price", 0)
        position_value = quantity * price

        # Check risk per trade
        max_risk_value = context.account_balance * context.max_risk_per_trade
        if position_value > max_risk_value:
            result.add_error(
                f"Position value {position_value:.2f} exceeds max risk "
                f"{max_risk_value:.2f} ({context.max_risk_per_trade * 100:.1f}% of balance)"
            )

            # Suggest adjusted quantity
            result.adjusted_quantity = (max_risk_value / price) * 0.95

        # Calculate account risk
        result.account_risk = position_value / context.account_balance

        # Check total exposure
        total_exposure = context.get_total_exposure() + position_value
        max_exposure = context.account_balance * 2  # 2x leverage limit

        if total_exposure > max_exposure:
            result.add_error(
                f"Total exposure {total_exposure:.2f} exceeds maximum {max_exposure:.2f}"
            )

    def _validate_position_limits(
        self, context: ValidationContext, result: ValidationResult
    ) -> None:
        """Validate position and order limits."""
        order = context.order
        symbol = order.get("symbol")

        # Check maximum positions
        if len(context.open_positions) >= context.max_positions:
            # Check if this is adding to existing position
            existing_position = next(
                (p for p in context.open_positions if p.get("symbol") == symbol), None
            )

            if not existing_position:
                result.add_error(f"Maximum positions limit reached: {context.max_positions}")

        # Check orders per symbol
        symbol_orders = [
            o
            for o in context.active_orders
            if o.get("symbol") == symbol and o.get("status") == "NEW"
        ]

        if len(symbol_orders) >= context.max_orders_per_symbol:
            result.add_error(f"Maximum orders per symbol reached: {context.max_orders_per_symbol}")

        # Check position concentration
        if context.open_positions:
            position_values = {}
            for pos in context.open_positions:
                sym = pos.get("symbol")
                value = pos.get("quantity", 0) * pos.get("entry_price", 0)
                position_values[sym] = position_values.get(sym, 0) + value

            # Add current order
            order_value = order.get("quantity", 0) * (
                order.get("price") or context.market_data.get("last_price", 0)
            )
            position_values[symbol] = position_values.get(symbol, 0) + order_value

            # Check concentration
            total_value = sum(position_values.values())
            if total_value > 0:
                concentration = position_values[symbol] / total_value
                max_concentration = 0.5  # 50% max in single symbol

                if concentration > max_concentration:
                    result.add_warning(
                        f"High position concentration in {symbol}: {concentration:.1%}"
                    )

    def _validate_symbol_constraints(
        self, context: ValidationContext, result: ValidationResult
    ) -> None:
        """Validate symbol-specific constraints."""
        order = context.order
        symbol_info = context.symbol_info

        if not symbol_info:
            result.add_warning("Symbol information not available")
            return

        quantity = order.get("quantity", 0)

        # Check minimum quantity
        min_qty = symbol_info.get("min_quantity", 0)
        if quantity < min_qty:
            result.add_error(f"Quantity {quantity} below minimum {min_qty}")
            result.adjusted_quantity = min_qty

        # Check maximum quantity
        max_qty = symbol_info.get("max_quantity", float("inf"))
        if quantity > max_qty:
            result.add_error(f"Quantity {quantity} above maximum {max_qty}")
            result.adjusted_quantity = max_qty

        # Check quantity step
        qty_step = symbol_info.get("quantity_step", 0)
        if qty_step > 0:
            rounded_qty = round(quantity / qty_step) * qty_step
            if abs(rounded_qty - quantity) > 1e-8:
                result.add_warning(f"Quantity adjusted to {rounded_qty} (step: {qty_step})")
                result.adjusted_quantity = rounded_qty

        # Check price precision for limit orders
        if order.get("type") in ["LIMIT", "STOP_LIMIT"]:
            price = order.get("price", 0)
            price_precision = symbol_info.get("price_precision", 8)

            if price > 0:
                rounded_price = round(price, price_precision)
                if abs(rounded_price - price) > 1e-10:
                    result.add_warning(
                        f"Price adjusted to {rounded_price} (precision: {price_precision})"
                    )
                    result.adjusted_price = rounded_price

        # Check minimum notional value
        min_notional = symbol_info.get("min_notional", 0)
        if min_notional > 0:
            price = order.get("price") or context.market_data.get("last_price", 0)
            notional = quantity * price

            if notional < min_notional:
                result.add_error(f"Notional value {notional:.2f} below minimum {min_notional}")

                if price > 0:
                    result.adjusted_quantity = (min_notional / price) * 1.01  # Add 1% buffer

    def _validate_market_conditions(
        self, context: ValidationContext, result: ValidationResult
    ) -> None:
        """Validate current market conditions."""
        market_data = context.market_data

        if not market_data:
            result.add_warning("Market data not available")
            return

        # Check if market is open
        if market_data.get("status") == "CLOSED":
            result.add_error("Market is closed")
            return

        # Check spread
        bid = market_data.get("bid_price", 0)
        ask = market_data.get("ask_price", 0)

        if bid > 0 and ask > 0:
            spread = (ask - bid) / ((ask + bid) / 2)
            result.spread = spread

            if spread > context.max_spread:
                result.add_warning(f"High spread: {spread:.4f} ({spread * 100:.2f}%)")

                # Suggest market order for high spread
                if context.order.get("type") == "LIMIT":
                    result.suggested_order_type = "MARKET"

        # Check volume
        volume_24h = market_data.get("volume_24h", 0)
        if volume_24h < context.min_volume:
            result.add_warning(f"Low 24h volume: {volume_24h:.0f}")

        # Check volatility
        high_24h = market_data.get("high_24h", 0)
        low_24h = market_data.get("low_24h", 0)

        if high_24h > 0 and low_24h > 0:
            volatility = (high_24h - low_24h) / low_24h
            result.volatility = volatility

            if volatility > context.max_volatility:
                result.add_warning(f"High volatility: {volatility:.4f} ({volatility * 100:.2f}%)")

    def _validate_liquidity(self, context: ValidationContext, result: ValidationResult) -> None:
        """Validate order book liquidity."""
        order = context.order
        market_data = context.market_data

        if not market_data or "orderbook" not in market_data:
            result.add_warning("Order book data not available")
            return

        orderbook = market_data.get("orderbook", {})
        quantity = order.get("quantity", 0)
        side = order.get("side")

        # Get relevant order book side
        if side == "BUY":
            levels = orderbook.get("asks", [])
        else:
            levels = orderbook.get("bids", [])

        if not levels:
            result.add_warning("Order book empty")
            return

        # Calculate available liquidity
        total_liquidity = 0
        weighted_price = 0
        remaining_qty = quantity

        for level in levels[:20]:  # Check top 20 levels
            level_price = float(level[0])
            level_qty = float(level[1])

            fill_qty = min(remaining_qty, level_qty)
            total_liquidity += fill_qty
            weighted_price += level_price * fill_qty
            remaining_qty -= fill_qty

            if remaining_qty <= 0:
                break

        # Calculate liquidity score
        if total_liquidity > 0:
            result.liquidity_score = min(total_liquidity / quantity, 1.0)

            if result.liquidity_score < 1.0:
                result.add_warning(
                    f"Insufficient liquidity: {result.liquidity_score:.2%} of order can be filled"
                )

            # Calculate expected slippage
            if quantity > 0:
                avg_fill_price = weighted_price / min(quantity, total_liquidity)
                best_price = float(levels[0][0])
                slippage = abs(avg_fill_price - best_price) / best_price

                if slippage > context.max_slippage:
                    result.add_warning(
                        f"High expected slippage: {slippage:.4f} ({slippage * 100:.2f}%)"
                    )

                    # Suggest smaller order size
                    max_size_for_slippage = quantity * (context.max_slippage / slippage)
                    result.adjusted_quantity = min(
                        result.adjusted_quantity or quantity, max_size_for_slippage
                    )

    def _validate_execution_params(
        self, context: ValidationContext, result: ValidationResult
    ) -> None:
        """Validate execution parameters."""
        order = context.order

        # Validate time in force
        valid_tif = ["GTC", "IOC", "FOK", "GTX"]
        tif = order.get("time_in_force", "GTC")

        if tif not in valid_tif:
            result.add_error(f"Invalid time in force: {tif}")

        # Check reduce_only flag
        if order.get("reduce_only"):
            # Check if there's a position to reduce
            symbol = order.get("symbol")
            position = next((p for p in context.open_positions if p.get("symbol") == symbol), None)

            if not position:
                result.add_error("reduce_only set but no position found")
            elif position:
                # Check if order side is opposite to position
                pos_side = position.get("side")  # LONG or SHORT
                order_side = order.get("side")  # BUY or SELL

                if (pos_side == "LONG" and order_side == "BUY") or (
                    pos_side == "SHORT" and order_side == "SELL"
                ):
                    result.add_error("reduce_only order must be opposite to position side")

        # Check post_only for limit orders
        if order.get("post_only") and order.get("type") != "LIMIT":
            result.add_error("post_only can only be used with limit orders")

    def _apply_adjustments(self, context: ValidationContext, result: ValidationResult) -> None:
        """Apply final adjustments to order parameters."""
        order = context.order

        # Apply quantity adjustment
        if result.adjusted_quantity is not None:
            original_qty = order.get("quantity", 0)

            # Ensure adjusted quantity is valid
            symbol_info = context.symbol_info
            if symbol_info:
                min_qty = symbol_info.get("min_quantity", 0)
                max_qty = symbol_info.get("max_quantity", float("inf"))
                qty_step = symbol_info.get("quantity_step", 0)

                # Constrain to limits
                result.adjusted_quantity = max(min_qty, min(result.adjusted_quantity, max_qty))

                # Round to step
                if qty_step > 0:
                    result.adjusted_quantity = round(result.adjusted_quantity / qty_step) * qty_step

            logger.info(f"Quantity adjusted from {original_qty} to {result.adjusted_quantity}")

        # Apply price adjustment
        if result.adjusted_price is not None:
            original_price = order.get("price", 0)
            logger.info(f"Price adjusted from {original_price} to {result.adjusted_price}")

        # Apply order type suggestion
        if result.suggested_order_type:
            original_type = order.get("type")
            logger.info(
                f"Suggested order type change from {original_type} to {result.suggested_order_type}"
            )

    def _load_validation_rules(self) -> Dict[str, Any]:
        """Load exchange-specific validation rules."""
        rules = {
            "bybit": {
                "max_orders": 200,
                "max_positions": 200,
                "min_quantity_usd": 1.0,
                "max_leverage": 100,
                "fee_rate": 0.00075,  # 0.075%
            },
            "binance": {
                "max_orders": 200,
                "max_positions": 50,
                "min_quantity_usd": 10.0,
                "max_leverage": 125,
                "fee_rate": 0.0004,  # 0.04%
            },
        }

        return rules.get(self.exchange_type, rules["bybit"])
