"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : database
    Project     : Project-103
    Description : Database manager for persistent storage.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, create_engine, desc, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.data.storage.models import BalanceHistoryModel, Base, OrderModel, SignalModel
from src.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database operations for market data."""

    def __init__(
        self,
        database_url: str = "postgresql://mashiron:1452@postgres:5432/project_102",
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 5,
        pool_timeout: int = 30,
    ):
        """
        Initialize database manager.

        All DatabaseManager methods are synchronous and called from the event loop,
        so queries serialize - concurrent demand is 2-3 connections (main loop,
        telegram bot, health checker sharing one manager). Pool defaults sized to that.

        Args:
            database_url: Database connection URL
            echo: Enable SQL echo
            pool_size: Connection pool size
            max_overflow: Max additional connections beyond pool_size
            pool_timeout: Max seconds to wait for a connection from the pool
        """
        try:
            self.engine = create_engine(
                database_url,
                echo=echo,
                pool_pre_ping=True,  # Verify connection before use (prevents stale connections)
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_recycle=3600,  # Recycle connections after 1 hour
                pool_timeout=pool_timeout,
            )
            self.SessionLocal = sessionmaker(bind=self.engine)

            # Create tables (handle duplicate index errors gracefully)
            try:
                Base.metadata.create_all(bind=self.engine)
            except SQLAlchemyError as create_error:
                error_str = str(create_error).lower()
                # Ignore duplicate table/index errors - they mean objects already exist
                if "already exists" in error_str or "duplicate" in error_str:
                    logger.warning(
                        f"Some database objects already exist (this is OK): {create_error}"
                    )
                else:
                    raise

            # Add new columns if they don't exist (for migrations)
            self._ensure_columns_exist()

            # Widen exit_reason column (was VARCHAR(30), now VARCHAR(80))
            try:
                from sqlalchemy import text

                with self.engine.connect() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE trading.signals ALTER COLUMN exit_reason TYPE VARCHAR(80)"
                        )
                    )
                    conn.commit()
            except Exception:
                pass  # Already widened or column doesn't exist yet

            logger.info(f"Connected to database: {database_url}")

        except SQLAlchemyError as e:
            logger.error(f"Database connection error: {e}")
            raise

    def get_session(self) -> Session:
        """Get database session."""
        return self.SessionLocal()

    def _ensure_columns_exist(self) -> None:
        """
        Ensure new columns exist in the database.
        This handles migrations for columns added after initial table creation.
        """
        from sqlalchemy import inspect, text

        # Whitelist of allowed SQL types (prevents SQL injection)
        ALLOWED_TYPES = {"FLOAT", "INTEGER", "VARCHAR", "TEXT", "BOOLEAN", "TIMESTAMP"}

        # Define new columns to add (schema, table_name, column_name, column_type)
        new_columns = [
            ("trading", "signals", "open_fee", "FLOAT"),
            ("trading", "signals", "close_fee", "FLOAT"),
            ("trading", "signals", "funding_fee", "FLOAT"),
            ("trading", "signals", "exchange_pnl", "FLOAT"),
            ("trading", "signals", "net_pnl", "FLOAT"),
        ]

        try:
            inspector = inspect(self.engine)

            for schema_name, table_name, column_name, column_type in new_columns:
                # Validate column type against whitelist
                if column_type.upper() not in ALLOWED_TYPES:
                    logger.warning(f"Skipping invalid column type: {column_type}")
                    continue

                # Validate identifiers (alphanumeric + underscore only)
                if not table_name.replace("_", "").isalnum():
                    logger.warning(f"Skipping invalid table name: {table_name}")
                    continue
                if not column_name.replace("_", "").isalnum():
                    logger.warning(f"Skipping invalid column name: {column_name}")
                    continue

                # Check if table exists in schema
                if table_name not in inspector.get_table_names(schema=schema_name):
                    continue

                # Get existing columns
                existing_columns = [
                    col["name"] for col in inspector.get_columns(table_name, schema=schema_name)
                ]

                # Add column if it doesn't exist
                if column_name not in existing_columns:
                    try:
                        with self.engine.connect() as conn:
                            conn.execute(
                                text(
                                    f"ALTER TABLE {schema_name}.{table_name} ADD COLUMN {column_name} {column_type}"
                                )
                            )
                            conn.commit()
                        logger.info(f"Added column {schema_name}.{table_name}.{column_name}")
                    except Exception as col_error:
                        # Handle race condition: column may have been added by another process
                        if (
                            "already exists" in str(col_error).lower()
                            or "duplicate" in str(col_error).lower()
                        ):
                            logger.debug(
                                f"Column {table_name}.{column_name} already exists (concurrent migration)"
                            )
                        else:
                            logger.warning(
                                f"Failed to add column {table_name}.{column_name}: {col_error}"
                            )

        except Exception as e:
            logger.warning(f"Could not ensure columns exist: {e}")

    # ==================== Signal Methods ====================

    def save_signal(self, signal_data: Dict[str, Any]) -> bool:
        """
        Save signal to database.

        Args:
            signal_data: Signal data dictionary

        Returns:
            True if saved successfully
        """
        session = self.get_session()
        try:
            signal = SignalModel(
                signal_id=signal_data["signal_id"],
                symbol=signal_data["symbol"],
                timestamp=signal_data.get("timestamp", utcnow()),
                signal_type=signal_data["signal_type"],
                strength=signal_data.get("strength"),
                confidence=signal_data.get("confidence"),
                entry_price=signal_data.get("entry_price"),
                stop_loss=signal_data.get("stop_loss"),
                take_profit=signal_data.get("take_profit"),
                risk_reward_ratio=signal_data.get("risk_reward_ratio"),
                position_size_percent=signal_data.get("position_size_percent"),
                strategy_name=signal_data.get("strategy_name"),
                timeframe=signal_data.get("timeframe"),
                indicators_snapshot=signal_data.get("indicators_snapshot"),
                signal_metadata=signal_data.get("metadata"),
                entry_reasons=signal_data.get("entry_reasons"),  # Why signal was generated
                status=signal_data.get("status", "PENDING"),
                exchange=signal_data.get("exchange"),
                source=signal_data.get("source"),
            )
            session.add(signal)
            session.commit()
            logger.info(
                f"Signal saved: {signal_data['signal_id']} {signal_data['symbol']} {signal_data['signal_type']}",
                extra={"log_type": "signal"},
            )
            return True

        except SQLAlchemyError as e:
            session.rollback()
            # Handle duplicate signal_id (already exists in DB)
            if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
                logger.debug(f"Signal already exists: {signal_data['signal_id']}")
                return True  # Not an error - signal is already saved
            logger.error(f"Error saving signal: {e}")
            return False
        finally:
            session.close()

    def update_signal_execution(
        self, signal_id: str, executed_price: float, executed_size: float, order_id: str
    ) -> bool:
        """
        Update signal with execution details.

        Args:
            signal_id: Signal ID
            executed_price: Actual execution price
            executed_size: Executed size
            order_id: Order ID from exchange

        Returns:
            True if updated successfully
        """
        session = self.get_session()
        try:
            signal = session.query(SignalModel).filter(SignalModel.signal_id == signal_id).first()

            if not signal:
                logger.warning(f"Signal not found: {signal_id}")
                return False

            signal.status = "EXECUTED"
            signal.executed_at = utcnow()
            signal.executed_price = executed_price
            signal.executed_size = executed_size
            signal.order_id = order_id

            session.commit()
            logger.debug(
                f"Signal executed: {signal_id} @ {executed_price}", extra={"log_type": "signal"}
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error updating signal execution: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def update_signal_telegram_message_id(self, signal_id: str, telegram_message_id: int) -> bool:
        """
        Update signal with Telegram message ID.

        Args:
            signal_id: Signal ID
            telegram_message_id: Telegram message ID for tracking/editing

        Returns:
            True if updated successfully
        """
        session = self.get_session()
        try:
            signal = session.query(SignalModel).filter(SignalModel.signal_id == signal_id).first()

            if not signal:
                logger.warning(f"Signal not found for telegram update: {signal_id}")
                return False

            signal.telegram_message_id = telegram_message_id
            session.commit()
            logger.debug(
                f"Signal telegram_message_id updated: {signal_id} -> {telegram_message_id}"
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error updating signal telegram_message_id: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def update_signal_outcome(
        self,
        signal_id: str,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        pnl_percent: float,
        fees: float = 0.0,
        max_profit_percent: float = None,
        open_fee: float = None,
        close_fee: float = None,
        funding_fee: float = None,
        exchange_pnl: float = None,
        net_pnl: float = None,
    ) -> bool:
        """
        Update signal with outcome (when position is closed).

        Args:
            signal_id: Signal ID
            exit_price: Exit price
            exit_reason: Reason for exit (STOP_LOSS, TAKE_PROFIT, MANUAL, etc.)
            pnl: Absolute P&L
            pnl_percent: P&L percentage
            fees: Total trading fees
            max_profit_percent: Maximum profit reached before close
            open_fee: Entry commission (USDT)
            close_fee: Exit commission (USDT)
            funding_fee: Total funding fees (USDT)
            exchange_pnl: PnL reported by exchange (USDT)
            net_pnl: Net PnL after fees (USDT)

        Returns:
            True if updated successfully
        """
        session = self.get_session()
        try:
            signal = session.query(SignalModel).filter(SignalModel.signal_id == signal_id).first()

            if not signal:
                logger.warning(f"Signal not found: {signal_id}")
                return False

            signal.status = "CLOSED"
            signal.closed_at = utcnow()
            signal.exit_price = exit_price
            signal.exit_reason = exit_reason
            signal.pnl = pnl
            signal.pnl_percent = pnl_percent
            signal.fees = fees

            if max_profit_percent is not None:
                signal.max_profit_percent = max_profit_percent

            # Store detailed fee breakdown if columns exist
            if open_fee is not None and hasattr(signal, "open_fee"):
                signal.open_fee = open_fee
            if close_fee is not None and hasattr(signal, "close_fee"):
                signal.close_fee = close_fee
            if funding_fee is not None and hasattr(signal, "funding_fee"):
                signal.funding_fee = funding_fee
            if exchange_pnl is not None and hasattr(signal, "exchange_pnl"):
                signal.exchange_pnl = exchange_pnl
            if net_pnl is not None and hasattr(signal, "net_pnl"):
                signal.net_pnl = net_pnl

            # Calculate holding time
            if signal.executed_at:
                holding_time = (utcnow() - signal.executed_at).total_seconds()
                signal.holding_time_seconds = int(holding_time)

            session.commit()

            # Enhanced logging with fees breakdown
            max_profit_str = f", Max: {max_profit_percent:.2f}%" if max_profit_percent else ""
            fees_str = f", Fees: {fees:.4f}" if fees > 0 else ""
            logger.info(
                f"Signal closed: {signal_id} | Exit: {exit_reason} | "
                f"PnL: {pnl:.2f} ({pnl_percent:.2f}%){max_profit_str}{fees_str}",
                extra={"log_type": "signal"},
            )
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error updating signal outcome: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def get_signals(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        status: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get signals from database.

        Args:
            symbol: Filter by symbol
            strategy: Filter by strategy name
            status: Filter by status
            start_time: Start time filter
            end_time: End time filter
            limit: Maximum number of signals

        Returns:
            List of signal dictionaries
        """
        session = self.get_session()

        try:
            query = session.query(SignalModel)

            if symbol:
                query = query.filter(SignalModel.symbol == symbol)

            if strategy:
                query = query.filter(SignalModel.strategy_name == strategy)

            if status:
                query = query.filter(SignalModel.status == status)

            if start_time:
                query = query.filter(SignalModel.timestamp >= start_time)

            if end_time:
                query = query.filter(SignalModel.timestamp <= end_time)

            query = query.order_by(desc(SignalModel.timestamp)).limit(limit)

            signals = query.all()
            return [signal.to_dict() for signal in signals]

        except SQLAlchemyError as e:
            logger.error(f"Error getting signals: {e}")
            return []
        finally:
            session.close()

    def get_signal_stats(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Get signal statistics using SQL aggregation (optimized, no N+1 queries).

        Args:
            symbol: Filter by symbol
            strategy: Filter by strategy name
            start_time: Start time filter
            end_time: End time filter

        Returns:
            Statistics dictionary
        """
        session = self.get_session()

        try:
            from sqlalchemy import case, func

            # Build base filter conditions
            filters = []
            if symbol:
                filters.append(SignalModel.symbol == symbol)
            if strategy:
                filters.append(SignalModel.strategy_name == strategy)
            if start_time:
                filters.append(SignalModel.timestamp >= start_time)
            if end_time:
                filters.append(SignalModel.timestamp <= end_time)

            # Query 1: Get aggregated stats for closed signals (single query)
            closed_filters = filters + [SignalModel.status == "CLOSED"]

            stats_query = (
                session.query(
                    func.count(SignalModel.id).label("total"),
                    func.sum(SignalModel.pnl).label("total_pnl"),
                    func.avg(SignalModel.pnl).label("avg_pnl"),
                    func.avg(SignalModel.holding_time_seconds).label("avg_holding_time"),
                    # Count winners (pnl > 0)
                    func.sum(case((SignalModel.pnl > 0, 1), else_=0)).label("winners"),
                    # Count losers (pnl < 0)
                    func.sum(case((SignalModel.pnl < 0, 1), else_=0)).label("losers"),
                    # Sum of winning trades
                    func.sum(case((SignalModel.pnl > 0, SignalModel.pnl), else_=0)).label(
                        "total_winners_pnl"
                    ),
                    # Sum of losing trades
                    func.sum(case((SignalModel.pnl < 0, SignalModel.pnl), else_=0)).label(
                        "total_losers_pnl"
                    ),
                )
                .filter(*closed_filters)
                .first()
            )

            total = stats_query.total or 0
            total_pnl = float(stats_query.total_pnl or 0)
            avg_pnl = float(stats_query.avg_pnl or 0)
            avg_holding_time = float(stats_query.avg_holding_time or 0)
            winners_count = int(stats_query.winners or 0)
            losers_count = int(stats_query.losers or 0)
            total_winners_pnl = float(stats_query.total_winners_pnl or 0)
            total_losers_pnl = float(stats_query.total_losers_pnl or 0)

            win_rate = winners_count / total if total > 0 else 0
            avg_winner = total_winners_pnl / winners_count if winners_count > 0 else 0
            avg_loser = total_losers_pnl / losers_count if losers_count > 0 else 0
            profit_factor = abs(avg_winner / avg_loser) if avg_loser != 0 else 0

            # Query 2: Count by status (single query with group by)
            status_query = (
                session.query(SignalModel.status, func.count(SignalModel.id).label("count"))
                .filter(*filters)
                .group_by(SignalModel.status)
                .all()
            )

            status_counts = {row.status: row.count for row in status_query}
            total_signals = sum(status_counts.values())

            return {
                "total_signals": total_signals,
                "closed_signals": total,
                "winners": winners_count,
                "losers": losers_count,
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "avg_pnl": avg_pnl,
                "avg_winner": avg_winner,
                "avg_loser": avg_loser,
                "profit_factor": profit_factor,
                "avg_holding_time_seconds": avg_holding_time,
                "by_status": status_counts,
            }

        except SQLAlchemyError as e:
            logger.error(f"Error getting signal stats: {e}")
            return {}
        finally:
            session.close()

    async def close(self) -> None:
        """Close database connection."""
        try:
            self.engine.dispose()
            logger.info("Database connection closed")
        except Exception as e:
            logger.error(f"Error closing database: {e}")

    # ==================== Order Tracking Methods ====================

    def save_order(self, order_data: Dict[str, Any]) -> bool:
        """
        Save order to database.

        Args:
            order_data: Order data dictionary

        Returns:
            True if saved successfully
        """
        session = self.get_session()
        try:
            order = OrderModel(
                signal_id=order_data["signal_id"],
                internal_order_id=order_data.get("internal_order_id"),
                exchange_order_id=order_data.get("exchange_order_id"),
                client_order_id=order_data.get("client_order_id"),
                order_type=order_data["order_type"],
                order_category=order_data.get("order_category"),
                side=order_data.get("side"),
                symbol=order_data["symbol"],
                price=order_data.get("price"),
                trigger_price=order_data.get("trigger_price"),
                quantity=order_data.get("quantity"),
                status=order_data.get("status", "PENDING"),
                position_idx=order_data.get("position_idx"),
                reduce_only=order_data.get("reduce_only", False),
                order_metadata=order_data.get("metadata"),
            )
            session.add(order)
            session.commit()
            logger.debug(
                f"Order saved: {order_data.get('order_type')} for signal {order_data['signal_id'][:8]}..."
            )
            return True

        except SQLAlchemyError as e:
            session.rollback()
            # Handle duplicate order (already exists in DB)
            if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
                logger.debug(
                    f"Order already exists: {order_data.get('order_type')} for {order_data['signal_id'][:8]}..."
                )
                return True
            logger.error(f"Error saving order: {e}")
            return False
        finally:
            session.close()

    def update_order_status(
        self,
        signal_id: str,
        order_type: str,
        status: str,
        exchange_order_id: str = None,
        filled_price: float = None,
        filled_quantity: float = None,
        commission: float = None,
        status_reason: str = None,
    ) -> bool:
        """
        Update order status.

        Args:
            signal_id: Signal ID
            order_type: Order type (ENTRY, STOP_LOSS, TAKE_PROFIT_1, etc.)
            status: New status (PLACED, FILLED, CANCELLED, etc.)
            exchange_order_id: Exchange order ID (if just placed)
            filled_price: Average fill price
            filled_quantity: Filled quantity
            commission: Trading fee
            status_reason: Reason for status change

        Returns:
            True if updated successfully
        """
        session = self.get_session()
        try:
            order = (
                session.query(OrderModel)
                .filter(OrderModel.signal_id == signal_id, OrderModel.order_type == order_type)
                .order_by(OrderModel.created_at.desc())
                .first()
            )

            if not order:
                logger.debug(f"Order not found: {order_type} for signal {signal_id[:8]}...")
                return False

            order.status = status
            order.updated_at = utcnow()

            if exchange_order_id:
                order.exchange_order_id = exchange_order_id
            if filled_price:
                order.average_fill_price = filled_price
            if filled_quantity is not None:
                order.filled_quantity = filled_quantity
            if commission is not None:
                order.commission = commission
            if status_reason:
                order.status_reason = status_reason

            # Update timestamps based on status
            if status == "PLACED" and not order.placed_at:
                order.placed_at = utcnow()
            elif status == "FILLED":
                order.filled_at = utcnow()
            elif status == "CANCELLED":
                order.cancelled_at = utcnow()

            session.commit()
            logger.debug(f"Order updated: {order_type} -> {status} for signal {signal_id[:8]}...")
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error updating order: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def order_exists(self, signal_id: str, order_type: str) -> bool:
        """Whether this order exists in the database.

        Needed to tell "row not found" from "database unavailable":
        update_order_status returns False in both cases, but for the WAL they
        differ — the latter must be retried, the former is pointless to retry.
        """
        session = self.get_session()
        try:
            return (
                session.query(OrderModel.id)
                .filter(OrderModel.signal_id == signal_id, OrderModel.order_type == order_type)
                .first()
                is not None
            )
        except SQLAlchemyError as e:
            logger.error(f"Error checking order existence: {e}")
            return True  # the database is silent — assume the order may exist
        finally:
            session.close()

    def get_orders_by_signal(self, signal_id: str) -> List[Dict[str, Any]]:
        """
        Get all orders for a signal.

        Args:
            signal_id: Signal ID

        Returns:
            List of order dictionaries
        """
        session = self.get_session()
        try:
            orders = (
                session.query(OrderModel)
                .filter(OrderModel.signal_id == signal_id)
                .order_by(OrderModel.created_at)
                .all()
            )

            return [order.to_dict() for order in orders]

        except SQLAlchemyError as e:
            logger.error(f"Error getting orders: {e}")
            return []
        finally:
            session.close()

    def get_order_stats(
        self, symbol: str = None, start_time: datetime = None, end_time: datetime = None
    ) -> Dict[str, Any]:
        """
        Get order statistics.

        Args:
            symbol: Filter by symbol
            start_time: Start time filter
            end_time: End time filter

        Returns:
            Statistics dictionary
        """
        session = self.get_session()
        try:
            from sqlalchemy import func

            # Build filters
            filters = []
            if symbol:
                filters.append(OrderModel.symbol == symbol)
            if start_time:
                filters.append(OrderModel.created_at >= start_time)
            if end_time:
                filters.append(OrderModel.created_at <= end_time)

            # Get counts by status and type
            stats_query = (
                session.query(
                    OrderModel.order_type,
                    OrderModel.status,
                    func.count(OrderModel.id).label("count"),
                )
                .filter(*filters)
                .group_by(OrderModel.order_type, OrderModel.status)
                .all()
            )

            # Organize results
            by_type = {}
            by_status = {}
            total = 0

            for row in stats_query:
                order_type = row.order_type
                status = row.status
                count = row.count
                total += count

                # By type
                if order_type not in by_type:
                    by_type[order_type] = {"total": 0, "filled": 0, "cancelled": 0}
                by_type[order_type]["total"] += count
                if status == "FILLED":
                    by_type[order_type]["filled"] += count
                elif status == "CANCELLED":
                    by_type[order_type]["cancelled"] += count

                # By status
                by_status[status] = by_status.get(status, 0) + count

            # Calculate fill rates
            for order_type, data in by_type.items():
                data["fill_rate"] = data["filled"] / data["total"] if data["total"] > 0 else 0

            return {
                "total_orders": total,
                "by_type": by_type,
                "by_status": by_status,
            }

        except SQLAlchemyError as e:
            logger.error(f"Error getting order stats: {e}")
            return {}
        finally:
            session.close()

    # ==================== Balance History Methods ====================

    def save_balance(
        self,
        balance: float,
        event_type: str,
        session_id: str = None,
        daily_start_balance: float = None,
        max_balance: float = None,
        daily_pnl: float = None,
        daily_pnl_percent: float = None,
        total_pnl: float = None,
        total_pnl_percent: float = None,
        trade_symbol: str = None,
        trade_pnl: float = None,
        total_trades: int = None,
        winning_trades: int = None,
        losing_trades: int = None,
    ) -> bool:
        """
        Save balance snapshot to database.

        Args:
            balance: Current balance
            event_type: Type of event (SESSION_START, SESSION_END, TRADE_CLOSE, DAILY_RESET)
            session_id: Unique session identifier
            daily_start_balance: Balance at start of trading day
            max_balance: Peak balance
            daily_pnl: Daily P&L in USDT
            daily_pnl_percent: Daily P&L percentage
            total_pnl: Total P&L from initial
            total_pnl_percent: Total P&L percentage
            trade_symbol: Symbol if event is TRADE_CLOSE
            trade_pnl: Trade P&L if event is TRADE_CLOSE
            total_trades: Total number of trades
            winning_trades: Number of winning trades
            losing_trades: Number of losing trades

        Returns:
            True if saved successfully
        """
        session = self.get_session()
        try:
            record = BalanceHistoryModel(
                timestamp=utcnow(),
                balance=balance,
                daily_start_balance=daily_start_balance,
                max_balance=max_balance,
                session_id=session_id,
                event_type=event_type,
                daily_pnl=daily_pnl,
                daily_pnl_percent=daily_pnl_percent,
                total_pnl=total_pnl,
                total_pnl_percent=total_pnl_percent,
                trade_symbol=trade_symbol,
                trade_pnl=trade_pnl,
                total_trades=total_trades,
                winning_trades=winning_trades,
                losing_trades=losing_trades,
            )
            session.add(record)
            session.commit()
            logger.debug(f"Balance saved: ${balance:.2f} ({event_type})")
            return True

        except SQLAlchemyError as e:
            logger.error(f"Error saving balance: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def get_latest_balance(self) -> Optional[Dict[str, Any]]:
        """
        Get the most recent balance record.

        Returns:
            Latest balance record as dictionary, or None if no records exist
        """
        session = self.get_session()
        try:
            record = (
                session.query(BalanceHistoryModel)
                .order_by(desc(BalanceHistoryModel.timestamp))
                .first()
            )

            return record.to_dict() if record else None

        except SQLAlchemyError as e:
            logger.error(f"Error getting latest balance: {e}")
            return None
        finally:
            session.close()

    def get_balance_history(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        event_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        Get balance history records.

        Args:
            start_time: Start time filter
            end_time: End time filter
            event_type: Filter by event type
            limit: Maximum number of records

        Returns:
            List of balance records
        """
        session = self.get_session()
        try:
            query = session.query(BalanceHistoryModel)

            if start_time:
                query = query.filter(BalanceHistoryModel.timestamp >= start_time)

            if end_time:
                query = query.filter(BalanceHistoryModel.timestamp <= end_time)

            if event_type:
                query = query.filter(BalanceHistoryModel.event_type == event_type)

            query = query.order_by(desc(BalanceHistoryModel.timestamp)).limit(limit)

            records = query.all()
            return [r.to_dict() for r in records]

        except SQLAlchemyError as e:
            logger.error(f"Error getting balance history: {e}")
            return []
        finally:
            session.close()

    def get_session_start_balance(self, session_id: str) -> Optional[float]:
        """
        Get the starting balance for a specific session.

        Args:
            session_id: Session identifier

        Returns:
            Starting balance or None
        """
        session = self.get_session()
        try:
            record = (
                session.query(BalanceHistoryModel)
                .filter(
                    and_(
                        BalanceHistoryModel.session_id == session_id,
                        BalanceHistoryModel.event_type == "SESSION_START",
                    )
                )
                .first()
            )

            return record.balance if record else None

        except SQLAlchemyError as e:
            logger.error(f"Error getting session start balance: {e}")
            return None
        finally:
            session.close()
