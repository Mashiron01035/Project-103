"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : order_builder
    Project     : Project-103
    Description : Order builder module. This module handles order construction and preparation.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.core.exceptions import OrderValidationError

logger = logging.getLogger(__name__)


@dataclass
class OrderBuildContext:
    """Context for building an order."""

    signal: Dict[str, Any]  # Trading signal
    risk_params: Dict[str, Any]  # Risk parameters
    account_info: Dict[str, Any]  # Account information
    market_data: Dict[str, Any]  # Current market data
    symbol_info: Dict[str, Any]  # Symbol information

    # Optional parameters
    order_type: str = "MARKET"
    time_in_force: str = "GTC"
    reduce_only: bool = False
    post_only: bool = False

    # Advanced options
    iceberg_quantity: Optional[float] = None  # Hidden quantity
    trigger_price: Optional[float] = None  # Trigger price for stop orders

    def validate(self) -> List[str]:
        """Validate build context."""
        errors = []

        if not self.signal:
            errors.append("Signal is required")

        if not self.risk_params:
            errors.append("Risk parameters are required")

        if not self.symbol_info:
            errors.append("Symbol information is required")

        return errors


@dataclass
class OrderTemplate:
    """Template for order creation."""

    # Basic info
    symbol: str
    side: str  # BUY or SELL
    type: str  # MARKET, LIMIT, etc.
    quantity: float

    # Prices
    price: Optional[float] = None  # For limit orders
    stop_price: Optional[float] = None  # For stop orders

    # Risk management
    stop_loss: Optional[float] = None
    take_profit: List[float] = None
    trailing_stop_distance: Optional[float] = None

    # Execution parameters
    time_in_force: str = "GTC"
    reduce_only: bool = False
    post_only: bool = False
    close_on_trigger: bool = False

    # Advanced
    iceberg_quantity: Optional[float] = None
    trigger_by: str = "LastPrice"  # LastPrice, MarkPrice, IndexPrice

    # Metadata
    signal_id: str = ""
    strategy_name: str = ""
    timeframe: str = ""
    confidence: float = 0.0

    # Validation
    min_quantity: float = 0.0
    max_quantity: float = 0.0
    quantity_step: float = 0.0
    price_precision: int = 2
    quantity_precision: int = 3

    def to_exchange_format(self, exchange: str) -> Dict[str, Any]:
        """Convert to exchange-specific format."""
        if exchange.lower() == "bybit":
            return self._to_bybit_format()
        elif exchange.lower() == "binance":
            return self._to_binance_format()
        else:
            return self._to_generic_format()

    def _to_bybit_format(self) -> Dict[str, Any]:
        """Convert to Bybit format."""
        params = {
            "symbol": self.symbol,
            "side": "Buy" if self.side == "BUY" else "Sell",
            "order_type": self._convert_order_type_bybit(),
            "qty": self.quantity,
            "time_in_force": self.time_in_force,
            "reduce_only": self.reduce_only,
            "close_on_trigger": self.close_on_trigger,
        }

        if self.price:
            params["price"] = self.price

        if self.stop_price:
            params["stop_px"] = self.stop_price
            params["trigger_by"] = self.trigger_by

        if self.stop_loss:
            params["stop_loss"] = self.stop_loss

        if self.take_profit and len(self.take_profit) > 0:
            params["take_profit"] = self.take_profit[0]  # Bybit supports single TP

        return params

    def _to_binance_format(self) -> Dict[str, Any]:
        """Convert to Binance format."""
        params = {
            "symbol": self.symbol,
            "side": self.side,
            "type": self._convert_order_type_binance(),
            "quantity": self.quantity,
            "timeInForce": self.time_in_force,
            "reduceOnly": self.reduce_only,
        }

        if self.price:
            params["price"] = self.price

        if self.stop_price:
            params["stopPrice"] = self.stop_price

        if self.iceberg_quantity:
            params["icebergQty"] = self.iceberg_quantity

        return params

    def _to_generic_format(self) -> Dict[str, Any]:
        """Convert to generic format."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "type": self.type,
            "quantity": self.quantity,
            "price": self.price,
            "stop_price": self.stop_price,
            "time_in_force": self.time_in_force,
            "reduce_only": self.reduce_only,
            "post_only": self.post_only,
        }

    def _convert_order_type_bybit(self) -> str:
        """Convert order type to Bybit format."""
        mapping = {
            "MARKET": "Market",
            "LIMIT": "Limit",
            "STOP_MARKET": "Market",  # With stop_px
            "STOP_LIMIT": "Limit",  # With stop_px
        }
        return mapping.get(self.type, "Market")

    def _convert_order_type_binance(self) -> str:
        """Convert order type to Binance format."""
        mapping = {
            "MARKET": "MARKET",
            "LIMIT": "LIMIT",
            "STOP_MARKET": "STOP_MARKET",
            "STOP_LIMIT": "STOP",
            "TAKE_PROFIT_MARKET": "TAKE_PROFIT_MARKET",
            "TAKE_PROFIT_LIMIT": "TAKE_PROFIT",
        }
        return mapping.get(self.type, "MARKET")


class OrderBuilder:
    """Order builder class."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize order builder.

        Args:
            config: Configuration
        """
        self.config = config
        self.exchange_type = config.get("exchange_type", "bybit")

        # Default parameters
        self.default_slippage = config.get("default_slippage", 0.001)
        self.default_time_in_force = config.get("default_time_in_force", "GTC")
        self.use_reduce_only = config.get("use_reduce_only", True)
        self.use_post_only_limits = config.get("use_post_only_limits", False)

        logger.info(f"OrderBuilder initialized for {self.exchange_type}")

    def build_from_signal(
        self,
        signal: Dict[str, Any],
        risk_params: Dict[str, Any],
        market_data: Optional[Dict[str, Any]] = None,
    ) -> OrderTemplate:
        """
        Build order template from signal.

        Args:
            signal: Trading signal
            risk_params: Risk parameters
            market_data: Current market data

        Returns:
            OrderTemplate: Order template

        Raises:
            OrderValidationError: If order cannot be built
        """
        try:
            # Extract signal information
            symbol = signal.get("symbol")
            signal_type = signal.get("type")
            entry_price = signal.get("entry_price")
            stop_loss = signal.get("stop_loss")
            take_profit = signal.get("take_profit", [])

            # Determine order side
            if signal_type in ["BUY", "LONG"]:
                side = "BUY"
            elif signal_type in ["SELL", "SHORT"]:
                side = "SELL"
            else:
                raise OrderValidationError(f"Invalid signal type: {signal_type}")

            # Determine order type
            order_type = self._determine_order_type(signal, market_data)

            # Calculate quantity
            quantity = risk_params.get("position_size", 0.0)

            # Create template
            template = OrderTemplate(
                symbol=symbol,
                side=side,
                type=order_type,
                quantity=quantity,
                price=entry_price if order_type == "LIMIT" else None,
                stop_loss=stop_loss,
                take_profit=take_profit if isinstance(take_profit, list) else [take_profit],
                time_in_force=self.default_time_in_force,
                reduce_only=False,  # New position
                post_only=self.use_post_only_limits and order_type == "LIMIT",
                signal_id=signal.get("id", ""),
                strategy_name=signal.get("strategy_name", ""),
                timeframe=signal.get("timeframe", ""),
                confidence=signal.get("confidence", 0.0),
            )

            # Apply symbol info if available
            if "symbol_info" in risk_params:
                self._apply_symbol_info(template, risk_params["symbol_info"])

            # Validate template
            errors = self._validate_template(template)
            if errors:
                raise OrderValidationError(f"Order validation failed: {errors}")

            logger.info(f"Built order template for {symbol} {side} {quantity}")

            return template

        except Exception as e:
            logger.error(f"Error building order from signal: {e}")
            raise OrderValidationError(f"Failed to build order: {e}")

    def build_close_order(
        self, position: Dict[str, Any], close_type: str = "MARKET", close_reason: str = "manual"
    ) -> OrderTemplate:
        """
        Build order to close position.

        Args:
            position: Position to close
            close_type: MARKET or LIMIT
            close_reason: Reason for closing

        Returns:
            OrderTemplate: Close order template
        """
        # Determine close side (opposite of position)
        close_side = "SELL" if position.get("side") == "LONG" else "BUY"

        template = OrderTemplate(
            symbol=position.get("symbol"),
            side=close_side,
            type=close_type,
            quantity=position.get("quantity"),
            reduce_only=True,
            time_in_force="IOC" if close_type == "MARKET" else "GTC",
        )

        # Add metadata
        template.signal_id = f"CLOSE_{position.get('id')}"
        template.strategy_name = close_reason

        logger.info(f"Built close order for position {position.get('id')}")

        return template

    def build_stop_loss_order(
        self, position: Dict[str, Any], stop_price: float, order_type: str = "STOP_MARKET"
    ) -> OrderTemplate:
        """
        Build stop loss order.

        Args:
            position: Position to protect
            stop_price: Stop loss price
            order_type: Order type (STOP_MARKET or STOP_LIMIT)

        Returns:
            OrderTemplate: Stop loss order template
        """
        # Stop loss side is opposite of position
        sl_side = "SELL" if position.get("side") == "LONG" else "BUY"

        template = OrderTemplate(
            symbol=position.get("symbol"),
            side=sl_side,
            type=order_type,
            quantity=position.get("quantity"),
            stop_price=stop_price,
            price=stop_price if order_type == "STOP_LIMIT" else None,
            reduce_only=True,
            close_on_trigger=True,
            time_in_force="GTC",
        )

        template.signal_id = f"SL_{position.get('id')}"

        logger.info(f"Built stop loss order at {stop_price} for position {position.get('id')}")

        return template

    def build_take_profit_orders(
        self,
        position: Dict[str, Any],
        take_profit_levels: List[float],
        partial_exits: Optional[List[float]] = None,
    ) -> List[OrderTemplate]:
        """
        Build take profit orders.

        Args:
            position: Position
            take_profit_levels: TP price levels
            partial_exits: Percentage to exit at each level

        Returns:
            List[OrderTemplate]: Take profit order templates
        """
        templates = []

        # Default to equal distribution
        if not partial_exits:
            partial_exits = [1.0 / len(take_profit_levels)] * len(take_profit_levels)

        # TP side is opposite of position
        tp_side = "SELL" if position.get("side") == "LONG" else "BUY"

        remaining_quantity = position.get("quantity")

        for i, (tp_price, exit_pct) in enumerate(zip(take_profit_levels, partial_exits)):
            tp_quantity = position.get("quantity") * exit_pct
            tp_quantity = min(tp_quantity, remaining_quantity)

            template = OrderTemplate(
                symbol=position.get("symbol"),
                side=tp_side,
                type="LIMIT",
                quantity=tp_quantity,
                price=tp_price,
                reduce_only=True,
                post_only=False,  # Don't use post-only for TP
                time_in_force="GTC",
            )

            template.signal_id = f"TP{i + 1}_{position.get('id')}"

            templates.append(template)
            remaining_quantity -= tp_quantity

            if remaining_quantity <= 0:
                break

        logger.info(f"Built {len(templates)} take profit orders for position {position.get('id')}")

        return templates

    def build_bracket_order(
        self, signal: Dict[str, Any], risk_params: Dict[str, Any]
    ) -> Tuple[OrderTemplate, OrderTemplate, List[OrderTemplate]]:
        """
        Build bracket order (entry + stop loss + take profits).

        Args:
            signal: Trading signal
            risk_params: Risk parameters

        Returns:
            Tuple of (entry_order, stop_loss_order, take_profit_orders)
        """
        # Build entry order
        entry_order = self.build_from_signal(signal, risk_params)

        # Create fake position for SL/TP orders
        fake_position = {
            "id": f"BRACKET_{signal.get('id')}",
            "symbol": signal.get("symbol"),
            "side": "LONG" if signal.get("type") in ["BUY", "LONG"] else "SHORT",
            "quantity": risk_params.get("position_size"),
        }

        # Build stop loss
        stop_loss_order = None
        if signal.get("stop_loss"):
            stop_loss_order = self.build_stop_loss_order(fake_position, signal.get("stop_loss"))

        # Build take profits
        take_profit_orders = []
        if signal.get("take_profit"):
            tp_levels = signal.get("take_profit")
            if not isinstance(tp_levels, list):
                tp_levels = [tp_levels]

            take_profit_orders = self.build_take_profit_orders(fake_position, tp_levels)

        logger.info(f"Built bracket order for {signal.get('symbol')}")

        return entry_order, stop_loss_order, take_profit_orders

    def build_scaled_entry(
        self,
        signal: Dict[str, Any],
        risk_params: Dict[str, Any],
        num_orders: int = 3,
        price_range: float = 0.005,  # 0.5% range
    ) -> List[OrderTemplate]:
        """
        Build scaled entry with multiple orders.

        Args:
            signal: Trading signal
            risk_params: Risk parameters
            num_orders: Number of entry orders
            price_range: Price range for scaling (as percentage)

        Returns:
            List[OrderTemplate]: Scaled entry orders
        """
        templates = []

        base_price = signal.get("entry_price")
        total_quantity = risk_params.get("position_size")
        quantity_per_order = total_quantity / num_orders

        # Calculate price levels
        if signal.get("type") in ["BUY", "LONG"]:
            # For buy orders, scale down from entry price
            prices = [
                base_price * (1 - price_range * i / (num_orders - 1)) for i in range(num_orders)
            ]
        else:
            # For sell orders, scale up from entry price
            prices = [
                base_price * (1 + price_range * i / (num_orders - 1)) for i in range(num_orders)
            ]

        # Build orders
        for i, price in enumerate(prices):
            order_signal = signal.copy()
            order_signal["entry_price"] = price

            order_risk = risk_params.copy()
            order_risk["position_size"] = quantity_per_order

            template = self.build_from_signal(order_signal, order_risk)
            template.type = "LIMIT"  # Always use limit for scaled entry
            template.price = price
            template.signal_id = f"{signal.get('id')}_SCALE_{i + 1}"

            templates.append(template)

        logger.info(f"Built {num_orders} scaled entry orders for {signal.get('symbol')}")

        return templates

    # ==================== Private Methods ====================

    def _determine_order_type(
        self, signal: Dict[str, Any], market_data: Optional[Dict[str, Any]]
    ) -> str:
        """Determine appropriate order type."""
        # Check if signal specifies order type
        if "order_type" in signal:
            return signal["order_type"]

        # Check if entry price is specified
        entry_price = signal.get("entry_price")

        if not entry_price:
            return "MARKET"

        # If market data available, check if limit order makes sense
        if market_data:
            current_price = market_data.get("last_price", 0)

            if current_price > 0:
                price_diff = abs(entry_price - current_price) / current_price

                # If price is very close, use market order
                if price_diff < self.default_slippage:
                    return "MARKET"

                # Check if it's a stop order
                signal_type = signal.get("type")
                if signal_type in ["BUY", "LONG"] and entry_price > current_price * 1.001:
                    return "STOP_MARKET"
                elif signal_type in ["SELL", "SHORT"] and entry_price < current_price * 0.999:
                    return "STOP_MARKET"

        return "LIMIT"

    def _apply_symbol_info(self, template: OrderTemplate, symbol_info: Dict[str, Any]) -> None:
        """Apply symbol information to template."""
        template.min_quantity = symbol_info.get("min_quantity", 0.0)
        template.max_quantity = symbol_info.get("max_quantity", float("inf"))
        template.quantity_step = symbol_info.get("quantity_step", 0.001)
        template.price_precision = symbol_info.get("price_precision", 2)
        template.quantity_precision = symbol_info.get("quantity_precision", 3)

        # Round quantity to step
        if template.quantity_step > 0:
            template.quantity = (
                round(template.quantity / template.quantity_step) * template.quantity_step
            )

        # Round prices to precision
        if template.price:
            template.price = round(template.price, template.price_precision)

        if template.stop_price:
            template.stop_price = round(template.stop_price, template.price_precision)

        if template.stop_loss:
            template.stop_loss = round(template.stop_loss, template.price_precision)

        if template.take_profit:
            template.take_profit = [
                round(tp, template.price_precision) for tp in template.take_profit
            ]

    def _validate_template(self, template: OrderTemplate) -> List[str]:
        """Validate order template."""
        errors = []

        if not template.symbol:
            errors.append("Symbol is required")

        if template.side not in ["BUY", "SELL"]:
            errors.append(f"Invalid side: {template.side}")

        if template.quantity <= 0:
            errors.append(f"Invalid quantity: {template.quantity}")

        if template.min_quantity > 0 and template.quantity < template.min_quantity:
            errors.append(f"Quantity {template.quantity} below minimum {template.min_quantity}")

        if template.max_quantity > 0 and template.quantity > template.max_quantity:
            errors.append(f"Quantity {template.quantity} above maximum {template.max_quantity}")

        if template.type == "LIMIT" and not template.price:
            errors.append("Price required for limit order")

        if template.type in ["STOP_MARKET", "STOP_LIMIT"] and not template.stop_price:
            errors.append("Stop price required for stop order")

        return errors
