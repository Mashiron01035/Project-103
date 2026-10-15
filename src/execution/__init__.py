"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : __init__
    Project     : Project-103
    Description : Execution module for Trading Bot system. This module handles order creation, sending, management and position tracking.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.execution.connectors import BaseConnector, BybitConnector, MockConnector, MT5Connector
from src.execution.limit_order_manager import (
    LimitOrderManager,
    PendingLimitOrder,
    PendingOrderStatus,
)
from src.execution.order_builder import OrderBuildContext, OrderBuilder, OrderTemplate
from src.execution.order_manager import (
    Order,
    OrderBookSnapshot,
    OrderCancelRequest,
    OrderManager,
    OrderModifyRequest,
    OrderRequest,
    OrderResult,
    PositionCloseRequest,
)
from src.execution.order_validator import (
    OrderValidator,
    ValidationContext,
    ValidationError,
    ValidationResult,
)
from src.execution.position_tracker import (
    Position,
    PositionMetrics,
    PositionStatus,
    PositionTracker,
    PositionUpdate,
)
from src.execution.smart_entry import SmartEntryAnalyzer, SmartEntryResult
from src.execution.trailing_stop import (
    BreakEvenConfig,
    BreakEvenManager,
    BreakEvenState,
    BreakEvenStatus,
    ReentryOrder,
    TrailingStopConfig,
    TrailingStopManager,
    TrailingStopStatus,
)

__version__ = "1.0.0"
__author__ = "Trading Bot Team"

__all__ = [
    # Order Management
    "OrderManager",
    "OrderRequest",
    "OrderModifyRequest",
    "OrderCancelRequest",
    "PositionCloseRequest",
    "Order",
    "OrderResult",
    "OrderBookSnapshot",
    # Order Building
    "OrderBuilder",
    "OrderBuildContext",
    "OrderTemplate",
    # Order Validation
    "OrderValidator",
    "ValidationContext",
    "ValidationResult",
    "ValidationError",
    # Position Tracking
    "PositionTracker",
    "Position",
    "PositionUpdate",
    "PositionStatus",
    "PositionMetrics",
    # Trailing Stop
    "TrailingStopManager",
    "TrailingStopConfig",
    "TrailingStopStatus",
    # Break-Even Stop
    "BreakEvenManager",
    "BreakEvenConfig",
    "BreakEvenStatus",
    "BreakEvenState",
    "ReentryOrder",
    # Connectors
    "BaseConnector",
    "BybitConnector",
    "MT5Connector",
    "MockConnector",
    # Smart Entry
    "SmartEntryAnalyzer",
    "SmartEntryResult",
    # Limit Order Manager
    "LimitOrderManager",
    "PendingLimitOrder",
    "PendingOrderStatus",
]
