"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : order_manager
    Project     : Project-103
    Description : Order management module. This module handles the complete order lifecycle from creation to closure.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from src.core.exceptions import OrderError
from src.execution.connectors.base_connector import BaseConnector
from src.execution.order_builder import OrderBuilder
from src.execution.order_validator import OrderValidator, ValidationContext
from src.execution.position_tracker import PositionTracker

logger = logging.getLogger(__name__)

# History size limits to prevent memory leaks
MAX_ORDER_HISTORY_SIZE = 1000


# ==================== Data Classes ====================


@dataclass
class OrderRequest:
    """Request to create an order."""

    # Main parameters
    signal: Dict[str, Any]  # Trading signal
    risk_decision: Dict[str, Any]  # Risk manager decision

    # Execution parameters
    order_type: str = "MARKET"  # MARKET, LIMIT, STOP_MARKET
    time_in_force: str = "GTC"  # GTC, IOC, FOK

    # Execution settings
    slippage_tolerance: float = 0.001  # 0.1% acceptable slippage
    max_retry_attempts: int = 3  # Retry attempts on failure
    retry_delay: float = 1.0  # Delay between retries in seconds

    # Additional parameters
    reduce_only: bool = False  # Only reduce position
    post_only: bool = False  # Only maker orders
    close_on_trigger: bool = False  # Close when triggered

    # Metadata
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class OrderModifyRequest:
    """Request to modify an order."""

    order_id: str

    # What can be modified
    new_price: Optional[float] = None
    new_quantity: Optional[float] = None
    new_stop_loss: Optional[float] = None
    new_take_profit: Optional[List[float]] = None
    new_trailing_distance: Optional[float] = None


@dataclass
class OrderCancelRequest:
    """Request to cancel an order."""

    order_id: str
    reason: str = "manual_cancel"
    force: bool = False  # Force cancellation


@dataclass
class PositionCloseRequest:
    """Request to close a position."""

    position_id: str
    close_type: str = "MARKET"  # MARKET or LIMIT
    reason: str = "manual_close"

    # For limit closing
    limit_price: Optional[float] = None

    # Partial closing
    partial_close: bool = False
    close_quantity: Optional[float] = None


@dataclass
class Order:
    """Complete order information."""

    # Identifiers
    id: str  # Internal ID
    exchange_order_id: Optional[str] = None  # Exchange ID
    signal_id: str = ""  # Signal ID that generated this order
    position_id: Optional[str] = None  # Associated position ID

    # Main parameters
    symbol: str = ""  # SOLUSDT
    side: str = ""  # BUY or SELL
    type: str = ""  # MARKET, LIMIT, etc.
    status: str = "NEW"  # NEW, FILLED, CANCELED, etc.

    # Quantities
    quantity: float = 0.0  # Requested quantity
    executed_quantity: float = 0.0  # Executed quantity
    remaining_quantity: float = 0.0  # Remaining to execute

    # Prices
    price: Optional[float] = None  # Price for limit orders
    stop_price: Optional[float] = None  # Stop price
    average_price: Optional[float] = None  # Average execution price

    # Timestamps
    created_at: int = 0  # Creation time
    updated_at: int = 0  # Last update time
    executed_at: Optional[int] = None  # Execution time

    # Fees and costs
    commission: float = 0.0  # Commission
    commission_asset: str = "USDT"  # Commission currency
    slippage: float = 0.0  # Actual slippage

    # Risk management
    stop_loss_order_id: Optional[str] = None  # Stop loss order ID
    take_profit_order_ids: List[str] = field(default_factory=list)  # Take profit order IDs

    # Statistics
    retry_count: int = 0  # Number of retry attempts
    error_message: Optional[str] = None  # Error message

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "exchange_order_id": self.exchange_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "type": self.type,
            "status": self.status,
            "quantity": self.quantity,
            "executed_quantity": self.executed_quantity,
            "average_price": self.average_price,
            "created_at": self.created_at,
            "commission": self.commission,
            "slippage": self.slippage,
        }


