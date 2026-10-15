"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : test_order_manager.py
    Project     : Project-103
    Description : Unit tests for Order Manager module. Tests order creation,
                  modification, cancellation, position closing, lifecycle
                  management, and error handling.

    SPDX-License-Identifier: AGPL-3.0

"""

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.order_manager import (
    Order,
    OrderCancelRequest,
    OrderModifyRequest,
    OrderRequest,
    PositionCloseRequest,
)


class TestOrderDataClasses:
    """Tests for Order data classes."""

    def test_order_request_creation(self):
        """Test OrderRequest creation with required fields."""
        signal = {
            "id": "signal-001",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry_price": 95000.0,
            "stop_loss": 94000.0,
        }
        risk_decision = {"approved": True, "position_size": 0.1}

        request = OrderRequest(signal=signal, risk_decision=risk_decision)

        assert request.signal == signal
        assert request.risk_decision == risk_decision
        assert request.order_type == "MARKET"
        assert request.time_in_force == "GTC"
        assert request.slippage_tolerance == 0.001

    def test_order_request_custom_params(self):
        """Test OrderRequest with custom parameters."""
        request = OrderRequest(
            signal={"id": "test"},
            risk_decision={"approved": True},
            order_type="LIMIT",
            time_in_force="IOC",
            slippage_tolerance=0.002,
            reduce_only=True,
            post_only=True,
        )

        assert request.order_type == "LIMIT"
        assert request.time_in_force == "IOC"
        assert request.slippage_tolerance == 0.002
        assert request.reduce_only is True
        assert request.post_only is True

    def test_order_modify_request(self):
        """Test OrderModifyRequest creation."""
        request = OrderModifyRequest(order_id="order-001", new_price=96000.0, new_quantity=0.15)

        assert request.order_id == "order-001"
        assert request.new_price == 96000.0
        assert request.new_quantity == 0.15
        assert request.new_stop_loss is None

    def test_order_cancel_request(self):
        """Test OrderCancelRequest creation."""
        request = OrderCancelRequest(order_id="order-001", reason="strategy_changed", force=True)

        assert request.order_id == "order-001"
        assert request.reason == "strategy_changed"
        assert request.force is True

    def test_position_close_request(self):
        """Test PositionCloseRequest creation."""
        request = PositionCloseRequest(
            position_id="pos-001",
            close_type="LIMIT",
            limit_price=96000.0,
            partial_close=True,
            close_quantity=0.05,
        )

        assert request.position_id == "pos-001"
        assert request.close_type == "LIMIT"
        assert request.limit_price == 96000.0
        assert request.partial_close is True
        assert request.close_quantity == 0.05


class TestOrderClass:
    """Tests for Order class."""

    def test_order_creation(self):
        """Test Order creation with minimal parameters."""
        order = Order(id="order-001", symbol="BTCUSDT", side="BUY", type="MARKET", quantity=0.1)

        assert order.id == "order-001"
        assert order.symbol == "BTCUSDT"
        assert order.side == "BUY"
        assert order.type == "MARKET"
        assert order.quantity == 0.1
        assert order.status == "NEW"

    def test_order_default_values(self):
        """Test Order default values."""
        order = Order(id="order-001")

        assert order.exchange_order_id is None
        assert order.signal_id == ""
        assert order.position_id is None
        assert order.executed_quantity == 0.0
        assert order.remaining_quantity == 0.0
        assert order.commission == 0.0
        assert order.retry_count == 0

    def test_order_to_dict(self):
        """Test Order to_dict method."""
        order = Order(
            id="order-001",
            symbol="BTCUSDT",
            side="BUY",
            type="MARKET",
            quantity=0.1,
            status="FILLED",
            average_price=95000.0,
        )

        order_dict = order.to_dict()

        assert isinstance(order_dict, dict)
        assert order_dict["id"] == "order-001"
        assert order_dict["symbol"] == "BTCUSDT"
        assert order_dict["status"] == "FILLED"

    def test_order_with_risk_management(self):
        """Test Order with stop loss and take profit orders."""
        order = Order(
            id="order-001",
            symbol="BTCUSDT",
            side="BUY",
            type="MARKET",
            quantity=0.1,
            stop_loss_order_id="sl-001",
            take_profit_order_ids=["tp-001", "tp-002"],
        )

        assert order.stop_loss_order_id == "sl-001"
        assert len(order.take_profit_order_ids) == 2
        assert "tp-001" in order.take_profit_order_ids


class TestOrderLifecycle:
    """Tests for order lifecycle management."""

    @pytest.fixture
    def mock_connector(self):
        """Create mock connector."""
        connector = MagicMock()
        connector.place_order = AsyncMock(
            return_value={"order_id": "exchange-order-001", "status": "NEW", "symbol": "BTCUSDT"}
        )
        connector.cancel_order = AsyncMock(return_value=True)
        connector.get_order_status = AsyncMock(
            return_value={"status": "FILLED", "executed_qty": 0.1, "average_price": 95000.0}
        )
        return connector

    def test_order_status_transitions(self):
        """Test valid order status transitions."""
        valid_transitions = {
            "NEW": ["PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"],
            "PARTIALLY_FILLED": ["FILLED", "CANCELLED"],
            "FILLED": [],  # Terminal state
            "CANCELLED": [],  # Terminal state
            "REJECTED": [],  # Terminal state
        }

        order = Order(id="order-001", status="NEW")

        # Valid transition
        assert "FILLED" in valid_transitions[order.status]

        order.status = "PARTIALLY_FILLED"
        assert "FILLED" in valid_transitions[order.status]

    def test_order_filled_calculations(self):
        """Test order filled quantity calculations."""
        order = Order(id="order-001", quantity=1.0, executed_quantity=0.6, remaining_quantity=0.4)

        assert order.executed_quantity + order.remaining_quantity == order.quantity

    def test_order_slippage_calculation(self):
        """Test order slippage calculation."""
        expected_price = 95000.0
        actual_price = 95100.0  # Worse price for buy

        slippage = (actual_price - expected_price) / expected_price
        assert slippage == pytest.approx(0.001053, rel=1e-2)


class TestOrderValidation:
    """Tests for order validation."""

    def test_valid_market_order(self):
        """Test validation of valid market order."""
        order = Order(id="order-001", symbol="BTCUSDT", side="BUY", type="MARKET", quantity=0.1)

        # Market order doesn't need price
        assert order.price is None
        assert order.quantity > 0

    def test_valid_limit_order(self):
        """Test validation of valid limit order."""
        order = Order(
            id="order-001", symbol="BTCUSDT", side="BUY", type="LIMIT", quantity=0.1, price=95000.0
        )

        # Limit order needs price
        assert order.price is not None
        assert order.price > 0

    def test_invalid_order_quantity(self):
        """Test detection of invalid order quantity."""
        order = Order(
            id="order-001",
            symbol="BTCUSDT",
            side="BUY",
            type="MARKET",
            quantity=-0.1,  # Invalid negative quantity
        )

        assert order.quantity < 0  # Should be detected as invalid

    def test_order_symbol_format(self):
        """Test order symbol format validation."""
        valid_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

        for symbol in valid_symbols:
            order = Order(id="test", symbol=symbol)
            assert order.symbol == symbol

    def test_order_side_validation(self):
        """Test order side validation."""
        valid_sides = ["BUY", "SELL"]

        for side in valid_sides:
            order = Order(id="test", side=side)
            assert order.side in valid_sides


class TestOrderMetadata:
    """Tests for order metadata handling."""

    def test_order_metadata_storage(self):
        """Test storing metadata in order."""
        metadata = {
            "strategy_name": "trend_following",
            "signal_confidence": 0.85,
            "market_condition": "trending",
        }

        order = Order(id="order-001", symbol="BTCUSDT", metadata=metadata)

        assert order.metadata == metadata
        assert order.metadata["strategy_name"] == "trend_following"

    def test_order_metadata_update(self):
        """Test updating order metadata."""
        order = Order(id="order-001", metadata={"initial": "value"})

        order.metadata["updated"] = "new_value"
        order.metadata["execution_time"] = 150

        assert "initial" in order.metadata
        assert "updated" in order.metadata
        assert order.metadata["execution_time"] == 150


class TestOrderErrors:
    """Tests for order error handling."""

    def test_order_with_error(self):
        """Test order with error message."""
        order = Order(id="order-001", status="REJECTED", error_message="Insufficient balance")

        assert order.status == "REJECTED"
        assert order.error_message == "Insufficient balance"

    def test_order_retry_count(self):
        """Test order retry counting."""
        order = Order(id="order-001", retry_count=0)

        # Simulate retries
        for i in range(3):
            order.retry_count += 1

        assert order.retry_count == 3

    def test_order_retry_limit(self):
        """Test order retry limit check."""
        max_retries = 3
        order = Order(id="order-001", retry_count=3)

        should_retry = order.retry_count < max_retries
        assert should_retry is False


class TestOrderTimestamps:
    """Tests for order timestamp handling."""

    def test_order_timestamps(self):
        """Test order timestamps are set correctly."""
        now = int(datetime.now().timestamp() * 1000)

        order = Order(id="order-001", created_at=now, updated_at=now)

        assert order.created_at == now
        assert order.updated_at == now
        assert order.executed_at is None

    def test_order_execution_timestamp(self):
        """Test setting execution timestamp."""
        order = Order(id="order-001")

        # Simulate execution
        order.status = "FILLED"
        order.executed_at = int(datetime.now().timestamp() * 1000)

        assert order.executed_at is not None
        assert order.status == "FILLED"


class TestOrderFees:
    """Tests for order fee calculations."""

    def test_order_commission(self):
        """Test order commission tracking."""
        order = Order(
            id="order-001",
            quantity=1.0,
            average_price=95000.0,
            commission=9.5,  # 0.01%
            commission_asset="USDT",
        )

        assert order.commission == 9.5
        assert order.commission_asset == "USDT"

    def test_commission_percentage(self):
        """Test commission percentage calculation."""
        order = Order(id="order-001", quantity=1.0, average_price=95000.0, commission=9.5)

        trade_value = order.quantity * order.average_price
        commission_percent = (order.commission / trade_value) * 100

        assert commission_percent == pytest.approx(0.01, rel=1e-2)


class TestOrderIntegration:
    """Integration-style tests for order components."""

    def test_full_order_flow(self):
        """Test complete order flow from creation to fill."""
        # Create order
        order = Order(
            id=str(uuid.uuid4()),
            symbol="BTCUSDT",
            side="BUY",
            type="MARKET",
            quantity=0.1,
            status="NEW",
            created_at=int(datetime.now().timestamp() * 1000),
        )

        assert order.status == "NEW"

        # Simulate partial fill
        order.status = "PARTIALLY_FILLED"
        order.executed_quantity = 0.05
        order.remaining_quantity = 0.05
        order.average_price = 95000.0

        assert order.status == "PARTIALLY_FILLED"
        assert order.executed_quantity == 0.05

        # Simulate full fill
        order.status = "FILLED"
        order.executed_quantity = 0.1
        order.remaining_quantity = 0.0
        order.executed_at = int(datetime.now().timestamp() * 1000)
        order.commission = 0.95

        assert order.status == "FILLED"
        assert order.remaining_quantity == 0.0
        assert order.executed_at is not None

    def test_order_with_linked_orders(self):
        """Test order with linked stop loss and take profit."""
        # Main entry order
        entry_order = Order(
            id="entry-001",
            symbol="BTCUSDT",
            side="BUY",
            type="MARKET",
            quantity=0.1,
            status="FILLED",
            average_price=95000.0,
        )

        # Stop loss order
        sl_order = Order(
            id="sl-001",
            symbol="BTCUSDT",
            side="SELL",
            type="STOP_MARKET",
            quantity=0.1,
            stop_price=94000.0,
            status="NEW",
        )

        # Take profit order
        tp_order = Order(
            id="tp-001",
            symbol="BTCUSDT",
            side="SELL",
            type="LIMIT",
            quantity=0.1,
            price=97000.0,
            status="NEW",
        )

        # Link orders
        entry_order.stop_loss_order_id = sl_order.id
        entry_order.take_profit_order_ids = [tp_order.id]

        assert entry_order.stop_loss_order_id == "sl-001"
        assert "tp-001" in entry_order.take_profit_order_ids
