"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 11.01.2026.
    File        : position_recovery.py
    Project     : Project-103
    Description : Recovers open positions after a bot restart by reconciling exchange positions with executed signals in the database and restoring tracked-signal state.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)


@dataclass
class RecoveryResult:
    """Result of position recovery attempt."""

    recovered: List[Dict[str, Any]]  # Successfully recovered positions
    orphans: List[Dict[str, Any]]  # Positions on exchange but not in DB
    stale: List[Dict[str, Any]]  # Signals in DB but no position on exchange
    errors: List[str]  # Any errors during recovery


class PositionRecoveryManager:
    """
    Manages position recovery after bot restart.

    Compares exchange positions with database signals to restore
    tracking state for open positions.
    """

    def __init__(self, connector, database, signal_lifecycle_manager=None):
        """
        Initialize PositionRecoveryManager.

        Args:
            connector: Exchange connector (BybitConnector)
            database: Database instance for signal queries
            signal_lifecycle_manager: Optional SLM to restore positions into
        """
        self.connector = connector
        self.database = database
        self.signal_lifecycle = signal_lifecycle_manager

    async def recover_positions(self, recover_orphans: bool = True) -> RecoveryResult:
        """
        Main recovery method. Compares exchange positions with DB and recovers.

        Args:
            recover_orphans: If True, also recover positions that exist on exchange
                           but not in database (e.g., from previous sessions when DB was unavailable)

        Returns:
            RecoveryResult with details of recovery attempt
        """
        result = RecoveryResult(recovered=[], orphans=[], stale=[], errors=[])

        try:
            # 1. Get open positions from exchange
            exchange_positions = await self._get_exchange_positions()

            # 2. Get open signals from database
            db_signals = await self._get_db_open_signals()

            # Quick path: nothing to recover
            if not exchange_positions and not db_signals:
                if not self.database:
                    logger.warning(
                        "Position recovery: database unavailable, no exchange positions found"
                    )
                else:
                    logger.info("Position recovery: no open positions, no pending signals")
                return result

            # Verbose path: there's something to recover
            logger.info("=" * 50)
            logger.info("POSITION RECOVERY STARTING")
            logger.info("=" * 50)
            logger.info(f"Found {len(exchange_positions)} open positions on exchange")
            if self.database:
                logger.info(f"Found {len(db_signals)} EXECUTED signals in database")
            else:
                logger.warning("Database unavailable - will recover positions from exchange only")

            # 3. Match and recover
            await self._match_and_recover(exchange_positions, db_signals, result, recover_orphans)

            # 4. Log summary
            self._log_summary(result)

        except Exception as e:
            error_msg = f"Recovery failed: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)

        return result

    async def _get_exchange_positions(self) -> List[Dict[str, Any]]:
        """Fetch all open positions from exchange."""
        try:
            positions = await self.connector.get_positions()

            # Filter only positions with size > 0
            open_positions = [p for p in positions if p.get("size", 0) > 0]

            for pos in open_positions:
                logger.debug(
                    f"  Exchange: {pos['side']} {pos['symbol']} | "
                    f"Size: {pos['size']} | Entry: {pos['entry_price']:.4f}"
                )

            return open_positions

        except Exception as e:
            logger.error(f"Failed to get exchange positions: {e}")
            return []

    async def _get_db_open_signals(self) -> List[Dict[str, Any]]:
        """Get all EXECUTED signals from database that haven't been closed."""
        try:
            if not self.database:
                logger.warning("No database connection, cannot query signals")
                return []

            # Query signals with status EXECUTED and no closed_at
            signals = self.database.get_signals(status="EXECUTED", limit=100)

            # Filter only those without closed_at (still open)
            open_signals = []
            for sig in signals:
                # sig is a dict from database.get_signals()
                closed_at = sig.get("closed_at")
                if closed_at is None:
                    signal_id = sig.get("signal_id")

                    # Get exchange_order_id from orders table (ENTRY order)
                    exchange_order_id = None
                    try:
                        orders = self.database.get_orders_by_signal(signal_id)
                        for order in orders:
                            if order.get("order_type") == "ENTRY" and order.get(
                                "exchange_order_id"
                            ):
                                exchange_order_id = order.get("exchange_order_id")
                                break
                    except Exception as e:
                        logger.debug(f"Could not get orders for signal {signal_id}: {e}")

                    open_signals.append(
                        {
                            "signal_id": signal_id,
                            "symbol": sig.get("symbol"),
                            "side": sig.get("signal_type"),  # BUY or SELL
                            "entry_price": sig.get("executed_price") or sig.get("entry_price", 0),
                            "stop_loss": sig.get("stop_loss", 0),
                            "take_profit": sig.get("take_profit", []),
                            "order_id": sig.get("order_id"),
                            "exchange_order_id": exchange_order_id,
                            "strategy_name": sig.get("strategy_name", ""),
                            "executed_at": sig.get("executed_at"),
                            "raw_signal": sig,
                        }
                    )

            for sig in open_signals:
                logger.debug(
                    f"  DB Signal: {sig['side']} {sig['symbol']} | "
                    f"Entry: {sig['entry_price']:.4f} | ID: {sig['signal_id'][:8]}..."
                )

            return open_signals

        except Exception as e:
            logger.error(f"Failed to get DB signals: {e}")
            return []

    async def _match_and_recover(
        self,
        exchange_positions: List[Dict[str, Any]],
        db_signals: List[Dict[str, Any]],
        result: RecoveryResult,
        recover_orphans: bool = True,
    ) -> None:
        """Match exchange positions with DB signals and recover."""

        # Create lookup by (symbol, side)
        db_by_symbol_side: Dict[Tuple[str, str], List[Dict]] = {}
        for sig in db_signals:
            key = (sig["symbol"], sig["side"])
            if key not in db_by_symbol_side:
                db_by_symbol_side[key] = []
            db_by_symbol_side[key].append(sig)

        matched_signal_ids = set()

        # Process each exchange position
        for pos in exchange_positions:
            symbol = pos["symbol"]
            # Convert exchange side to signal side
            # Exchange: "Buy" for LONG, "Sell" for SHORT
            # Signal: "BUY" or "SELL"
            pos_side = pos["side"].upper()
            if pos_side == "BUY":
                signal_side = "BUY"
            else:
                signal_side = "SELL"

            key = (symbol, signal_side)

            # Find matching signal(s)
            matching_signals = db_by_symbol_side.get(key, [])

            if matching_signals:
                # Take the most recent signal for this symbol/side
                best_match = await self._find_best_match(pos, matching_signals)

                if best_match:
                    matched_signal_ids.add(best_match["signal_id"])

                    # Recover this position
                    recovery_info = await self._recover_position(pos, best_match)
                    result.recovered.append(recovery_info)
                else:
                    # No good match found - try to recover as orphan
                    if recover_orphans:
                        recovery_info = await self._recover_orphan_position(pos)
                        if recovery_info.get("recovered"):
                            result.recovered.append(recovery_info)
                        else:
                            result.orphans.append(
                                {
                                    "position": pos,
                                    "reason": "No matching signal found (price mismatch?)",
                                }
                            )
                    else:
                        result.orphans.append(
                            {
                                "position": pos,
                                "reason": "No matching signal found (price mismatch?)",
                            }
                        )
            else:
                # No signal in DB for this position - recover from exchange data if enabled
                if recover_orphans:
                    recovery_info = await self._recover_orphan_position(pos)
                    if recovery_info.get("recovered"):
                        result.recovered.append(recovery_info)
                        logger.info(
                            f"✓ Recovered orphan position: {signal_side} {symbol} @ {pos['entry_price']:.4f}"
                        )
                    else:
                        result.orphans.append(
                            {
                                "position": pos,
                                "reason": "No signal in database for this symbol/side",
                            }
                        )
                else:
                    result.orphans.append(
                        {"position": pos, "reason": "No signal in database for this symbol/side"}
                    )

        # Find stale signals (in DB but not on exchange)
        for sig in db_signals:
            if sig["signal_id"] not in matched_signal_ids:
                result.stale.append(
                    {"signal": sig, "reason": "Signal in DB but no matching position on exchange"}
                )

                # Update DB to mark as closed (position was closed externally)
                await self._mark_signal_closed(sig, "EXTERNAL_CLOSE")

    async def _find_best_match(
        self, position: Dict[str, Any], signals: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Find the best matching signal for a position.

        Matches by:
        - exchange_order_id if available (verified with exchange)
        - entry price proximity
        - most recent execution time
        """
        pos_entry = position.get("entry_price", 0)
        pos_symbol = position.get("symbol")

        # First, try to match by exchange_order_id
        for sig in signals:
            exchange_order_id = sig.get("exchange_order_id")
            if exchange_order_id and not exchange_order_id.startswith("PAPER_"):
                try:
                    # Verify order status on exchange
                    order_status = await self.connector.get_order_status(
                        exchange_order_id, pos_symbol
                    )
                    if order_status and order_status.get("status") in ("Filled", "FILLED"):
                        logger.debug(
                            f"Matched signal {sig['signal_id'][:8]}... by exchange_order_id {exchange_order_id}"
                        )
                        return sig
                except Exception as e:
                    logger.debug(f"Could not verify order {exchange_order_id}: {e}")

        # Match by entry price proximity (within 2%)
        candidates = []
        for sig in signals:
            sig_entry = sig.get("entry_price", 0)
            if sig_entry > 0 and pos_entry > 0:
                price_diff = abs(pos_entry - sig_entry) / sig_entry * 100
                if price_diff <= 2.0:  # Within 2% of entry price
                    candidates.append((sig, price_diff))

        if candidates:
            # Return the one with closest price
            candidates.sort(key=lambda x: x[1])
            return candidates[0][0]

        # If no price match, return most recent
        if signals:

            def get_executed_at_key(x):
                """Convert executed_at to comparable value (timestamp in ms)."""
                val = x.get("executed_at")
                if isinstance(val, (int, float)):
                    return val
                elif isinstance(val, datetime):
                    return val.timestamp() * 1000
                return 0

            signals_sorted = sorted(signals, key=get_executed_at_key, reverse=True)
            return signals_sorted[0]

        return None

    async def _recover_position(
        self, position: Dict[str, Any], signal: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Recover a position into the signal lifecycle manager.

        Returns:
            Dict with recovery details
        """
        recovery_info = {
            "symbol": position["symbol"],
            "side": signal["side"],
            "signal_id": signal["signal_id"],
            "entry_price": position["entry_price"],
            "size": position["size"],
            "unrealized_pnl": position.get("unrealized_pnl", 0),
            "recovered": True,
        }

        # If we have a signal lifecycle manager, restore the TrackedSignal
        if self.signal_lifecycle:
            try:
                restored = await self._restore_tracked_signal(position, signal)
                recovery_info["tracking_restored"] = restored
            except Exception as e:
                logger.error(f"Failed to restore tracking for {signal['signal_id']}: {e}")
                recovery_info["tracking_restored"] = False
                recovery_info["error"] = str(e)

        logger.info(
            f"✅ RECOVERED: {signal['side']} {position['symbol']} | "
            f"Entry: {position['entry_price']:.4f} | "
            f"Size: {position['size']} | "
            f"Signal: {signal['signal_id'][:8]}..."
        )

        return recovery_info

    async def _recover_orphan_position(self, position: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recover an orphan position (exists on exchange but not in database).

        Creates a synthetic signal to track the position in signal_lifecycle.
        This prevents the bot from opening duplicate positions for symbols
        that already have open positions from previous sessions.

        Args:
            position: Position data from exchange

        Returns:
            Dict with recovery details
        """
        symbol = position["symbol"]
        pos_side = position["side"].upper()
        signal_side = "BUY" if pos_side in ("BUY", "LONG") else "SELL"

        # Generate synthetic signal ID
        import uuid

        synthetic_signal_id = f"ORPHAN_{symbol}_{signal_side}_{uuid.uuid4().hex[:8]}"

        # Create synthetic signal data from exchange position
        synthetic_signal = {
            "signal_id": synthetic_signal_id,
            "symbol": symbol,
            "side": signal_side,
            "entry_price": position["entry_price"],
            "stop_loss": 0,  # Unknown - will need to set manually or use default
            "take_profit": [],  # Unknown
            "order_id": None,
            "strategy_name": "RECOVERED_ORPHAN",
            "executed_at": utcnow(),
            "raw_signal": None,
        }

        recovery_info = {
            "symbol": symbol,
            "side": signal_side,
            "signal_id": synthetic_signal_id,
            "entry_price": position["entry_price"],
            "size": position["size"],
            "unrealized_pnl": position.get("unrealized_pnl", 0),
            "recovered": False,  # Will be set to True if tracking restored
            "is_orphan": True,
        }

        # If we have a signal lifecycle manager, restore the TrackedSignal
        if self.signal_lifecycle:
            try:
                restored = await self._restore_tracked_signal(position, synthetic_signal)
                recovery_info["tracking_restored"] = restored
                recovery_info["recovered"] = restored

                if restored:
                    logger.info(
                        f"✅ ORPHAN RECOVERED: {signal_side} {symbol} | "
                        f"Entry: {position['entry_price']:.4f} | "
                        f"Size: {position['size']} | "
                        f"Synthetic ID: {synthetic_signal_id[:16]}..."
                    )
                    logger.warning(
                        f"⚠️  Orphan position {symbol} has no SL/TP - "
                        f"please set manually or close position"
                    )
            except Exception as e:
                logger.error(f"Failed to restore orphan tracking for {symbol}: {e}")
                recovery_info["tracking_restored"] = False
                recovery_info["error"] = str(e)
        else:
            # No lifecycle manager - just log the orphan
            logger.warning(
                f"⚠️  Orphan position detected: {signal_side} {symbol} @ {position['entry_price']:.4f} "
                f"(no lifecycle manager to restore tracking)"
            )

        return recovery_info

    async def _restore_tracked_signal(
        self, position: Dict[str, Any], signal: Dict[str, Any]
    ) -> bool:
        """
        Restore a TrackedSignal in the signal lifecycle manager.

        Returns:
            True if successfully restored
        """
        if not self.signal_lifecycle:
            return False

        # Import here to avoid circular imports
        from src.execution.signal_lifecycle_manager import TrackedSignal

        # Get take_profit as list
        take_profit = signal.get("take_profit", [])
        if isinstance(take_profit, (int, float)):
            take_profit = [take_profit]
        elif take_profit is None:
            take_profit = []

        # Convert executed_at from timestamp (ms) to datetime if needed
        executed_at_raw = signal.get("executed_at")
        if isinstance(executed_at_raw, (int, float)):
            # Convert milliseconds timestamp to datetime
            executed_at_dt = datetime.utcfromtimestamp(executed_at_raw / 1000)
        elif isinstance(executed_at_raw, datetime):
            executed_at_dt = executed_at_raw
        else:
            executed_at_dt = utcnow()

        # Create TrackedSignal
        tracked = TrackedSignal(
            signal=signal.get("raw_signal"),
            signal_id=signal["signal_id"],
            symbol=position["symbol"],
            side=signal["side"],
            entry_price=signal.get("entry_price", position["entry_price"]),
            stop_loss=signal.get("stop_loss", 0),
            take_profit=take_profit,
            created_at=executed_at_dt,
            status="EXECUTED",
            strategy_name=signal.get("strategy_name", ""),
            executed_at=executed_at_dt,
            executed_price=position["entry_price"],
            exchange_order_id=signal.get("order_id"),
        )

        # Set position ticket for MT5 multi-position support
        tracked.position_ticket = position.get("ticket")

        # Calculate position size from exchange data
        tracked.position_size_usdt = position["size"] * position["entry_price"]
        tracked.initial_quantity = position["size"]  # Volume in lots

        # Add to tracked signals
        self.signal_lifecycle.tracked_signals[signal["signal_id"]] = tracked

        # Register with the break-even manager — with the same parameters as at entry
        # (signal_lifecycle_manager, step 8): use_breakeven, threshold and lock from the
        # signal metadata, TP for the level, side LONG/SHORT. Recovery used to register
        # BUY/SELL without metadata and enable breakeven at once on the global config
        # threshold, and the "profit percentage" was computed as profit / (lots * price) —
        # a 100x overstatement on XAUUSD: a short at +0.33% got a stop $0.7 from the price.
        # There is no immediate activation any more: the first price update checks the
        # threshold the normal way (with the "stop not beyond price" guard).
        be_meta = (signal.get("raw_signal") or {}).get("metadata") or {}
        if self.signal_lifecycle.break_even_manager and be_meta.get("use_breakeven", True):
            self.signal_lifecycle.break_even_manager.register_position(
                position_id=signal["signal_id"],
                symbol=position["symbol"],
                side="LONG" if signal["side"] == "BUY" else "SHORT",
                entry_price=position["entry_price"],
                quantity=position["size"],
                signal_id=signal["signal_id"],
                stop_loss=signal.get("stop_loss", 0),
                take_profit=take_profit,
                activation_percent=be_meta.get("breakeven_activation_percent"),
                lock_profit_ratio=be_meta.get("breakeven_lock_profit_ratio"),
            )

        # Sync exchange orders (create SL/TP if missing)
        await self._sync_exchange_orders(tracked, position, signal)

        return True

    async def _sync_exchange_orders(self, tracked, position, signal) -> None:
        """Check and create missing SL/TP orders on exchange."""
        if not self.connector:
            logger.warning("No connector for order sync")
            return

        try:
            symbol = position["symbol"]
            side = signal["side"]
            quantity = position["size"]

            # Get CONDITIONAL orders (SL/TP are stop orders, not regular orders)
            if hasattr(self.connector, "get_conditional_orders"):
                existing_orders = await self.connector.get_conditional_orders(symbol)
            else:
                existing_orders = await self.connector.get_open_orders(symbol)

            position_idx = 1 if side == "BUY" else 2
            close_side = "Sell" if side == "BUY" else "Buy"

            # Track existing orders for this specific position
            sl_order_id = None
            tp_order_ids = []

            # Get entry price to distinguish SL from TP
            entry_price = position.get("entry_price", 0)
            is_long = side == "BUY"

            for order in existing_orders:
                order_side = order.get("side", "").capitalize()
                order_position_idx = order.get("position_idx", 0)
                order_symbol = order.get("symbol", "")
                stop_price = order.get("stop_price", 0)

                # Skip orders for different symbol
                if order_symbol != symbol:
                    continue

                # Skip orders for different side (close_side is the side to close the position)
                if order_side != close_side:
                    continue

                # In hedge mode, check position_idx matches (1=Long, 2=Short)
                if order_position_idx != 0 and order_position_idx != position_idx:
                    continue

                # Determine if SL or TP based on stop_price vs entry_price
                # For LONG: SL is below entry, TP is above entry
                # For SHORT: SL is above entry, TP is below entry
                if stop_price > 0 and entry_price > 0:
                    if is_long:
                        is_sl_order = stop_price < entry_price
                        is_tp_order = stop_price > entry_price
                    else:
                        is_sl_order = stop_price > entry_price
                        is_tp_order = stop_price < entry_price

                    if is_sl_order and not sl_order_id:
                        sl_order_id = order.get("order_id")
                    elif is_tp_order:
                        tp_order_ids.append(order.get("order_id"))

            # Update tracked signal with found order IDs
            if sl_order_id:
                tracked.sl_order_id = sl_order_id
            if tp_order_ids:
                tracked.tp_order_ids = tp_order_ids

            orders_created = []

            # MT5: SL/TP live on the position itself, not as orders. If the broker
            # already holds them, leave them: there may be a breakeven or trailing stop
            # there, while the DB has the original signal stop (recovery once rolled a BE back).
            if tracked.position_ticket:
                if (position.get("sl") or 0) > 0:
                    sl_order_id = "TRADING_STOP"
                    tracked.sl_order_id = sl_order_id
                if (position.get("tp") or 0) > 0:
                    tracked.tp_order_ids = ["TRADING_STOP_TP"]

            # Create SL if missing
            if not sl_order_id and signal.get("stop_loss", 0) > 0:
                sl_price = signal["stop_loss"]
                sl_result = await self._create_sl_order(
                    symbol,
                    close_side.upper(),
                    quantity,
                    sl_price,
                    position_idx,
                    position_ticket=tracked.position_ticket,
                )
                if sl_result:
                    tracked.sl_order_id = sl_result
                    orders_created.append(f"SL @ {sl_price:.4f}")

            # Create TPs if missing
            take_profit = signal.get("take_profit", [])
            if isinstance(take_profit, (int, float)):
                take_profit = [take_profit]

            # Only create TPs that don't exist yet
            existing_tp_count = len(tp_order_ids)
            if tracked.position_ticket:
                if not take_profit or (position.get("tp") or 0) > 0:
                    pass  # TP is already on the position, or the signal has no TP
                else:
                    # MT5: a limit order opens a NEW position (there is no reduce_only) — TP only
                    # via position modification, as in signal_lifecycle_manager. Otherwise at the
                    # TP touch the position closes on its TP and the limit opens an opposite one with no SL/TP.
                    tp_result = await self.connector.set_trading_stop(
                        symbol=symbol,
                        position_idx=position_idx,
                        take_profit=take_profit[0],
                        position_ticket=tracked.position_ticket,
                    )
                    if tp_result and tp_result.get("success"):
                        tracked.tp_order_ids = ["TRADING_STOP_TP"]
                        orders_created.append(f"TP1 @ {take_profit[0]:.4f} (position modify)")
                    else:
                        error = tp_result.get("error", "Unknown") if tp_result else "No response"
                        logger.error(f"Failed to set TP1 for {symbol}: {error}")
            elif take_profit and existing_tp_count < len(take_profit[:2]):
                partial_percents = [40.0, 20.0, 40.0]
                if self.signal_lifecycle and hasattr(self.signal_lifecycle, "config"):
                    partial_percents = self.signal_lifecycle.config.partial_close_percents

                if not tracked.tp_order_ids:
                    tracked.tp_order_ids = list(tp_order_ids)  # Copy existing

                # Start from where we left off
                for i in range(existing_tp_count, min(2, len(take_profit))):
                    tp_price = take_profit[i]
                    if i >= len(partial_percents):
                        break
                    tp_qty = quantity * (partial_percents[i] / 100)
                    tp_qty = self._round_quantity(symbol, tp_qty, tp_price)
                    tp_result = await self._create_tp_order(
                        symbol, close_side.upper(), tp_qty, tp_price, position_idx
                    )
                    if tp_result:
                        tracked.tp_order_ids.append(tp_result)
                        orders_created.append(f"TP{i+1} @ {tp_price:.4f}")
                    else:
                        tracked.tp_order_ids.append(None)

            if orders_created:
                logger.info(f"ORDERS SYNCED for {side} {symbol}: " + ", ".join(orders_created))
            else:
                logger.info(
                    f"Orders already exist for {symbol}: SL={sl_order_id is not None}, TP count={len(tp_order_ids)}"
                )

        except Exception as e:
            logger.error(f"Error syncing orders: {e}")

    def _round_quantity(self, symbol: str, raw_quantity: float, price: float) -> float:
        """Round quantity to valid step size for the symbol."""
        from src.core.trading_pairs import trading_pairs

        return trading_pairs.round_quantity(symbol, raw_quantity, price)

    async def _create_sl_order(
        self, symbol, side, quantity, stop_price, position_idx, position_ticket=None
    ):
        """Create stop loss via trading-stop API (market SL on position)."""
        try:
            kwargs = {"symbol": symbol, "position_idx": position_idx, "stop_loss": stop_price}
            if position_ticket:  # MT5: without a ticket it may hit another position on the symbol
                kwargs["position_ticket"] = position_ticket
            result = await self.connector.set_trading_stop(**kwargs)
            if result and result.get("success"):
                logger.info(f"SL SET: {symbol} @ {stop_price:.4f} (trading-stop)")
                return "TRADING_STOP"
            else:
                error = result.get("error", "Unknown") if result else "No response"
                logger.error(f"Failed to create SL for {symbol}: {error}")
            return None
        except Exception as e:
            logger.error(f"Error creating SL: {e}")
            return None

    async def _create_tp_order(self, symbol, side, quantity, stop_price, position_idx):
        """Create take profit as regular limit order (maker fee)."""
        try:
            result = await self.connector.place_order(
                {
                    "symbol": symbol,
                    "side": side,
                    "type": "LIMIT",
                    "quantity": quantity,
                    "price": stop_price,
                    "reduce_only": True,
                    "position_idx": position_idx,
                    "time_in_force": "GTC",
                }
            )
            if result and result.get("success"):
                logger.info(f"TP ORDER CREATED: {symbol} @ {stop_price:.4f} (limit, maker)")
                return result.get("order_id")
            else:
                error = result.get("error", "Unknown") if result else "No response"
                logger.error(f"Failed to create TP for {symbol}: {error}")
            return None
        except Exception as e:
            logger.error(f"Error creating TP: {e}")
            return None

    async def _sync_be_stop_to_exchange(self, tracked, be_stop_price: float) -> bool:
        """
        Sync Break-Even stop loss to exchange during recovery.

        This updates the existing SL order to the new BE stop price.
        Critical for positions that were in profit when recovered.

        Args:
            tracked: TrackedSignal with position info
            be_stop_price: The new stop price for BE

        Returns:
            True if sync was successful
        """
        try:
            if not self.connector or not tracked.sl_order_id:
                logger.warning(
                    f"Cannot sync BE for {tracked.symbol}: "
                    f"connector={bool(self.connector)}, sl_order_id={tracked.sl_order_id}"
                )
                return False

            symbol = tracked.symbol
            side = tracked.side

            # Update SL to BE price via trading-stop API
            position_idx = 1 if side == "BUY" else 2

            max_retries = 3
            sl_result = None

            for attempt in range(max_retries):
                sl_result = await self.connector.set_trading_stop(
                    symbol=symbol,
                    position_idx=position_idx,
                    stop_loss=be_stop_price,
                )

                if sl_result and sl_result.get("success"):
                    tracked.sl_order_id = "TRADING_STOP"
                    logger.info(
                        f"🛡️ BE SL SYNCED: {symbol} | " f"Stop @ {be_stop_price:.4f} (trading-stop)"
                    )
                    return True

                error = sl_result.get("error", "Unknown") if sl_result else "No response"
                if sl_result and sl_result.get("error_code") not in (None, "NO_RESPONSE", 10002):
                    break

                if attempt < max_retries - 1:
                    delay = 1.0 * (attempt + 1)
                    logger.warning(
                        f"⚠️ BE SL retry {attempt + 1}/{max_retries} for {symbol}: {error}"
                    )
                    await asyncio.sleep(delay)

            error = sl_result.get("error", "Unknown") if sl_result else "No response"
            logger.error(f"Failed to create BE SL for {symbol}: {error}")
            return False

        except Exception as e:
            logger.error(f"Error syncing BE stop to exchange: {e}")
            return False

    async def _mark_signal_closed(self, signal: Dict[str, Any], reason: str) -> None:
        """Mark a stale signal as closed in the database."""
        try:
            if self.database:
                self.database.update_signal_outcome(
                    signal_id=signal["signal_id"],
                    exit_price=signal.get("entry_price", 0),  # Unknown exit price
                    exit_reason=reason,
                    pnl=0,
                    pnl_percent=0,
                )
                logger.info(
                    f"📝 Marked as closed: {signal['signal_id'][:8]}... " f"(reason: {reason})"
                )
        except Exception as e:
            logger.error(f"Failed to mark signal as closed: {e}")

    def _log_summary(self, result: RecoveryResult) -> None:
        """Log recovery summary."""
        logger.info("=" * 50)
        logger.info("📊 RECOVERY SUMMARY")
        logger.info("=" * 50)
        logger.info(f"  ✅ Recovered: {len(result.recovered)}")
        logger.info(f"  ⚠️  Orphans:   {len(result.orphans)}")
        logger.info(f"  📝 Stale:     {len(result.stale)}")
        logger.info(f"  ❌ Errors:    {len(result.errors)}")

        if result.recovered:
            logger.info("\n  Recovered positions:")
            for r in result.recovered:
                logger.info(f"    - {r['side']} {r['symbol']} @ {r['entry_price']:.4f}")

        if result.orphans:
            logger.info("\n  Orphan positions (not in DB):")
            for o in result.orphans:
                pos = o["position"]
                logger.info(f"    - {pos['side']} {pos['symbol']} | {o['reason']}")

        if result.stale:
            logger.info("\n  Stale signals (closed externally):")
            for s in result.stale:
                sig = s["signal"]
                logger.info(f"    - {sig['side']} {sig['symbol']} | {s['reason']}")

        logger.info("=" * 50)

    async def clean_orphan_orders(self) -> Dict[str, Any]:
        """
        Find and cancel orphan orders - orders that exist on exchange
        but have no associated open position.

        This can happen when:
        - Position was closed manually but SL/TP orders remain
        - Bot crashed before cancelling orders
        - Orders remain from previous sessions

        Returns:
            Dict with cleanup results
        """
        result = {"checked": 0, "cancelled": 0, "failed": 0, "details": []}

        if not self.connector:
            logger.warning("No connector available for orphan order cleanup")
            return result

        try:
            # 1. Get all open positions
            positions = await self._get_exchange_positions()
            position_keys = set()

            for pos in positions:
                symbol = pos["symbol"]
                side = pos["side"].upper()
                close_side = "Sell" if side in ("BUY", "LONG") else "Buy"
                position_keys.add((symbol, close_side))

            # 2. Get all conditional orders (SL/TP)
            if hasattr(self.connector, "get_conditional_orders"):
                all_orders = await self.connector.get_conditional_orders()
            else:
                all_orders = await self.connector.get_open_orders()

            if not all_orders:
                logger.debug(
                    f"Orphan orders cleanup: {len(positions)} positions, no conditional orders"
                )
                return result

            logger.info(f"📋 Found {len(all_orders)} conditional orders")

            # 3. Find orphan orders (orders without matching position)
            orphan_orders = []

            for order in all_orders:
                order_symbol = order.get("symbol", "")
                order_side = order.get("side", "").capitalize()
                order_id = order.get("order_id", "")
                stop_price = order.get("stop_price", 0)

                result["checked"] += 1

                # Check if there's a position that would have this order
                if (order_symbol, order_side) not in position_keys:
                    orphan_orders.append(
                        {
                            "symbol": order_symbol,
                            "side": order_side,
                            "order_id": order_id,
                            "stop_price": stop_price,
                            "order_type": order.get("order_type", "UNKNOWN"),
                        }
                    )

            if not orphan_orders:
                logger.info("  ✓ No orphan orders found")
                return result

            logger.info(f"⚠️  Found {len(orphan_orders)} orphan orders to cancel")

            # 4. Cancel orphan orders
            for orphan in orphan_orders:
                try:
                    cancel_result = await self.connector.cancel_order(
                        {"symbol": orphan["symbol"], "order_id": orphan["order_id"]}
                    )

                    if cancel_result.get("success"):
                        result["cancelled"] += 1
                        logger.info(
                            f"  ✓ Cancelled: {orphan['side']} {orphan['symbol']} "
                            f"@ {orphan['stop_price']:.4f} ({orphan['order_type']})"
                        )
                        result["details"].append({**orphan, "status": "cancelled"})
                    else:
                        error = cancel_result.get("error", "Unknown")
                        # Order already gone is not a failure
                        if "not exist" in str(error).lower() or "not found" in str(error).lower():
                            logger.debug(f"  Order already gone: {orphan['order_id'][:8]}...")
                        else:
                            result["failed"] += 1
                            logger.warning(f"  ✗ Failed to cancel: {orphan['symbol']} - {error}")
                            result["details"].append({**orphan, "status": "failed", "error": error})

                except Exception as e:
                    result["failed"] += 1
                    logger.error(f"  Error cancelling order: {e}")

            # 5. Summary
            logger.info("=" * 50)
            logger.info(
                f"🧹 CLEANUP COMPLETE: {result['cancelled']} cancelled, {result['failed']} failed"
            )
            logger.info("=" * 50)

        except Exception as e:
            logger.error(f"Orphan order cleanup failed: {e}")
            result["error"] = str(e)

        return result

    async def clean_duplicate_orders(self) -> Dict[str, Any]:
        """
        Find and cancel duplicate SL/TP orders for the same position.

        For each position, keeps only:
        - 1 SL order (the one closest to current price)
        - Up to 2 TP orders (the ones closest to current price)

        Cancels all other duplicates.

        Returns:
            Dict with cleanup results
        """
        result = {"checked": 0, "cancelled": 0, "failed": 0, "details": []}

        if not self.connector:
            logger.warning("No connector available for duplicate order cleanup")
            return result

        try:
            # 1. Get all open positions
            positions = await self._get_exchange_positions()
            if not positions:
                logger.debug("Duplicate orders cleanup: no open positions")
                return result

            # 2. Get all conditional orders
            if hasattr(self.connector, "get_conditional_orders"):
                all_orders = await self.connector.get_conditional_orders()
            else:
                all_orders = await self.connector.get_open_orders()

            if not all_orders:
                logger.debug("Duplicate orders cleanup: no orders to check")
                return result

            logger.info(f"📊 Checking {len(all_orders)} orders for {len(positions)} positions")

            # 3. Group orders by symbol and position side
            from collections import defaultdict

            orders_by_position = defaultdict(lambda: {"sl": [], "tp": []})

            for order in all_orders:
                order_symbol = order.get("symbol", "")
                order_side = order.get("side", "").upper()
                stop_price = order.get("stop_price", 0)
                order_id = order.get("order_id", "")
                order_type = order.get("order_type", "").upper()
                position_idx = order.get("position_idx", 0)

                # Determine position side from order side
                # Buy orders close Short positions, Sell orders close Long positions
                pos_side = "SHORT" if order_side == "BUY" else "LONG"
                key = (order_symbol, pos_side, position_idx)

                order_info = {
                    "order_id": order_id,
                    "stop_price": stop_price,
                    "order_type": order_type,
                    "symbol": order_symbol,
                    "side": order_side,
                }

                # Classify as SL or TP based on order type
                if "STOP" in order_type and "PROFIT" not in order_type:
                    orders_by_position[key]["sl"].append(order_info)
                elif "PROFIT" in order_type or "TAKE" in order_type:
                    orders_by_position[key]["tp"].append(order_info)

                result["checked"] += 1

            # 4. For each position, find duplicates
            orders_to_cancel = []

            for key, orders in orders_by_position.items():
                symbol, pos_side, pos_idx = key

                # Get current price for sorting
                current_price = 0
                for pos in positions:
                    if pos["symbol"] == symbol:
                        current_price = pos.get("entry_price", 0)
                        break

                # Sort SL orders by distance from current price, keep closest
                sl_orders = orders["sl"]
                if len(sl_orders) > 1:
                    sl_orders.sort(key=lambda x: abs(x["stop_price"] - current_price))
                    # Keep only the first (closest), cancel rest
                    for sl in sl_orders[1:]:
                        orders_to_cancel.append(sl)
                        logger.debug(f"  Duplicate SL: {symbol} @ {sl['stop_price']:.4f}")

                # Sort TP orders by distance from current price, keep 2 closest
                tp_orders = orders["tp"]
                if len(tp_orders) > 2:
                    tp_orders.sort(key=lambda x: abs(x["stop_price"] - current_price))
                    # Keep only first 2, cancel rest
                    for tp in tp_orders[2:]:
                        orders_to_cancel.append(tp)
                        logger.debug(f"  Duplicate TP: {symbol} @ {tp['stop_price']:.4f}")

            if not orders_to_cancel:
                logger.info("  ✓ No duplicate orders found")
                return result

            logger.info(f"⚠️  Found {len(orders_to_cancel)} duplicate orders to cancel")

            # 5. Cancel duplicates
            for order in orders_to_cancel:
                try:
                    cancel_result = await self.connector.cancel_order(
                        {"symbol": order["symbol"], "order_id": order["order_id"]}
                    )

                    if cancel_result.get("success"):
                        result["cancelled"] += 1
                        logger.info(
                            f"  ✓ Cancelled duplicate: {order['side']} {order['symbol']} "
                            f"@ {order['stop_price']:.4f}"
                        )
                    else:
                        error = cancel_result.get("error", "Unknown")
                        if "not exist" not in str(error).lower():
                            result["failed"] += 1
                            logger.warning(f"  ✗ Failed: {order['symbol']} - {error}")

                except Exception as e:
                    result["failed"] += 1
                    logger.error(f"  Error cancelling duplicate: {e}")

            logger.info("=" * 50)
            logger.info(
                f"🔄 DUPLICATES CLEANUP: {result['cancelled']} cancelled, {result['failed']} failed"
            )
            logger.info("=" * 50)

        except Exception as e:
            logger.error(f"Duplicate order cleanup failed: {e}")
            result["error"] = str(e)

        return result
