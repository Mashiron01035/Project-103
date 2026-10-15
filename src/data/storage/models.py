"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : models
    Project     : Project-103
    Description : SQLAlchemy ORM models for database storage.

    SPDX-License-Identifier: AGPL-3.0

"""

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import declarative_base  # moved here in SQLAlchemy 2.0

from src.utils.datetime_utils import utcnow

Base = declarative_base()


class BalanceHistoryModel(Base):
    """Balance history model for tracking account balance over time."""

    __tablename__ = "balance_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=utcnow)

    # Balance data
    balance = Column(Float, nullable=False)
    daily_start_balance = Column(Float)  # Balance at start of trading day
    max_balance = Column(Float)  # Peak balance (for drawdown calculation)

    # Session info
    session_id = Column(String(50))  # Unique session identifier
    event_type = Column(String(30))  # SESSION_START, SESSION_END, TRADE_CLOSE, DAILY_RESET

    # P&L info
    daily_pnl = Column(Float)  # P&L for current day
    daily_pnl_percent = Column(Float)
    total_pnl = Column(Float)  # Total P&L from initial balance
    total_pnl_percent = Column(Float)

    # Trade info (if event is TRADE_CLOSE)
    trade_symbol = Column(String(20))
    trade_pnl = Column(Float)

    # Statistics snapshot
    total_trades = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)

    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index("idx_balhist_timestamp", "timestamp"),
        Index("idx_balhist_session", "session_id"),
        Index("idx_balhist_event", "event_type"),
        {"schema": "trading"},
    )

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": int(self.timestamp.timestamp() * 1000) if self.timestamp else None,
            "balance": self.balance,
            "daily_start_balance": self.daily_start_balance,
            "max_balance": self.max_balance,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_percent": self.daily_pnl_percent,
            "total_pnl": self.total_pnl,
            "total_pnl_percent": self.total_pnl_percent,
            "trade_symbol": self.trade_symbol,
            "trade_pnl": self.trade_pnl,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
        }


class SignalModel(Base):
    """Trading signal model for storing generated signals and their outcomes."""

    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(String(50), unique=True, nullable=False)  # UUID from Signal model
    symbol = Column(String(20), nullable=False)
    timestamp = Column(DateTime, nullable=False)

    # Signal parameters
    signal_type = Column(String(20), nullable=False)  # LONG, SHORT, BUY, SELL
    strength = Column(String(20))  # WEAK, MODERATE, STRONG, VERY_STRONG
    confidence = Column(Float)

    # Price levels
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(JSON)  # List of TP levels

    # Risk management
    risk_reward_ratio = Column(Float)
    position_size_percent = Column(Float)

    # Strategy info
    strategy_name = Column(String(50))
    timeframe = Column(String(10))

    # Indicators snapshot at signal time
    indicators_snapshot = Column(JSON)
    signal_metadata = Column(JSON)  # renamed from 'metadata' (reserved in SQLAlchemy)

    # Entry reasons (why signal was generated)
    entry_reasons = Column(JSON)  # List of reasons: ['Strong uptrend', 'MACD bullish', ...]

    # Status tracking
    status = Column(String(20), default="PENDING")  # PENDING, EXECUTED, REJECTED, EXPIRED, CLOSED

    # Execution details (filled after order execution)
    executed_at = Column(DateTime)
    executed_price = Column(Float)
    executed_size = Column(Float)
    order_id = Column(String(50))

    # Telegram notification
    telegram_message_id = Column(Integer)  # Message ID for editing/tracking

    # Outcome (filled after position closed)
    closed_at = Column(DateTime)
    exit_price = Column(Float)
    exit_reason = Column(
        String(80)
    )  # STOP_LOSS, TAKE_PROFIT, MANUAL, TIMEOUT, FAN_BATCH_CLOSE, etc.

    # P&L
    pnl = Column(Float)  # Absolute P&L
    pnl_percent = Column(Float)  # P&L percentage
    max_profit_percent = Column(Float)  # Maximum profit reached before close
    fees = Column(Float)  # Total trading fees (sum of all)

    # Detailed fees breakdown (from exchange)
    open_fee = Column(Float)  # Entry commission (USDT)
    close_fee = Column(Float)  # Exit commission (USDT)
    funding_fee = Column(Float)  # Total funding fees during position (USDT)
    exchange_pnl = Column(Float)  # PnL reported by exchange (USDT)
    net_pnl = Column(Float)  # Net PnL = exchange_pnl (real profit)

    # Duration
    holding_time_seconds = Column(Integer)

    exchange = Column(String(50))
    source = Column(String(50))

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("idx_sig_symbol_ts", "symbol", "timestamp"),
        Index("idx_sig_strategy", "strategy_name"),
        Index("idx_sig_status", "status"),
        Index("idx_sig_signal_id", "signal_id"),
        {"schema": "trading"},
    )

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "timestamp": int(self.timestamp.timestamp() * 1000) if self.timestamp else None,
            "signal_type": self.signal_type,
            "strength": self.strength,
            "confidence": self.confidence,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward_ratio": self.risk_reward_ratio,
            "position_size_percent": self.position_size_percent,
            "strategy_name": self.strategy_name,
            "timeframe": self.timeframe,
            "entry_reasons": self.entry_reasons,
            "status": self.status,
            "executed_at": int(self.executed_at.timestamp() * 1000) if self.executed_at else None,
            "executed_price": self.executed_price,
            "executed_size": self.executed_size,
            "order_id": self.order_id,
            "telegram_message_id": self.telegram_message_id,
            "closed_at": int(self.closed_at.timestamp() * 1000) if self.closed_at else None,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "max_profit_percent": self.max_profit_percent,
            "fees": self.fees,
            "open_fee": self.open_fee,
            "close_fee": self.close_fee,
            "funding_fee": self.funding_fee,
            "exchange_pnl": self.exchange_pnl,
            "net_pnl": self.net_pnl,
            "holding_time_seconds": self.holding_time_seconds,
            "exchange": self.exchange,
            # Recovery after a restart (position_recovery -> tracked.signal = this dict)
            # reads signal['metadata']: time_exit_minutes, fan_batch_id, etc. Without the
            # key a recovered position lost its time exit and its grid batch state.
            "metadata": self.signal_metadata or {},
        }


class OrderModel(Base):
    """
    Order tracking model for storing all orders related to signals/positions.
    Tracks entry orders, SL orders, TP orders and their statuses.
    """

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Link to signal
    signal_id = Column(String(50), nullable=False)  # Links to SignalModel.signal_id

    # Order identification
    internal_order_id = Column(String(100))  # Internal ID (e.g., LMT_xxx)
    exchange_order_id = Column(String(50))  # Order ID from exchange (Bybit orderId)
    client_order_id = Column(String(50))  # Client order ID (orderLinkId)

    # Order type and details
    order_type = Column(
        String(20), nullable=False
    )  # ENTRY, STOP_LOSS, TAKE_PROFIT_1, TAKE_PROFIT_2, etc.
    order_category = Column(String(20))  # MARKET, LIMIT, STOP_MARKET, STOP_LIMIT
    side = Column(String(10))  # BUY, SELL
    symbol = Column(String(20), nullable=False)

    # Prices
    price = Column(Float)  # Order price (for limit orders)
    trigger_price = Column(Float)  # Trigger price (for stop orders)
    average_fill_price = Column(Float)  # Actual fill price

    # Quantities
    quantity = Column(Float)  # Order quantity
    filled_quantity = Column(Float, default=0.0)  # Filled quantity
    remaining_quantity = Column(Float)  # Remaining to fill

    # Status tracking
    status = Column(
        String(20), default="PENDING"
    )  # PENDING, PLACED, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, EXPIRED
    status_reason = Column(
        String(100)
    )  # Why status changed (e.g., "User cancelled", "Hit SL", etc.)

    # Position info (for SL/TP)
    position_idx = Column(Integer)  # 1=Long, 2=Short (Bybit hedge mode)
    reduce_only = Column(Boolean, default=False)

    # Fees
    commission = Column(Float, default=0.0)  # Trading fee for this order

    # Timestamps
    created_at = Column(DateTime, default=utcnow)  # When order was created locally
    placed_at = Column(DateTime)  # When order was sent to exchange
    filled_at = Column(DateTime)  # When order was fully filled
    cancelled_at = Column(DateTime)  # When order was cancelled
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Metadata
    order_metadata = Column(JSON)  # Additional info (partial fills, amendments, etc.)

    __table_args__ = (
        Index("idx_ord_signal_id", "signal_id"),
        Index("idx_ord_exchange_id", "exchange_order_id"),
        Index("idx_ord_symbol", "symbol"),
        Index("idx_ord_type", "order_type"),
        Index("idx_ord_status", "status"),
        Index("idx_ord_created", "created_at"),
        {"schema": "trading"},
    )

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "signal_id": self.signal_id,
            "internal_order_id": self.internal_order_id,
            "exchange_order_id": self.exchange_order_id,
            "client_order_id": self.client_order_id,
            "order_type": self.order_type,
            "order_category": self.order_category,
            "side": self.side,
            "symbol": self.symbol,
            "price": self.price,
            "trigger_price": self.trigger_price,
            "average_fill_price": self.average_fill_price,
            "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "remaining_quantity": self.remaining_quantity,
            "status": self.status,
            "status_reason": self.status_reason,
            "position_idx": self.position_idx,
            "reduce_only": self.reduce_only,
            "commission": self.commission,
            "created_at": int(self.created_at.timestamp() * 1000) if self.created_at else None,
            "placed_at": int(self.placed_at.timestamp() * 1000) if self.placed_at else None,
            "filled_at": int(self.filled_at.timestamp() * 1000) if self.filled_at else None,
            "cancelled_at": int(self.cancelled_at.timestamp() * 1000)
            if self.cancelled_at
            else None,
            "updated_at": int(self.updated_at.timestamp() * 1000) if self.updated_at else None,
            "order_metadata": self.order_metadata,
        }