@dataclass
class OrderResult:
    """Order operation result."""

    success: bool
    order: Optional[Order] = None

    # Execution details
    execution_time_ms: int = 0  # Execution time in milliseconds
    actual_price: Optional[float] = None  # Actual price
    actual_quantity: Optional[float] = None  # Actual quantity

    # Slippage information
    price_slippage: float = 0.0  # Price deviation
    quantity_slippage: float = 0.0  # Quantity deviation

    # Fees
    commission: float = 0.0
    commission_asset: str = "USDT"

    # Errors
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    error_details: Optional[Dict] = None

    # Warnings
    warnings: List[str] = field(default_factory=list)

    @property
    def is_partial_fill(self) -> bool:
        """Check if partially filled."""
        if self.order:
            return 0 < self.order.executed_quantity < self.order.quantity
        return False


@dataclass
class OrderBookSnapshot:
    """Order book snapshot at execution time."""

    timestamp: int
    best_bid: float
    best_ask: float
    spread: float
    bid_liquidity: float  # Bid side liquidity
    ask_liquidity: float  # Ask side liquidity
    depth_10_percent: float  # Order book depth within 10%


# ==================== OrderManager Class ====================


class OrderManager:
    """Main order manager class."""

    def __init__(
        self,
        exchange_connector: BaseConnector,
        position_tracker: PositionTracker,
        config: Dict[str, Any],
    ):
        """
        Initialize order manager.

        Args:
            exchange_connector: Exchange connector
            position_tracker: Position tracker
            config: Configuration
        """
        self.connector = exchange_connector
        self.position_tracker = position_tracker
        self.config = config

        # Order storage
        self.active_orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self.pending_orders: Dict[str, Order] = {}

        # Components
        self.order_builder = OrderBuilder(config)
        self.order_validator = OrderValidator(
            exchange_type=config.get("exchange_type", "bybit"), config=config
        )

        # Statistics
        self.total_orders = 0
        self.successful_orders = 0
        self.failed_orders = 0
        self.total_slippage = 0.0
        self.total_commission = 0.0

        # Locks for thread safety
        self._order_lock = asyncio.Lock()
        self._position_lock = asyncio.Lock()

        logger.info("OrderManager initialized")

    async def create_order(self, request: OrderRequest) -> Order:
        """
        Create order from request.

        Args:
            request: Order creation request

        Returns:
            Order: Created order

        Raises:
            OrderError: On creation error
        """
        try:
            # Generate unique order ID
            order_id = self._generate_order_id()

            # Extract signal and risk info
            signal = request.signal
            risk_decision = request.risk_decision

            # Create order
            order = Order(
                id=order_id,
                signal_id=signal.get("id", ""),
                symbol=signal.get("symbol", ""),
                side=self._convert_signal_side(signal.get("type", "")),
                type=request.order_type,
                status="NEW",
                quantity=risk_decision.get("position_size", 0.0),
                price=signal.get("entry_price") if request.order_type == "LIMIT" else None,
                created_at=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
                metadata={
                    "signal": signal,
                    "risk_decision": risk_decision,
                    "request": asdict(request),
                },
            )

            # Calculate remaining quantity
            order.remaining_quantity = order.quantity

            # Store in pending orders
            async with self._order_lock:
                self.pending_orders[order_id] = order

            logger.info(
                f"Created order {order_id} for {order.symbol} {order.side} {order.quantity}"
            )

            return order

        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            raise OrderError(f"Failed to create order: {e}")

    async def send_order(self, order: Order) -> OrderResult:
        """
        Send order to exchange.

        Args:
            order: Order to send

        Returns:
            OrderResult: Execution result
        """
        start_time = time.time()
        result = OrderResult(success=False)

        try:
            # Get order request from metadata
            request = OrderRequest(**order.metadata.get("request", {}))

            # Validate order
            validation_context = await self._create_validation_context(order)
            validation_result = self.order_validator.validate(validation_context)

            if not validation_result.is_valid:
                result.error_message = f"Validation failed: {validation_result.errors}"
                result.error_details = {"validation_errors": validation_result.errors}
                return result

            # Apply adjustments if any
            if validation_result.adjusted_quantity:
                order.quantity = validation_result.adjusted_quantity
                order.remaining_quantity = validation_result.adjusted_quantity
                result.warnings.append(
                    f"Quantity adjusted to {validation_result.adjusted_quantity}"
                )

            if validation_result.adjusted_price:
                order.price = validation_result.adjusted_price
                result.warnings.append(f"Price adjusted to {validation_result.adjusted_price}")

            # Get order book snapshot
            orderbook_snapshot = await self._get_orderbook_snapshot(order.symbol)

            # Prepare order parameters
            order_params = self._prepare_order_params(order, request)

            # Send order with retry logic
            exchange_response = await self._send_with_retry(
                order_params, request.max_retry_attempts, request.retry_delay
            )

            if exchange_response.get("success"):
                # Update order with exchange info
                order.exchange_order_id = exchange_response.get("order_id")
                order.status = exchange_response.get("status", "SUBMITTED")
                order.executed_at = int(time.time() * 1000)

                # Calculate execution metrics
                actual_price = exchange_response.get("price", order.price)
                actual_quantity = exchange_response.get("quantity", order.quantity)

                result.success = True
                result.order = order
                result.actual_price = actual_price
                result.actual_quantity = actual_quantity
                result.execution_time_ms = int((time.time() - start_time) * 1000)

                # Calculate slippage
                if order.type == "MARKET" and orderbook_snapshot:
                    expected_price = (
                        orderbook_snapshot.best_ask
                        if order.side == "BUY"
                        else orderbook_snapshot.best_bid
                    )
                    result.price_slippage = abs(actual_price - expected_price) / expected_price
                    order.slippage = result.price_slippage

                # Update commission
                result.commission = exchange_response.get("commission", 0.0)
                result.commission_asset = exchange_response.get("commission_asset", "USDT")
                order.commission = result.commission
                order.commission_asset = result.commission_asset

                # Move to active orders
                async with self._order_lock:
                    if order.id in self.pending_orders:
                        del self.pending_orders[order.id]
                    self.active_orders[order.id] = order

                # Update statistics
                self.total_orders += 1
                self.successful_orders += 1
                self.total_slippage += result.price_slippage
                self.total_commission += result.commission

                # Create position if needed
                if order.status == "FILLED":
                    await self._create_position_from_order(order)

                logger.info(f"Order {order.id} executed successfully at {actual_price}")

            else:
                # Handle failure
                error_msg = exchange_response.get("error", "Unknown error")
                result.error_message = error_msg
                result.error_code = exchange_response.get("error_code")
                result.error_details = exchange_response

                order.status = "REJECTED"
                order.error_message = error_msg

                # Update statistics
                self.total_orders += 1
                self.failed_orders += 1

                logger.error(f"Order {order.id} failed: {error_msg}")

        except Exception as e:
            result.error_message = str(e)
            result.error_details = {"exception": str(e)}
            logger.error(f"Error sending order {order.id}: {e}", exc_info=True)

        return result

    async def cancel_order(self, request: OrderCancelRequest) -> bool:
        """
        Cancel an order.

        Args:
            request: Cancel request

        Returns:
            bool: Success status
        """
        try:
            async with self._order_lock:
                order = self.active_orders.get(request.order_id)

                if not order:
                    logger.warning(f"Order {request.order_id} not found in active orders")
                    return False

                # Check if order can be cancelled
                if order.status in ["FILLED", "CANCELED", "REJECTED"]:
                    logger.warning(
                        f"Order {request.order_id} cannot be cancelled, status: {order.status}"
                    )
                    return False

            # Send cancel request to exchange
            cancel_params = {
                "symbol": order.symbol,
                "order_id": order.exchange_order_id,
            }

            result = await self.connector.cancel_order(cancel_params)

            if result.get("success"):
                # Update order status
                async with self._order_lock:
                    order.status = "CANCELED"
                    order.updated_at = int(time.time() * 1000)
                    order.metadata["cancel_reason"] = request.reason

                    # Move to history
                    del self.active_orders[request.order_id]
                    self.order_history.append(order)

                    # Trim history to prevent memory leak
                    if len(self.order_history) > MAX_ORDER_HISTORY_SIZE:
                        self.order_history = self.order_history[-MAX_ORDER_HISTORY_SIZE:]

                logger.info(f"Order {request.order_id} cancelled successfully")
                return True
            else:
                logger.error(f"Failed to cancel order {request.order_id}: {result.get('error')}")
                return False

        except Exception as e:
            logger.error(f"Error cancelling order {request.order_id}: {e}")
            return False

    async def modify_order(self, request: OrderModifyRequest) -> OrderResult:
        """
        Modify an existing order.

        Args:
            request: Modification request

        Returns:
            OrderResult: Modification result
        """
        result = OrderResult(success=False)

        try:
            async with self._order_lock:
                order = self.active_orders.get(request.order_id)

                if not order:
                    result.error_message = f"Order {request.order_id} not found"
                    return result

                if order.status != "NEW":
                    result.error_message = f"Cannot modify order with status {order.status}"
                    return result

            # Prepare modification parameters
            modify_params = {
                "symbol": order.symbol,
                "order_id": order.exchange_order_id,
            }

            if request.new_price is not None:
                modify_params["price"] = request.new_price

            if request.new_quantity is not None:
                modify_params["quantity"] = request.new_quantity

            # Send modification request
            response = await self.connector.modify_order(modify_params)

            if response.get("success"):
                # Update order
                async with self._order_lock:
                    if request.new_price is not None:
                        order.price = request.new_price
                    if request.new_quantity is not None:
                        order.quantity = request.new_quantity
                        order.remaining_quantity = request.new_quantity - order.executed_quantity

                    order.updated_at = int(time.time() * 1000)

                result.success = True
                result.order = order

                logger.info(f"Order {request.order_id} modified successfully")
            else:
                result.error_message = response.get("error", "Modification failed")
                logger.error(f"Failed to modify order {request.order_id}: {result.error_message}")

        except Exception as e:
            result.error_message = str(e)
            logger.error(f"Error modifying order {request.order_id}: {e}")

        return result

    async def close_position(self, request: PositionCloseRequest) -> OrderResult:
        """
        Close a position.

        Args:
            request: Close position request

        Returns:
            OrderResult: Close result
        """
        result = OrderResult(success=False)

        try:
            # Get position
            position = self.position_tracker.get_position(request.position_id)

            if not position:
                result.error_message = f"Position {request.position_id} not found"
                return result

            # Create close order
            close_order = Order(
                id=self._generate_order_id(),
                symbol=position.symbol,
                side="SELL" if position.side == "LONG" else "BUY",
                type=request.close_type,
                quantity=request.close_quantity if request.partial_close else position.quantity,
                price=request.limit_price if request.close_type == "LIMIT" else None,
                position_id=request.position_id,
                created_at=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
                metadata={
                    "close_reason": request.reason,
                    "partial_close": request.partial_close,
                },
            )

            # Send close order
            order_request = OrderRequest(
                signal={"symbol": position.symbol, "type": "CLOSE"},
                risk_decision={"position_size": close_order.quantity},
                order_type=request.close_type,
            )

            close_order.metadata["request"] = asdict(order_request)

            result = await self.send_order(close_order)

            if result.success:
                # Update position
                if request.partial_close:
                    self.position_tracker.reduce_position(
                        request.position_id, request.close_quantity
                    )
                else:
                    self.position_tracker.close_position(
                        request.position_id, result.actual_price, request.reason
                    )

                logger.info(f"Position {request.position_id} closed successfully")

        except Exception as e:
            result.error_message = str(e)
            logger.error(f"Error closing position {request.position_id}: {e}")

        return result

    async def set_stop_loss(self, position_id: str, stop_price: float) -> Optional[str]:
        """
        Set stop loss for a position.

        Args:
            position_id: Position ID
            stop_price: Stop loss price

        Returns:
            Optional[str]: Stop loss order ID
        """
        try:
            position = self.position_tracker.get_position(position_id)

            if not position:
                logger.error(f"Position {position_id} not found")
                return None

            # Create stop loss order
            sl_params = {
                "symbol": position.symbol,
                "side": "SELL" if position.side == "LONG" else "BUY",
                "type": "STOP_MARKET",
                "quantity": position.quantity,
                "stop_price": stop_price,
                "reduce_only": True,
            }

            response = await self.connector.place_order(sl_params)

            if response.get("success"):
                sl_order_id = response.get("order_id")

                # Update position
                position.stop_loss = stop_price
                position.stop_loss_order_id = sl_order_id

                logger.info(f"Stop loss set for position {position_id} at {stop_price}")
                return sl_order_id
            else:
                logger.error(f"Failed to set stop loss: {response.get('error')}")
                return None

        except Exception as e:
            logger.error(f"Error setting stop loss for {position_id}: {e}")
            return None

    async def set_take_profit(self, position_id: str, take_profit_levels: List[float]) -> List[str]:
        """
        Set take profit levels for a position.

        Args:
            position_id: Position ID
            take_profit_levels: List of TP prices

        Returns:
            List[str]: Take profit order IDs
        """
        tp_order_ids = []

        try:
            position = self.position_tracker.get_position(position_id)

            if not position:
                logger.error(f"Position {position_id} not found")
                return tp_order_ids

            # Calculate quantity for each TP level
            quantity_per_level = position.quantity / len(take_profit_levels)

            for i, tp_price in enumerate(take_profit_levels):
                tp_params = {
                    "symbol": position.symbol,
                    "side": "SELL" if position.side == "LONG" else "BUY",
                    "type": "LIMIT",
                    "quantity": quantity_per_level,
                    "price": tp_price,
                    "reduce_only": True,
                    "post_only": False,
                }

                response = await self.connector.place_order(tp_params)

                if response.get("success"):
                    tp_order_id = response.get("order_id")
                    tp_order_ids.append(tp_order_id)

                    logger.info(f"TP{i + 1} set for position {position_id} at {tp_price}")
                else:
                    logger.error(f"Failed to set TP{i + 1}: {response.get('error')}")

            # Update position
            if tp_order_ids:
                position.take_profit = take_profit_levels
                position.take_profit_order_ids = tp_order_ids

        except Exception as e:
            logger.error(f"Error setting take profit for {position_id}: {e}")

        return tp_order_ids

    async def update_trailing_stop(self, position_id: str, new_stop_price: float) -> bool:
        """
        Update trailing stop for a position.

        Args:
            position_id: Position ID
            new_stop_price: New stop price

        Returns:
            bool: Success status
        """
        try:
            position = self.position_tracker.get_position(position_id)

            if not position:
                logger.error(f"Position {position_id} not found")
                return False

            # Cancel old stop loss
            if position.stop_loss_order_id:
                cancel_request = OrderCancelRequest(
                    order_id=position.stop_loss_order_id, reason="trailing_stop_update"
                )
                await self.cancel_order(cancel_request)

            # Set new stop loss
            new_sl_id = await self.set_stop_loss(position_id, new_stop_price)

            if new_sl_id:
                position.trailing_stop = new_stop_price
                logger.info(f"Trailing stop updated for {position_id} to {new_stop_price}")
                return True

            return False

        except Exception as e:
            logger.error(f"Error updating trailing stop for {position_id}: {e}")
            return False

    async def get_active_orders(self) -> List[Order]:
        """Get all active orders."""
        async with self._order_lock:
            return list(self.active_orders.values())

    async def get_order_history(
        self, limit: int = 100, symbol: Optional[str] = None
    ) -> List[Order]:
        """
        Get order history.

        Args:
            limit: Maximum number of orders
            symbol: Filter by symbol

        Returns:
            List[Order]: Historical orders
        """
        history = self.order_history[-limit:]

        if symbol:
            history = [o for o in history if o.symbol == symbol]

        return history

    def get_statistics(self) -> Dict[str, Any]:
        """Get order manager statistics."""
        return {
            "total_orders": self.total_orders,
            "successful_orders": self.successful_orders,
            "failed_orders": self.failed_orders,
            "success_rate": self.successful_orders / self.total_orders
            if self.total_orders > 0
            else 0,
            "average_slippage": self.total_slippage / self.successful_orders
            if self.successful_orders > 0
            else 0,
            "total_commission": self.total_commission,
            "active_orders": len(self.active_orders),
            "pending_orders": len(self.pending_orders),
        }

    # ==================== Private Methods ====================

    def _generate_order_id(self) -> str:
        """Generate unique order ID."""
        return f"ORD_{uuid.uuid4().hex[:12].upper()}"

    def _convert_signal_side(self, signal_type: str) -> str:
        """Convert signal type to order side."""
        if signal_type in ["BUY", "LONG"]:
            return "BUY"
        elif signal_type in ["SELL", "SHORT"]:
            return "SELL"
        else:
            return ""

    async def _create_validation_context(self, order: Order) -> ValidationContext:
        """Create validation context for order."""
        # Get current market data with None safety
        market_data = await self.connector.get_ticker(order.symbol) or {}

        # Get symbol info with None safety
        symbol_info = await self.connector.get_symbol_info(order.symbol) or {}

        # Get account balance with None safety
        account_info = await self.connector.get_account_info() or {}

        return ValidationContext(
            order=order.to_dict(),
            account_balance=account_info.get("balance", 0.0),
            open_positions=self.position_tracker.get_all_positions(),
            active_orders=list(self.active_orders.values()),
            market_data=market_data,
            symbol_info=symbol_info,
        )

    async def _get_orderbook_snapshot(self, symbol: str) -> Optional[OrderBookSnapshot]:
        """Get order book snapshot."""
        try:
            orderbook = await self.connector.get_orderbook(symbol, limit=20)

            if orderbook:
                bids = orderbook.get("bids", [])
                asks = orderbook.get("asks", [])

                if bids and asks:
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])

                    # Calculate liquidity
                    bid_liquidity = sum(float(b[1]) * float(b[0]) for b in bids[:10])
                    ask_liquidity = sum(float(a[1]) * float(a[0]) for a in asks[:10])

                    return OrderBookSnapshot(
                        timestamp=int(time.time() * 1000),
                        best_bid=best_bid,
                        best_ask=best_ask,
                        spread=best_ask - best_bid,
                        bid_liquidity=bid_liquidity,
                        ask_liquidity=ask_liquidity,
                        depth_10_percent=(bid_liquidity + ask_liquidity) / 2,
                    )

        except Exception as e:
            logger.error(f"Error getting orderbook snapshot: {e}")

        return None

    def _prepare_order_params(self, order: Order, request: OrderRequest) -> Dict[str, Any]:
        """Prepare order parameters for exchange."""
        params = {
            "symbol": order.symbol,
            "side": order.side,
            "type": order.type,
            "quantity": order.quantity,
            "time_in_force": request.time_in_force,
            "reduce_only": request.reduce_only,
            "post_only": request.post_only,
            "close_on_trigger": request.close_on_trigger,
        }

        if order.price:
            params["price"] = order.price

        if order.stop_price:
            params["stop_price"] = order.stop_price

        return params

    async def _send_with_retry(
        self, order_params: Dict[str, Any], max_attempts: int, retry_delay: float
    ) -> Dict[str, Any]:
        """Send order with retry logic."""
        last_error = None

        for attempt in range(max_attempts):
            try:
                response = await self.connector.place_order(order_params)

                if response.get("success"):
                    return response

                last_error = response.get("error")

                # Check if error is retryable
                error_code = response.get("error_code", "")
                if error_code in ["TIMEOUT", "CONNECTION_ERROR", "RATE_LIMIT"]:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(retry_delay * (2**attempt))  # Exponential backoff
                        continue

                # Non-retryable error
                break

            except Exception as e:
                last_error = str(e)
                if attempt < max_attempts - 1:
                    await asyncio.sleep(retry_delay * (2**attempt))
                    continue
                break

        return {
            "success": False,
            "error": last_error or "Unknown error",
            "attempts": max_attempts,
        }

    async def _create_position_from_order(self, order: Order) -> None:
        """Create position from filled order."""
        try:
            signal = order.metadata.get("signal", {})

            position = self.position_tracker.open_position(order=order.to_dict(), signal=signal)

            if position:
                # Link order to position
                order.position_id = position.id

                # Set stop loss and take profit
                if signal.get("stop_loss"):
                    await self.set_stop_loss(position.id, signal["stop_loss"])

                if signal.get("take_profit"):
                    await self.set_take_profit(position.id, signal["take_profit"])

                logger.info(f"Position {position.id} created from order {order.id}")

        except Exception as e:
            logger.error(f"Error creating position from order {order.id}: {e}")
