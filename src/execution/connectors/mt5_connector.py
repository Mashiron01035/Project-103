"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : mt5_connector
    Project     : Project-103
    Description : MetaTrader 5 connector. This module implements the MT5 connector for forex and CFD trading.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.execution.connectors.base_connector import BaseConnector

# Execution telemetry (requested vs filled JSONL) is not part of the public build.
# Guarded so the connector runs without it; records are then simply skipped.
try:
    from src.execution.execution_telemetry import adverse_slippage, get_execution_telemetry
except ImportError:
    adverse_slippage = get_execution_telemetry = None
from src.utils.mt5_bridge import MT5_AVAILABLE, mt5

logger = logging.getLogger(__name__)


class MT5Connector(BaseConnector):
    """MetaTrader 5 connector implementation."""

    def __init__(
        self,
        api_key: str = "",  # Not used for MT5
        api_secret: str = "",  # Not used for MT5
        testnet: bool = False,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None,
    ):
        """
        Initialize MT5 connector.

        Args:
            api_key: Not used for MT5
            api_secret: Not used for MT5
            testnet: Whether to use demo account
            login: MT5 account number
            password: MT5 account password
            server: MT5 server name
            path: Path to MT5 terminal
        """
        super().__init__(api_key, api_secret, testnet)

        self.login = login
        self.password = password
        self.server = server
        self.path = path

        # Check if MT5 is available
        if not MT5_AVAILABLE:
            logger.error("MT5 not available. Install MetaTrader5 (Windows) or mt5linux (Linux).")
            raise ImportError(
                "MT5 not available. Install MetaTrader5 (Windows) or mt5linux (Linux)"
            )

        # MT5 specific settings
        self.magic_number = 123456  # Magic number for orders
        self.deviation = 20  # Maximum price deviation

        # Reconnection settings
        self._consecutive_errors = 0
        self._max_errors_before_reconnect = 3
        self._reconnecting = False

        # Filling mode cache per symbol
        self._filling_mode_cache: Dict[str, int] = {}

        # Execution telemetry (requested vs filled, latency, rejects).
        # Never on the critical path: every call is self-guarding.
        self._telemetry = (
            get_execution_telemetry(account=login) if get_execution_telemetry else None
        )

        logger.info(f"MT5Connector initialized (demo: {testnet})")

    async def connect(self) -> bool:
        """
        Establish connection to MT5.

        Returns:
            bool: Connection success status
        """
        try:
            # Initialize MT5
            if self.path:
                if not await self._run_mt5(mt5.initialize, self.path):
                    logger.error(f"MT5 initialization failed: {mt5.last_error()}")
                    return False
            else:
                if not await self._run_mt5(mt5.initialize):
                    logger.error(f"MT5 initialization failed: {mt5.last_error()}")
                    return False

            # Login if credentials provided
            if self.login and self.password and self.server:
                authorized = await self._run_mt5(
                    mt5.login, login=self.login, password=self.password, server=self.server
                )

                if not authorized:
                    logger.error(f"MT5 login failed: {mt5.last_error()}")
                    await self._run_mt5(mt5.shutdown)
                    return False

            # Get account info to verify connection
            account_info = await self._run_mt5(mt5.account_info)
            if account_info:
                self.is_connected = True
                logger.info(f"Connected to MT5 successfully. Account: {account_info.login}")
                return True
            else:
                logger.error("Failed to get MT5 account info")
                await self._run_mt5(mt5.shutdown)
                return False

        except Exception as e:
            logger.error(f"Error connecting to MT5: {e}")
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from MT5.

        Returns:
            bool: Disconnection success status
        """
        try:
            await self._run_mt5(mt5.shutdown)
            self.is_connected = False
            logger.info("Disconnected from MT5")
            return True

        except Exception as e:
            logger.error(f"Error disconnecting from MT5: {e}")
            return False

    async def _ensure_connected(self) -> bool:
        """
        Check MT5 connection and reconnect if needed.
        Called before trading operations when consecutive errors are detected.

        Returns:
            bool: True if connected
        """
        if self._reconnecting:
            return False

        try:
            info = await self._run_mt5(mt5.terminal_info)
            if info is not None:
                self._consecutive_errors = 0
                return True
        except Exception:
            pass

        # Connection lost - attempt reconnect
        self._reconnecting = True
        logger.warning("MT5 connector: connection lost, attempting reconnect...")

        try:
            try:
                await self._run_mt5(mt5.shutdown)
            except Exception:
                pass

            await asyncio.sleep(2)

            if self.path:
                result = await self._run_mt5(mt5.initialize, self.path)
            else:
                result = await self._run_mt5(mt5.initialize)

            if not result:
                logger.error(f"MT5 reconnect failed: {mt5.last_error()}")
                self._reconnecting = False
                return False

            if self.login and self.password and self.server:
                if not await self._run_mt5(
                    mt5.login, login=self.login, password=self.password, server=self.server
                ):
                    logger.error(f"MT5 re-login failed: {mt5.last_error()}")
                    self._reconnecting = False
                    return False

            account_info = await self._run_mt5(mt5.account_info)
            if account_info:
                self.is_connected = True
                self._consecutive_errors = 0
                logger.info(
                    f"MT5 connector reconnected successfully. Account: {account_info.login}"
                )
                self._reconnecting = False
                return True
            else:
                logger.error("MT5 reconnect: failed to get account info")
                self._reconnecting = False
                return False

        except Exception as e:
            logger.error(f"MT5 reconnect error: {e}")
            self._reconnecting = False
            return False

    async def _get_filling_mode(self, symbol: str) -> int:
        """
        Determine the correct filling mode for a symbol based on broker support.

        Args:
            symbol: Trading symbol

        Returns:
            int: MT5 filling mode constant
        """
        if symbol in self._filling_mode_cache:
            return self._filling_mode_cache[symbol]

        try:
            info = await self._run_mt5(mt5.symbol_info, symbol)
            if info is None:
                logger.warning(f"Cannot get symbol info for {symbol}, defaulting to IOC")
                return mt5.ORDER_FILLING_IOC

            filling = info.filling_mode

            # Check bitmask: bit 0 = FOK, bit 1 = IOC, bit 2 = RETURN
            if filling & 2:  # IOC supported
                mode = mt5.ORDER_FILLING_IOC
                mode_name = "IOC"
            elif filling & 1:  # FOK supported
                mode = mt5.ORDER_FILLING_FOK
                mode_name = "FOK"
            else:  # RETURN (or unknown)
                mode = mt5.ORDER_FILLING_RETURN
                mode_name = "RETURN"

            self._filling_mode_cache[symbol] = mode
            logger.info(f"Filling mode for {symbol}: {mode_name} " f"(bitmask={filling})")
            return mode

        except Exception as e:
            logger.warning(f"Error detecting filling mode for {symbol}: {e}")
            return mt5.ORDER_FILLING_IOC

    @staticmethod
    async def _run_mt5(func, *args, **kwargs):
        """Run a synchronous MT5 function in a thread to avoid blocking the event loop."""
        return await asyncio.to_thread(func, *args, **kwargs)

    def _record_order_telemetry(
        self,
        event: str,
        outcome: str,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        order_type: Optional[str] = None,
        requested_price: Optional[float] = None,
        requested_volume: Optional[float] = None,
        req_tick: Any = None,
        result: Any = None,
        latency_ms: Optional[float] = None,
        strategy: Optional[str] = None,
        signal_id: Optional[str] = None,
        position_ticket: Optional[Any] = None,
        exit_reason: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Emit one execution telemetry record.

        Reads only values already fetched by the calling order path — it issues
        no MT5 calls of its own, so it adds nothing to order latency.
        """
        if self._telemetry is None:
            return
        try:
            filled_price = getattr(result, "price", None) or None
            req_bid = getattr(req_tick, "bid", None)
            req_ask = getattr(req_tick, "ask", None)
            res_bid = getattr(result, "bid", None) or None
            res_ask = getattr(result, "ask", None) or None
            retcode = getattr(result, "retcode", None)

            self._telemetry.record(
                event=event,
                outcome=outcome,
                symbol=symbol,
                side=side,
                order_type=order_type,
                strategy=strategy,
                signal_id=signal_id,
                position_ticket=position_ticket,
                order_id=str(getattr(result, "order", "")) or None,
                deal_id=str(getattr(result, "deal", "")) or None,
                requested_price=requested_price,
                filled_price=filled_price,
                slippage=(
                    filled_price - requested_price if filled_price and requested_price else None
                ),
                slippage_adverse=adverse_slippage(side, requested_price, filled_price),
                req_bid=req_bid,
                req_ask=req_ask,
                req_spread=(req_ask - req_bid) if req_bid and req_ask else None,
                res_bid=res_bid,
                res_ask=res_ask,
                res_spread=(res_ask - res_bid) if res_bid and res_ask else None,
                requested_volume=requested_volume,
                filled_volume=getattr(result, "volume", None),
                latency_ms=round(latency_ms, 2) if latency_ms is not None else None,
                retcode=retcode,
                retcode_msg=self._get_error_message(retcode) if retcode else None,
                broker_comment=getattr(result, "comment", None) or None,
                deviation=self.deviation,
                magic=self.magic_number,
                exit_reason=exit_reason,
                error=error,
            )
        except Exception as e:
            logger.debug(f"Telemetry record skipped: {e}")

    @staticmethod
    def _server_offset_sec() -> int:
        """Broker server time offset from UTC, in seconds.

        Same source as tick_data_loader and mt5_provider — it must be a single
        source, otherwise backtest and live drift apart in time.
        MT5 history queries run in server time: UTC+2 in winter, UTC+3 in summer.
        """
        try:
            from datetime import timezone as _tz

            from src.backtesting.data.tick_data_loader import get_mt5_offset_for_date

            return get_mt5_offset_for_date(datetime.now(_tz.utc))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Could not determine the server offset ({exc}), " f"assuming summer UTC+3"
            )
            return 3 * 3600

    @staticmethod
    def _to_mt5_time(utc_epoch: float, server_offset_sec: int) -> datetime:
        """A UTC moment -> a naive datetime that MT5 interprets correctly.

        TWO different shifts meet here and must not be confused:

          1. Deal times inside MT5 are stored in the broker SERVER clock
             (UTC+2 in winter, UTC+3 in summer).
          2. The MT5 Python API converts a naive datetime passed to history_*_get
             into a timestamp using the LOCAL MACHINE clock, including its
             daylight saving time.

        Verified empirically on a terminal: a deal at server time 15:32 was found
        by a window around 17:32 with the machine on UTC+2 — i.e. the argument
        must be the local representation of the server timestamp. The formula
        below does exactly that, so it does not depend on the machine time zone:
        a VPS on UTC and a workstation on UTC+2 give the same result.

        The old code passed datetime.now() without the first shift, and a
        ten-second window missed the deal by two to three hours.
        """
        return datetime.fromtimestamp(utc_epoch + server_offset_sec)

    async def _resolve_position_ticket(self, result, symbol: str) -> Optional[int]:
        """Ticket of the position opened by a just-executed deal.

        WHY THERE IS NO TIME WINDOW HERE.

        The deal used to be looked up via history_deals_get(date_from, date_to)
        with a window around datetime.now(). It NEVER worked: MT5 expects SERVER
        time in these arguments, while datetime.now() is local. The broker server
        runs at UTC+2 in winter and UTC+3 in summer, so the window
        [now-10s, now+1s] landed two to three hours before the deal and never
        contained it. In the live log the fallback path fired on every
        order — 9 out of 9.

        Looking up by ticket removes the question entirely: it depends neither on
        the time zone nor on how well the machine and server clocks agree.

        Lookup order:
          1. by deal ticket         — exact answer;
          2. by order ticket        — the deal may not be in history yet,
                                      while the order already is;
          3. newest position        — only if the first two return nothing;
                                      narrowed by magic and volume, because a
                                      grid strategy can open several positions
                                      on one symbol at once, and "simply the
                                      newest" may belong to another order.
        """
        deal_id = getattr(result, "deal", None)
        order_id = getattr(result, "order", None)

        for attempt in range(3):
            if deal_id:
                deals = await self._run_mt5(mt5.history_deals_get, ticket=deal_id)
                for d in deals or ():
                    pid = getattr(d, "position_id", 0)
                    if pid:
                        return int(pid)

            if order_id:
                orders = await self._run_mt5(mt5.history_orders_get, ticket=order_id)
                for o in orders or ():
                    pid = getattr(o, "position_id", 0)
                    if pid:
                        return int(pid)

            # MT5 can write to history with a delay of tens of milliseconds
            if attempt < 2:
                await asyncio.sleep(0.1)

        positions = await self._run_mt5(mt5.positions_get, symbol=symbol)
        if not positions:
            logger.warning(
                f"place_order({symbol}): deal {deal_id} not found by ticket "
                f"or by order, and there are no open positions — ticket unknown"
            )
            return None

        candidates = [p for p in positions if getattr(p, "magic", None) == self.magic_number]
        if not candidates:
            candidates = list(positions)

        filled = getattr(result, "volume", None)
        if filled:
            exact = [p for p in candidates if abs(getattr(p, "volume", 0.0) - filled) < 1e-9]
            if exact:
                candidates = exact

        newest = max(candidates, key=lambda p: p.time_msc)
        logger.warning(
            f"place_order({symbol}): deal {deal_id} not found in history, "
            f"taking the newest position {newest.ticket} "
            f"({len(candidates)} candidates out of {len(positions)})"
        )
        return int(newest.ticket)

    async def place_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place an order on MT5.

        Args:
            order_params: Order parameters

        Returns:
            Dict: Order placement result
        """
        # Telemetry attribution — supplied by callers, never forwarded to MT5.
        tele_strategy = order_params.get("strategy")
        tele_signal_id = order_params.get("signal_id")
        req_tick = None

        try:
            # Ensure connection before trading
            await self._ensure_connected()

            symbol = order_params["symbol"]

            # Get symbol info
            symbol_info = await self._run_mt5(mt5.symbol_info, symbol)
            if not symbol_info:
                return {
                    "success": False,
                    "error": f"Symbol {symbol} not found",
                }

            if not symbol_info.visible:
                # Enable symbol
                if not await self._run_mt5(mt5.symbol_select, symbol, True):
                    return {
                        "success": False,
                        "error": f"Failed to enable symbol {symbol}",
                    }

            # Prepare MT5 order request
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(order_params["quantity"]),
                "type": self._convert_order_type(order_params["type"], order_params["side"]),
                "deviation": self.deviation,
                "magic": self.magic_number,
                "comment": self._order_comment(order_params),
            }

            # Add price for limit/stop orders
            if order_params["type"] == "LIMIT":
                request["action"] = mt5.TRADE_ACTION_PENDING
                request["price"] = float(order_params["price"])
            elif order_params["type"] in ["STOP_MARKET", "STOP_LIMIT"]:
                request["action"] = mt5.TRADE_ACTION_PENDING
                request["price"] = float(
                    order_params.get("stop_price", order_params.get("price", 0))
                )
            else:
                # Market order - use current price
                tick = await self._run_mt5(mt5.symbol_info_tick, symbol)
                if not tick:
                    return {
                        "success": False,
                        "error": f"Cannot get tick for {symbol} (market closed?)",
                    }
                req_tick = tick
                if order_params["side"] == "BUY":
                    request["price"] = tick.ask
                else:
                    request["price"] = tick.bid

            # Add SL/TP if provided
            if "stop_loss" in order_params:
                request["sl"] = float(order_params["stop_loss"])

            if "take_profit" in order_params:
                request["tp"] = float(order_params["take_profit"])

            # Set order type and filling (auto-detect per symbol)
            request["type_filling"] = await self._get_filling_mode(symbol)
            request["type_time"] = self._convert_time_in_force(
                order_params.get("time_in_force", "GTC")
            )

            # Send order
            _t_send = time.perf_counter()
            result = await self._run_mt5(mt5.order_send, request)
            _latency_ms = (time.perf_counter() - _t_send) * 1000.0

            retcode_done_partial = getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)

            if result and result.retcode in (mt5.TRADE_RETCODE_DONE, retcode_done_partial):
                if result.retcode == retcode_done_partial:
                    logger.warning(
                        f"Partial fill on {symbol}: requested {request['volume']}, "
                        f"filled {result.volume}"
                    )

                # Find position ticket from deal
                position_ticket = None
                if result.deal:
                    try:
                        position_ticket = await self._resolve_position_ticket(result, symbol)
                    except Exception as e:
                        logger.debug(f"Could not resolve position ticket from deal: {e}")

                self._consecutive_errors = 0

                self._record_order_telemetry(
                    event="ENTRY",
                    outcome="PARTIAL" if result.retcode == retcode_done_partial else "FILLED",
                    symbol=symbol,
                    side=order_params.get("side"),
                    order_type=order_params.get("type"),
                    requested_price=request.get("price"),
                    requested_volume=request.get("volume"),
                    req_tick=req_tick,
                    result=result,
                    latency_ms=_latency_ms,
                    strategy=tele_strategy,
                    signal_id=tele_signal_id,
                    position_ticket=position_ticket,
                )

                return {
                    "success": True,
                    "order_id": str(result.order),
                    "deal_id": str(result.deal) if result.deal else None,
                    "position_ticket": position_ticket,
                    "volume": result.volume,
                    "price": result.price,
                    "bid": result.bid,
                    "ask": result.ask,
                    "comment": result.comment,
                    "request_id": result.request_id,
                    "partial_fill": result.retcode == retcode_done_partial,
                    "raw_response": result._asdict(),
                }
            else:
                error_msg = self._get_error_message(result.retcode if result else 0)

                self._record_order_telemetry(
                    event="ENTRY",
                    outcome="REJECTED",
                    symbol=symbol,
                    side=order_params.get("side"),
                    order_type=order_params.get("type"),
                    requested_price=request.get("price"),
                    requested_volume=request.get("volume"),
                    req_tick=req_tick,
                    result=result,
                    latency_ms=_latency_ms,
                    strategy=tele_strategy,
                    signal_id=tele_signal_id,
                    error=error_msg,
                )

                self._consecutive_errors += 1
                if self._consecutive_errors >= self._max_errors_before_reconnect:
                    logger.warning(
                        f"MT5 consecutive errors ({self._consecutive_errors}x), "
                        f"triggering reconnect..."
                    )
                    await self._ensure_connected()
                return {
                    "success": False,
                    "error": error_msg,
                    "error_code": result.retcode if result else 0,
                    "raw_response": result._asdict() if result else None,
                }

        except Exception as e:
            logger.error(f"Error placing MT5 order: {e}")
            self._record_order_telemetry(
                event="ENTRY",
                outcome="ERROR",
                symbol=order_params.get("symbol"),
                side=order_params.get("side"),
                order_type=order_params.get("type"),
                requested_volume=order_params.get("quantity"),
                req_tick=req_tick,
                strategy=tele_strategy,
                signal_id=tele_signal_id,
                error=str(e),
            )
            return {
                "success": False,
                "error": str(e),
                "error_code": "EXCEPTION",
            }

    async def cancel_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cancel an order on MT5.

        Args:
            order_params: Order cancellation parameters

        Returns:
            Dict: Cancellation result
        """
        try:
            order_id = int(order_params.get("order_id", 0))

            if not order_id:
                return {
                    "success": False,
                    "error": "Order ID required",
                }

            # Get order info
            orders = await self._run_mt5(mt5.orders_get, ticket=order_id)

            if not orders:
                return {
                    "success": False,
                    "error": f"Order {order_id} not found",
                }

            # Prepare cancellation request
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": order_id,
            }

            result = await self._run_mt5(mt5.order_send, request)

            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return {
                    "success": True,
                    "order_id": str(order_id),
                    "raw_response": result._asdict(),
                }
            else:
                error_msg = self._get_error_message(result.retcode if result else 0)
                return {
                    "success": False,
                    "error": error_msg,
                    "error_code": result.retcode if result else 0,
                }

        except Exception as e:
            logger.error(f"Error cancelling MT5 order: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_code": "EXCEPTION",
            }

    async def modify_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Modify an existing order on MT5.

        Args:
            order_params: Order modification parameters

        Returns:
            Dict: Modification result
        """
        try:
            order_id = int(order_params.get("order_id", 0))

            # Get current order
            orders = await self._run_mt5(mt5.orders_get, ticket=order_id)

            if not orders:
                # Check if it's a position
                positions = await self._run_mt5(mt5.positions_get, ticket=order_id)

                if positions:
                    return await self._modify_position(order_params, positions[0])
                else:
                    return {
                        "success": False,
                        "error": f"Order {order_id} not found",
                    }

            order = orders[0]

            # Prepare modification request
            request = {
                "action": mt5.TRADE_ACTION_MODIFY,
                "order": order_id,
                "symbol": order.symbol,
            }

            # Update fields
            if "price" in order_params:
                request["price"] = float(order_params["price"])
            else:
                request["price"] = order.price_open

            if "stop_loss" in order_params:
                request["sl"] = float(order_params["stop_loss"])
            else:
                request["sl"] = order.sl

            if "take_profit" in order_params:
                request["tp"] = float(order_params["take_profit"])
            else:
                request["tp"] = order.tp

            if "expiration" in order_params:
                request["expiration"] = order_params["expiration"]

            result = await self._run_mt5(mt5.order_send, request)

            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return {
                    "success": True,
                    "order_id": str(order_id),
                    "raw_response": result._asdict(),
                }
            else:
                error_msg = self._get_error_message(result.retcode if result else 0)
                return {
                    "success": False,
                    "error": error_msg,
                    "error_code": result.retcode if result else 0,
                }

        except Exception as e:
            logger.error(f"Error modifying MT5 order: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_code": "EXCEPTION",
            }

    async def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Get order details. Alias for get_order_status with swapped arg order."""
        return await self.get_order_status(order_id=order_id, symbol=symbol)

    async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Get order status from MT5.

        Args:
            order_id: Order ID
            symbol: Trading symbol

        Returns:
            Dict: Order status
        """
        try:
            if order_id.startswith("TRADING_STOP"):
                return {}

            ticket = int(order_id)

            # Check open orders
            orders = await self._run_mt5(mt5.orders_get, ticket=ticket)

            if orders:
                order = orders[0]
                return self._format_order(order)

            # Check history
            history_orders = await self._run_mt5(mt5.history_orders_get, ticket=ticket)

            if history_orders:
                order = history_orders[0]
                return self._format_order(order)

            return {}

        except Exception as e:
            logger.error(f"Error getting MT5 order status: {e}")
            return {}

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open orders from MT5.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Open orders
        """
        try:
            if symbol:
                orders = await self._run_mt5(mt5.orders_get, symbol=symbol)
            else:
                orders = await self._run_mt5(mt5.orders_get)

            if orders:
                return [self._format_order(order) for order in orders]

            return []

        except Exception as e:
            logger.error(f"Error getting MT5 open orders: {e}")
            return []

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get open positions from MT5.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Open positions
        """
        try:
            await self._ensure_connected()

            if symbol:
                positions = await self._run_mt5(mt5.positions_get, symbol=symbol)
            else:
                positions = await self._run_mt5(mt5.positions_get)

            if positions:
                formatted_positions = []

                for pos in positions:
                    side = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                    formatted_positions.append(
                        {
                            "ticket": pos.ticket,
                            "symbol": pos.symbol,
                            "type": side,
                            "side": side,  # Alias for position sync compatibility
                            "volume": pos.volume,
                            "size": pos.volume,  # Alias for position sync compatibility
                            "price_open": pos.price_open,
                            "entry_price": pos.price_open,  # Alias for position_recovery compatibility
                            "price_current": pos.price_current,
                            "swap": pos.swap,
                            "profit": pos.profit,
                            "unrealized_pnl": pos.profit,  # MT5 profit = unrealized PnL
                            "sl": pos.sl,
                            "tp": pos.tp,
                            "time": pos.time,
                            "time_msc": pos.time_msc,
                            "time_update": pos.time_update,
                            "time_update_msc": pos.time_update_msc,
                            "magic": pos.magic,
                            "comment": pos.comment,
                            "raw_response": pos._asdict(),
                        }
                    )

                return formatted_positions

            return []

        except Exception as e:
            logger.error(f"Error getting MT5 positions: {e}")
            return []

    async def get_account_info(self) -> Dict[str, Any]:
        """
        Get account information from MT5.

        Returns:
            Dict: Account information
        """
        try:
            account_info = await self._run_mt5(mt5.account_info)

            if account_info:
                return {
                    "login": account_info.login,
                    "server": account_info.server,
                    "currency": account_info.currency,
                    "leverage": account_info.leverage,
                    "balance": account_info.balance,
                    "equity": account_info.equity,
                    "profit": account_info.profit,
                    "margin": account_info.margin,
                    "margin_free": account_info.margin_free,
                    "margin_level": account_info.margin_level,
                    "margin_so_call": account_info.margin_so_call,
                    "margin_so_so": account_info.margin_so_so,
                    "trade_allowed": account_info.trade_allowed,
                    "trade_expert": account_info.trade_expert,
                    "limit_orders": account_info.limit_orders,
                    "margin_mode": self._get_margin_mode(account_info.margin_mode),
                    "raw_response": account_info._asdict(),
                }

            return {}

        except Exception as e:
            logger.error(f"Error getting MT5 account info: {e}")
            return {}

    async def get_balance(self) -> Dict[str, float]:
        """
        Get account balance from MT5.

        Returns:
            Dict: Asset balances
        """
        try:
            account_info = await self._run_mt5(mt5.account_info)

            if account_info:
                return {
                    account_info.currency: account_info.balance,
                    f"{account_info.currency}_free": account_info.margin_free,
                    f"{account_info.currency}_margin": account_info.margin,
                }

            return {}

        except Exception as e:
            logger.error(f"Error getting MT5 balance: {e}")
            return {}

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get ticker data from MT5.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Ticker data
        """
        try:
            # Ensure symbol is selected
            if not await self._run_mt5(mt5.symbol_select, symbol, True):
                logger.error(f"Failed to select symbol {symbol}")
                return {}

            tick = await self._run_mt5(mt5.symbol_info_tick, symbol)

            if tick:
                return {
                    "symbol": symbol,
                    "time": tick.time,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "last": tick.last,
                    "volume": tick.volume,
                    "time_msc": tick.time_msc,
                    "flags": tick.flags,
                    "volume_real": tick.volume_real,
                    "spread": tick.ask - tick.bid,
                    "raw_response": tick._asdict(),
                }

            return {}

        except Exception as e:
            logger.error(f"Error getting MT5 ticker: {e}")
            return {}

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get order book from MT5.

        Note: MT5 doesn't provide full order book, only best bid/ask.

        Args:
            symbol: Trading symbol
            limit: Order book depth (ignored for MT5)

        Returns:
            Dict: Order book data
        """
        try:
            tick = await self._run_mt5(mt5.symbol_info_tick, symbol)

            if tick:
                # MT5 only provides best bid/ask
                return {
                    "symbol": symbol,
                    "timestamp": tick.time,
                    "bids": [[tick.bid, tick.volume]],  # Only best bid
                    "asks": [[tick.ask, tick.volume]],  # Only best ask
                    "spread": tick.ask - tick.bid,
                }

            return {}

        except Exception as e:
            logger.error(f"Error getting MT5 orderbook: {e}")
            return {}

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get recent trades from MT5.

        Args:
            symbol: Trading symbol
            limit: Number of trades

        Returns:
            List[Dict]: Recent trades
        """
        try:
            # Get ticks (trades) from MT5
            ticks = await self._run_mt5(
                mt5.copy_ticks_from, symbol, datetime.now(), limit, mt5.COPY_TICKS_TRADE
            )

            if ticks is not None and len(ticks) > 0:
                formatted_trades = []

                for tick in ticks:
                    formatted_trades.append(
                        {
                            "time": tick["time"],
                            "time_msc": tick["time_msc"],
                            "price": tick["last"],
                            "volume": tick["volume"],
                            "flags": tick["flags"],
                        }
                    )

                return formatted_trades

            return []

        except Exception as e:
            logger.error(f"Error getting MT5 recent trades: {e}")
            return []

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[List]:
        """
        Get kline/candle data from MT5.

        Args:
            symbol: Trading symbol
            interval: Kline interval
            limit: Number of klines
            start_time: Start timestamp
            end_time: End timestamp

        Returns:
            List[List]: Kline data
        """
        try:
            # Convert interval to MT5 timeframe
            timeframe = self._convert_interval_to_mt5(interval)

            # Get rates
            if start_time and end_time:
                start_dt = datetime.fromtimestamp(start_time)
                end_dt = datetime.fromtimestamp(end_time)
                rates = await self._run_mt5(
                    mt5.copy_rates_range, symbol, timeframe, start_dt, end_dt
                )
            elif start_time:
                start_dt = datetime.fromtimestamp(start_time)
                rates = await self._run_mt5(mt5.copy_rates_from, symbol, timeframe, start_dt, limit)
            else:
                rates = await self._run_mt5(mt5.copy_rates_from_pos, symbol, timeframe, 0, limit)

            if rates is not None and len(rates) > 0:
                formatted_klines = []

                for rate in rates:
                    formatted_klines.append(
                        [
                            int(rate["time"]),  # timestamp
                            float(rate["open"]),  # open
                            float(rate["high"]),  # high
                            float(rate["low"]),  # low
                            float(rate["close"]),  # close
                            float(rate["tick_volume"]),  # volume
                            float(rate["real_volume"])
                            if "real_volume" in rate
                            else 0,  # real volume
                        ]
                    )

                return formatted_klines

            return []

        except Exception as e:
            logger.error(f"Error getting MT5 klines: {e}")
            return []

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get symbol trading rules from MT5.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Symbol information
        """
        try:
            info = await self._run_mt5(mt5.symbol_info, symbol)

            if info:
                return {
                    "symbol": info.name,
                    "base": info.currency_base,
                    "quote": info.currency_profit,
                    "margin_currency": info.currency_margin,
                    "digits": info.digits,
                    "point": info.point,
                    "trade_contract_size": info.trade_contract_size,
                    "trade_tick_value": info.trade_tick_value,
                    "trade_tick_size": info.trade_tick_size,
                    "volume_min": info.volume_min,
                    "volume_max": info.volume_max,
                    "volume_step": info.volume_step,
                    "trade_stops_level": info.trade_stops_level,
                    "trade_freeze_level": info.trade_freeze_level,
                    "trade_mode": self._get_trade_mode(info.trade_mode),
                    "trade_exe_mode": self._get_exe_mode(info.trade_exemode),
                    "swap_mode": self._get_swap_mode(info.swap_mode),
                    "swap_long": info.swap_long,
                    "swap_short": info.swap_short,
                    "margin_initial": info.margin_initial,
                    "margin_maintenance": info.margin_maintenance,
                    "session_deals": info.session_deals,
                    "session_buy_orders": info.session_buy_orders,
                    "session_sell_orders": info.session_sell_orders,
                    "volume": info.volume,
                    "volumehigh": info.volumehigh,
                    "volumelow": info.volumelow,
                    "bid": info.bid,
                    "ask": info.ask,
                    "last": info.last,
                    "spread": info.spread,
                    "raw_response": info._asdict(),
                }

            return {}

        except Exception as e:
            logger.error(f"Error getting MT5 symbol info: {e}")
            return {}

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get order history from MT5.

        Args:
            symbol: Optional symbol filter
            limit: Maximum number of orders

        Returns:
            List[Dict]: Historical orders
        """
        try:
            # Get history from the last 30 days
            from_date = datetime.now().timestamp() - (30 * 24 * 3600)
            to_date = datetime.now().timestamp()

            if symbol:
                history_orders = await self._run_mt5(
                    mt5.history_orders_get,
                    datetime.fromtimestamp(from_date),
                    datetime.fromtimestamp(to_date),
                    group=symbol,
                )
            else:
                history_orders = await self._run_mt5(
                    mt5.history_orders_get,
                    datetime.fromtimestamp(from_date),
                    datetime.fromtimestamp(to_date),
                )

            if history_orders:
                # Sort by time and limit
                sorted_orders = sorted(history_orders, key=lambda x: x.time_done, reverse=True)[
                    :limit
                ]

                return [self._format_order(order) for order in sorted_orders]

            return []

        except Exception as e:
            logger.error(f"Error getting MT5 order history: {e}")
            return []

    # WebSocket methods not implemented for MT5
    async def subscribe_orderbook(self, symbol: str, callback: callable) -> bool:
        """MT5 doesn't support WebSocket subscriptions."""
        logger.warning("MT5 doesn't support WebSocket orderbook subscription")
        return False

    async def subscribe_trades(self, symbol: str, callback: callable) -> bool:
        """MT5 doesn't support WebSocket subscriptions."""
        logger.warning("MT5 doesn't support WebSocket trades subscription")
        return False

    async def subscribe_klines(self, symbol: str, interval: str, callback: callable) -> bool:
        """MT5 doesn't support WebSocket subscriptions."""
        logger.warning("MT5 doesn't support WebSocket klines subscription")
        return False

    async def subscribe_account(self, callback: callable) -> bool:
        """MT5 doesn't support WebSocket subscriptions."""
        logger.warning("MT5 doesn't support WebSocket account subscription")
        return False

    async def close_position_by_ticket(
        self,
        position_ticket: int,
        volume: Optional[float] = None,
        comment: str = "Bot close",
        strategy: Optional[str] = None,
        signal_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Close a specific MT5 position by its ticket.

        Args:
            position_ticket: The MT5 position ticket to close
            volume: Volume to close (None = close full position)
            comment: Order comment
            strategy: Strategy name, telemetry attribution only
            signal_id: Originating signal id, telemetry attribution only
        """
        req_tick = None
        close_side = None

        try:
            await self._ensure_connected()

            # Get position details
            positions = await self._run_mt5(mt5.positions_get, ticket=position_ticket)
            if not positions:
                # Position may have already been closed by SL/TP on exchange
                logger.info(
                    f"Position {position_ticket} not found (likely already closed by SL/TP)"
                )
                self._record_order_telemetry(
                    event="EXIT",
                    outcome="ALREADY_CLOSED",
                    position_ticket=position_ticket,
                    strategy=strategy,
                    signal_id=signal_id,
                    exit_reason=comment,
                )
                return {"success": True, "already_closed": True}

            position = positions[0]
            close_volume = volume or position.volume

            # Reverse type for close
            close_type = (
                mt5.ORDER_TYPE_SELL
                if position.type == mt5.POSITION_TYPE_BUY
                else mt5.ORDER_TYPE_BUY
            )
            # Side of the closing ORDER — this is what adverse-slippage sign is based on.
            close_side = "SELL" if position.type == mt5.POSITION_TYPE_BUY else "BUY"

            # Get current price
            tick = await self._run_mt5(mt5.symbol_info_tick, position.symbol)
            if not tick:
                return {"success": False, "error": f"Cannot get tick for {position.symbol}"}

            req_tick = tick
            price = tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask

            # Force native Python types (RPyC proxy objects can cause issues)
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": str(position.symbol),
                "volume": float(close_volume),
                "type": close_type,
                "position": int(position_ticket),
                "price": float(price),
                "deviation": int(self.deviation),
                "magic": int(self.magic_number),
                "comment": str(comment),
                "type_filling": await self._get_filling_mode(str(position.symbol)),
            }

            logger.info(f"Closing position {position_ticket}: {request}")
            _t_send = time.perf_counter()
            result = await self._run_mt5(mt5.order_send, request)
            _latency_ms = (time.perf_counter() - _t_send) * 1000.0

            if result is None:
                # Check if position disappeared (closed by SL/TP while we were sending)
                recheck = await self._run_mt5(mt5.positions_get, ticket=int(position_ticket))
                if not recheck:
                    logger.info(
                        f"Position {position_ticket} closed during order send (likely SL/TP)"
                    )
                    self._record_order_telemetry(
                        event="EXIT",
                        outcome="ALREADY_CLOSED",
                        symbol=str(position.symbol),
                        side=close_side,
                        order_type="MARKET",
                        requested_price=float(price),
                        requested_volume=float(close_volume),
                        req_tick=req_tick,
                        latency_ms=_latency_ms,
                        strategy=strategy,
                        signal_id=signal_id,
                        position_ticket=position_ticket,
                        exit_reason=comment,
                    )
                    return {"success": True, "already_closed": True}
                last_err = mt5.last_error()
                logger.error(
                    f"order_send returned None for {position_ticket}, last_error: {last_err}"
                )
                self._record_order_telemetry(
                    event="EXIT",
                    outcome="ERROR",
                    symbol=str(position.symbol),
                    side=close_side,
                    order_type="MARKET",
                    requested_price=float(price),
                    requested_volume=float(close_volume),
                    req_tick=req_tick,
                    latency_ms=_latency_ms,
                    strategy=strategy,
                    signal_id=signal_id,
                    position_ticket=position_ticket,
                    exit_reason=comment,
                    error=f"order_send None, last_error: {last_err}",
                )
                self._consecutive_errors += 1
                if self._consecutive_errors >= self._max_errors_before_reconnect:
                    await self._ensure_connected()
                return {"success": False, "error": f"order_send None, last_error: {last_err}"}

            retcode_done_partial = getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)

            if result.retcode in (mt5.TRADE_RETCODE_DONE, retcode_done_partial):
                if result.retcode == retcode_done_partial:
                    logger.warning(
                        f"Partial fill closing {position_ticket}: "
                        f"requested {close_volume}, filled {result.volume}"
                    )
                logger.info(
                    f"Position {position_ticket} closed: {position.symbol} "
                    f"vol={close_volume} price={result.price}"
                )
                self._consecutive_errors = 0

                self._record_order_telemetry(
                    event="EXIT",
                    outcome="PARTIAL" if result.retcode == retcode_done_partial else "FILLED",
                    symbol=str(position.symbol),
                    side=close_side,
                    order_type="MARKET",
                    requested_price=float(price),
                    requested_volume=float(close_volume),
                    req_tick=req_tick,
                    result=result,
                    latency_ms=_latency_ms,
                    strategy=strategy,
                    signal_id=signal_id,
                    position_ticket=position_ticket,
                    exit_reason=comment,
                )

                return {
                    "success": True,
                    "order_id": str(result.order),
                    "deal_id": str(result.deal) if result.deal else None,
                    "price": result.price,
                    "volume": result.volume,
                    "partial_fill": result.retcode == retcode_done_partial,
                }
            else:
                error_msg = self._get_error_message(result.retcode if result else 0)
                raw = result._asdict() if hasattr(result, "_asdict") else str(result)
                logger.error(
                    f"Failed to close position {position_ticket}: {error_msg} | "
                    f"retcode={result.retcode} comment='{result.comment}' | raw={raw}"
                )
                self._record_order_telemetry(
                    event="EXIT",
                    outcome="REJECTED",
                    symbol=str(position.symbol),
                    side=close_side,
                    order_type="MARKET",
                    requested_price=float(price),
                    requested_volume=float(close_volume),
                    req_tick=req_tick,
                    result=result,
                    latency_ms=_latency_ms,
                    strategy=strategy,
                    signal_id=signal_id,
                    position_ticket=position_ticket,
                    exit_reason=comment,
                    error=error_msg,
                )
                self._consecutive_errors += 1
                if self._consecutive_errors >= self._max_errors_before_reconnect:
                    await self._ensure_connected()
                return {
                    "success": False,
                    "error": f"{error_msg}: {result.comment}"
                    if hasattr(result, "comment")
                    else error_msg,
                    "error_code": result.retcode if result else 0,
                }

        except Exception as e:
            logger.error(f"Error closing position {position_ticket}: {e}")
            # Locals beyond these may be unbound depending on where we threw.
            self._record_order_telemetry(
                event="EXIT",
                outcome="ERROR",
                side=close_side,
                order_type="MARKET",
                req_tick=req_tick,
                strategy=strategy,
                signal_id=signal_id,
                position_ticket=position_ticket,
                exit_reason=comment,
                error=str(e),
            )
            self._consecutive_errors += 1
            if self._consecutive_errors >= self._max_errors_before_reconnect:
                await self._ensure_connected()
            return {"success": False, "error": str(e)}

    async def get_closed_pnl(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        limit: int = 10,
        position_ticket: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get recently closed positions with actual PnL and exit price.

        Uses MT5 history_deals_get to find closing deals.
        Returns list of dicts with: entry_price, exit_price, closed_pnl, side, volume, ticket.

        Args:
            symbol: Trading symbol
            start_time: Start time in milliseconds (epoch). If None, last 10 minutes.
            limit: Max number of records to return
            position_ticket: If provided, get deals for this specific position
        """
        try:
            await self._ensure_connected()

            if position_ticket:
                # Get deals for specific position
                deals = await self._run_mt5(mt5.history_deals_get, position=position_ticket)
            else:
                # Get recent deals by time range — see _to_mt5_time().
                # utcnow() used to be here, and the window was off from the real
                # deals by several hours: this branch never found anything.
                offset = self._server_offset_sec()
                now_utc = time.time()
                if start_time:
                    dt_from = self._to_mt5_time(start_time / 1000, offset)
                else:
                    dt_from = self._to_mt5_time(now_utc - 600, offset)
                dt_to = self._to_mt5_time(now_utc + 60, offset)

                deals = await self._run_mt5(
                    mt5.history_deals_get, dt_from, dt_to, group=f"*{symbol}*"
                )

            if not deals:
                return []

            # Group deals by position_id to pair entry/exit
            position_deals: Dict[int, list] = {}
            for deal in deals:
                pos_id = deal.position_id
                if pos_id:
                    position_deals.setdefault(pos_id, []).append(deal)

            results = []
            for pos_id, pos_deals in position_deals.items():
                # Find entry (in) and exit (out) deals
                entry_deal = None
                exit_deal = None
                for d in pos_deals:
                    if d.entry == mt5.DEAL_ENTRY_IN:
                        entry_deal = d
                    elif d.entry == mt5.DEAL_ENTRY_OUT:
                        exit_deal = d

                if exit_deal:
                    entry_price = entry_deal.price if entry_deal else 0.0
                    results.append(
                        {
                            "entry_price": entry_price,
                            "exit_price": exit_deal.price,
                            "closed_pnl": exit_deal.profit,
                            "side": "BUY" if exit_deal.type == mt5.DEAL_TYPE_SELL else "SELL",
                            "volume": exit_deal.volume,
                            "ticket": pos_id,
                            "symbol": exit_deal.symbol,
                            "time": exit_deal.time,
                        }
                    )

            # Sort by time descending (most recent first), limit
            results.sort(key=lambda x: x.get("time", 0), reverse=True)
            return results[:limit]

        except Exception as e:
            logger.error(f"Error getting closed PnL for {symbol}: {e}")
            return []

    async def set_trading_stop(
        self,
        symbol: str,
        position_idx: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        sl_trigger_by: str = "LastPrice",
        tp_trigger_by: str = "LastPrice",
        position_ticket: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Set or update SL/TP on an existing position.
        MT5 equivalent of Bybit's set_trading_stop.

        Args:
            symbol: Trading symbol (e.g., XAUUSD)
            position_idx: 1=Long(BUY), 2=Short(SELL) — used to find correct position
            stop_loss: New stop loss price (0 = remove, None = don't change)
            take_profit: New take profit price (0 = remove, None = don't change)
            trailing_stop: Not used in MT5 (ignored)
            sl_trigger_by: Not used in MT5 (ignored)
            tp_trigger_by: Not used in MT5 (ignored)
            position_ticket: Specific MT5 position ticket (if provided, used instead of direction search)
        """
        try:
            await self._ensure_connected()

            positions = await self._run_mt5(mt5.positions_get, symbol=symbol)
            if not positions:
                return {"success": False, "error": f"No open position for {symbol}"}

            position = None

            # If specific ticket provided, find that exact position
            if position_ticket:
                for pos in positions:
                    if pos.ticket == position_ticket:
                        position = pos
                        break
                if not position:
                    # Try getting by ticket directly (may have different symbol format)
                    ticket_positions = await self._run_mt5(
                        mt5.positions_get, ticket=position_ticket
                    )
                    if ticket_positions:
                        position = ticket_positions[0]
                if not position:
                    # Ticket was specified but not found — position already closed.
                    # Do NOT fallback to another position (would modify wrong one).
                    logger.debug(
                        f"Position ticket {position_ticket} not found for {symbol}, "
                        f"likely already closed"
                    )
                    return {"success": True, "already_closed": True}

            # Fallback: find by direction (only when no specific ticket requested)
            if not position:
                target_type = mt5.POSITION_TYPE_BUY if position_idx == 1 else mt5.POSITION_TYPE_SELL
                for pos in positions:
                    if pos.type == target_type:
                        position = pos
                        break

            if not position:
                # Last resort: use first position for this symbol
                position = positions[0]

            order_params = {}
            if stop_loss is not None:
                order_params["stop_loss"] = stop_loss
            if take_profit is not None:
                order_params["take_profit"] = take_profit

            result = await self._modify_position(order_params, position)

            if result.get("success"):
                logger.info(
                    f"Trading stop updated: {symbol} ticket={position.ticket} "
                    f"SL={stop_loss} TP={take_profit}"
                )

            return result

        except Exception as e:
            logger.error(f"Error setting trading stop for {symbol}: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Private Methods ====================

    async def _modify_position(self, order_params: Dict[str, Any], position: Any) -> Dict[str, Any]:
        """Modify an open position."""
        try:
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": position.ticket,
                "symbol": position.symbol,
            }

            if "stop_loss" in order_params:
                request["sl"] = float(order_params["stop_loss"])
            else:
                request["sl"] = position.sl

            if "take_profit" in order_params:
                request["tp"] = float(order_params["take_profit"])
            else:
                request["tp"] = position.tp

            # Validate SL/TP against fresh tick to avoid Invalid stops
            # (cached prices may be stale due to high latency)
            tick = await self._run_mt5(mt5.symbol_info_tick, position.symbol)
            if tick:
                is_buy = position.type == mt5.POSITION_TYPE_BUY
                sl = request.get("sl", 0)
                tp = request.get("tp", 0)

                if is_buy:
                    # BUY: SL must be below bid, TP must be above ask
                    if sl > 0 and sl >= tick.bid:
                        request["sl"] = round(tick.bid * 0.9998, 2)
                        logger.debug(
                            f"SL adjusted: {sl:.2f} -> {request['sl']:.2f} "
                            f"(was >= bid {tick.bid:.2f})"
                        )
                    if tp > 0 and tp <= tick.ask:
                        request["tp"] = 0  # Remove TP, price already past
                        logger.debug(f"TP removed for BUY: {tp:.2f} <= ask {tick.ask:.2f}")
                else:
                    # SELL: SL must be above ask, TP must be below bid
                    if sl > 0 and sl <= tick.ask:
                        request["sl"] = round(tick.ask * 1.0002, 2)
                        logger.debug(
                            f"SL adjusted: {sl:.2f} -> {request['sl']:.2f} "
                            f"(was <= ask {tick.ask:.2f})"
                        )
                    if tp > 0 and tp >= tick.bid:
                        request["tp"] = 0  # Remove TP, price already past
                        logger.debug(f"TP removed for SELL: {tp:.2f} >= bid {tick.bid:.2f}")

                # Skip if nothing to change
                if request.get("sl", 0) == position.sl and request.get("tp", 0) == position.tp:
                    return {
                        "success": True,
                        "position_id": str(position.ticket),
                        "no_changes": True,
                    }

            result = await self._run_mt5(mt5.order_send, request)

            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return {
                    "success": True,
                    "position_id": str(position.ticket),
                    "raw_response": result._asdict(),
                }
            elif result and result.retcode == mt5.TRADE_RETCODE_NO_CHANGES:
                # SL/TP already at desired price — treat as success
                logger.debug(f"Position {position.ticket} SL/TP unchanged (already set)")
                return {
                    "success": True,
                    "position_id": str(position.ticket),
                    "no_changes": True,
                }
            else:
                error_msg = self._get_error_message(result.retcode if result else 0)
                logger.warning(
                    f"Failed to modify position {position.ticket}: {error_msg} "
                    f"(retcode={result.retcode if result else 'None'})"
                )
                return {
                    "success": False,
                    "error": error_msg,
                    "error_code": result.retcode if result else 0,
                }

        except Exception as e:
            logger.error(f"Error modifying MT5 position: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @staticmethod
    def _order_comment(order_params: Dict[str, Any]) -> str:
        """MT5 position comment: explicit comment, else strategy name, else "Python Bot".

        MT5 stores up to 31 characters; the broker may reject non-ASCII, so ASCII only.
        """
        raw = order_params.get("comment") or order_params.get("strategy") or "Python Bot"
        text = str(raw).encode("ascii", "ignore").decode().strip()
        return (text or "Python Bot")[:31]

    def _convert_order_type(self, order_type: str, side: str) -> int:
        """Convert order type to MT5 format."""
        if order_type == "MARKET":
            return mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
        elif order_type == "LIMIT":
            return mt5.ORDER_TYPE_BUY_LIMIT if side == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        elif order_type == "STOP_MARKET":
            return mt5.ORDER_TYPE_BUY_STOP if side == "BUY" else mt5.ORDER_TYPE_SELL_STOP
        elif order_type == "STOP_LIMIT":
            return (
                mt5.ORDER_TYPE_BUY_STOP_LIMIT if side == "BUY" else mt5.ORDER_TYPE_SELL_STOP_LIMIT
            )
        else:
            return mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL

    def _convert_time_in_force(self, tif: str) -> int:
        """Convert time in force to MT5 format.
        Note: MT5 does not have ORDER_TIME_IOC/FOK — IOC/FOK are filling modes, not time types.
        Map IOC/FOK to GTC for MT5 compatibility."""
        mapping = {
            "GTC": mt5.ORDER_TIME_GTC,
            "DAY": mt5.ORDER_TIME_DAY,
        }
        return mapping.get(tif, mt5.ORDER_TIME_GTC)

    def _convert_interval_to_mt5(self, interval: str) -> int:
        """Convert interval to MT5 timeframe."""
        mapping = {
            "1m": mt5.TIMEFRAME_M1,
            "2m": mt5.TIMEFRAME_M2,
            "3m": mt5.TIMEFRAME_M3,
            "4m": mt5.TIMEFRAME_M4,
            "5m": mt5.TIMEFRAME_M5,
            "6m": mt5.TIMEFRAME_M6,
            "10m": mt5.TIMEFRAME_M10,
            "12m": mt5.TIMEFRAME_M12,
            "15m": mt5.TIMEFRAME_M15,
            "20m": mt5.TIMEFRAME_M20,
            "30m": mt5.TIMEFRAME_M30,
            "1h": mt5.TIMEFRAME_H1,
            "2h": mt5.TIMEFRAME_H2,
            "3h": mt5.TIMEFRAME_H3,
            "4h": mt5.TIMEFRAME_H4,
            "6h": mt5.TIMEFRAME_H6,
            "8h": mt5.TIMEFRAME_H8,
            "12h": mt5.TIMEFRAME_H12,
            "1d": mt5.TIMEFRAME_D1,
            "1w": mt5.TIMEFRAME_W1,
            "1M": mt5.TIMEFRAME_MN1,
        }
        return mapping.get(interval, mt5.TIMEFRAME_M1)

    def _get_error_message(self, retcode: int) -> str:
        """Get error message for MT5 return code."""
        errors = {
            mt5.TRADE_RETCODE_REQUOTE: "Requote",
            mt5.TRADE_RETCODE_REJECT: "Request rejected",
            mt5.TRADE_RETCODE_CANCEL: "Request cancelled",
            mt5.TRADE_RETCODE_PLACED: "Order placed",
            mt5.TRADE_RETCODE_DONE: "Request completed",
            mt5.TRADE_RETCODE_DONE_PARTIAL: "Request partially completed",
            mt5.TRADE_RETCODE_ERROR: "Request processing error",
            mt5.TRADE_RETCODE_TIMEOUT: "Request timeout",
            mt5.TRADE_RETCODE_INVALID: "Invalid request",
            mt5.TRADE_RETCODE_INVALID_VOLUME: "Invalid volume",
            mt5.TRADE_RETCODE_INVALID_PRICE: "Invalid price",
            mt5.TRADE_RETCODE_INVALID_STOPS: "Invalid stops",
            mt5.TRADE_RETCODE_TRADE_DISABLED: "Trade disabled",
            mt5.TRADE_RETCODE_MARKET_CLOSED: "Market closed",
            mt5.TRADE_RETCODE_NO_MONEY: "Insufficient funds",
            mt5.TRADE_RETCODE_PRICE_CHANGED: "Price changed",
            mt5.TRADE_RETCODE_PRICE_OFF: "No quotes",
            mt5.TRADE_RETCODE_INVALID_EXPIRATION: "Invalid expiration",
            mt5.TRADE_RETCODE_ORDER_CHANGED: "Order changed",
            mt5.TRADE_RETCODE_TOO_MANY_REQUESTS: "Too many requests",
            mt5.TRADE_RETCODE_NO_CHANGES: "No changes",
            mt5.TRADE_RETCODE_SERVER_DISABLES_AT: "Autotrading disabled by server",
            mt5.TRADE_RETCODE_CLIENT_DISABLES_AT: "Autotrading disabled by client",
            mt5.TRADE_RETCODE_LOCKED: "Request locked",
            mt5.TRADE_RETCODE_FROZEN: "Order or position frozen",
            mt5.TRADE_RETCODE_INVALID_FILL: "Invalid fill type",
            mt5.TRADE_RETCODE_CONNECTION: "No connection",
            mt5.TRADE_RETCODE_ONLY_REAL: "Only for real accounts",
            mt5.TRADE_RETCODE_LIMIT_ORDERS: "Limit orders exceeded",
            mt5.TRADE_RETCODE_LIMIT_VOLUME: "Volume limit exceeded",
            mt5.TRADE_RETCODE_INVALID_ORDER: "Invalid order",
            mt5.TRADE_RETCODE_POSITION_CLOSED: "Position already closed",
        }
        return errors.get(retcode, f"Unknown error code: {retcode}")

    def _format_order(self, order: Any) -> Dict[str, Any]:
        """Format MT5 order to standard format."""
        return {
            "ticket": order.ticket,
            "time_setup": order.time_setup,
            "time_setup_msc": order.time_setup_msc,
            "time_done": order.time_done,
            "time_done_msc": order.time_done_msc,
            "time_expiration": order.time_expiration,
            "type": self._get_order_type_name(order.type),
            "type_time": self._get_time_type_name(order.type_time),
            "type_filling": self._get_filling_type_name(order.type_filling),
            "state": self._get_order_state_name(order.state),
            "magic": order.magic,
            "position_id": order.position_id,
            "position_by_id": order.position_by_id,
            "reason": self._get_order_reason_name(order.reason),
            "volume_initial": order.volume_initial,
            "volume_current": order.volume_current,
            "price_open": order.price_open,
            "sl": order.sl,
            "tp": order.tp,
            "price_current": order.price_current,
            "price_stoplimit": order.price_stoplimit,
            "symbol": order.symbol,
            "comment": order.comment,
            "external_id": order.external_id,
            "raw_response": order._asdict(),
        }

    def _get_order_type_name(self, order_type: int) -> str:
        """Convert MT5 order type to string."""
        types = {
            mt5.ORDER_TYPE_BUY: "BUY",
            mt5.ORDER_TYPE_SELL: "SELL",
            mt5.ORDER_TYPE_BUY_LIMIT: "BUY_LIMIT",
            mt5.ORDER_TYPE_SELL_LIMIT: "SELL_LIMIT",
            mt5.ORDER_TYPE_BUY_STOP: "BUY_STOP",
            mt5.ORDER_TYPE_SELL_STOP: "SELL_STOP",
            mt5.ORDER_TYPE_BUY_STOP_LIMIT: "BUY_STOP_LIMIT",
            mt5.ORDER_TYPE_SELL_STOP_LIMIT: "SELL_STOP_LIMIT",
            mt5.ORDER_TYPE_CLOSE_BY: "CLOSE_BY",
        }
        return types.get(order_type, f"UNKNOWN_{order_type}")

    def _get_time_type_name(self, time_type: int) -> str:
        """Convert MT5 time type to string."""
        types = {
            mt5.ORDER_TIME_GTC: "GTC",
            mt5.ORDER_TIME_DAY: "DAY",
        }
        return types.get(time_type, f"UNKNOWN_{time_type}")

    def _get_filling_type_name(self, filling_type: int) -> str:
        """Convert MT5 filling type to string."""
        types = {
            mt5.ORDER_FILLING_FOK: "FOK",
            mt5.ORDER_FILLING_IOC: "IOC",
            mt5.ORDER_FILLING_RETURN: "RETURN",
        }
        return types.get(filling_type, f"UNKNOWN_{filling_type}")

    def _get_order_state_name(self, state: int) -> str:
        """Convert MT5 order state to string."""
        states = {
            mt5.ORDER_STATE_STARTED: "STARTED",
            mt5.ORDER_STATE_PLACED: "PLACED",
            mt5.ORDER_STATE_CANCELED: "CANCELED",
            mt5.ORDER_STATE_PARTIAL: "PARTIAL",
            mt5.ORDER_STATE_FILLED: "FILLED",
            mt5.ORDER_STATE_REJECTED: "REJECTED",
            mt5.ORDER_STATE_EXPIRED: "EXPIRED",
            mt5.ORDER_STATE_REQUEST_ADD: "REQUEST_ADD",
            mt5.ORDER_STATE_REQUEST_MODIFY: "REQUEST_MODIFY",
            mt5.ORDER_STATE_REQUEST_CANCEL: "REQUEST_CANCEL",
        }
        return states.get(state, f"UNKNOWN_{state}")

    def _get_order_reason_name(self, reason: int) -> str:
        """Convert MT5 order reason to string."""
        reasons = {
            mt5.ORDER_REASON_CLIENT: "CLIENT",
            mt5.ORDER_REASON_MOBILE: "MOBILE",
            mt5.ORDER_REASON_WEB: "WEB",
            mt5.ORDER_REASON_EXPERT: "EXPERT",
            mt5.ORDER_REASON_SL: "STOP_LOSS",
            mt5.ORDER_REASON_TP: "TAKE_PROFIT",
            mt5.ORDER_REASON_SO: "STOP_OUT",
        }
        return reasons.get(reason, f"UNKNOWN_{reason}")

    def _get_margin_mode(self, mode: int) -> str:
        """Convert MT5 margin mode to string."""
        modes = {
            mt5.ACCOUNT_MARGIN_MODE_RETAIL_NETTING: "RETAIL_NETTING",
            mt5.ACCOUNT_MARGIN_MODE_EXCHANGE: "EXCHANGE",
            mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING: "RETAIL_HEDGING",
        }
        return modes.get(mode, f"UNKNOWN_{mode}")

    def _get_trade_mode(self, mode: int) -> str:
        """Convert MT5 trade mode to string."""
        modes = {
            mt5.SYMBOL_TRADE_MODE_DISABLED: "DISABLED",
            mt5.SYMBOL_TRADE_MODE_LONGONLY: "LONG_ONLY",
            mt5.SYMBOL_TRADE_MODE_SHORTONLY: "SHORT_ONLY",
            mt5.SYMBOL_TRADE_MODE_CLOSEONLY: "CLOSE_ONLY",
            mt5.SYMBOL_TRADE_MODE_FULL: "FULL",
        }
        return modes.get(mode, f"UNKNOWN_{mode}")

    def _get_exe_mode(self, mode: int) -> str:
        """Convert MT5 execution mode to string."""
        modes = {
            mt5.SYMBOL_TRADE_EXECUTION_REQUEST: "REQUEST",
            mt5.SYMBOL_TRADE_EXECUTION_INSTANT: "INSTANT",
            mt5.SYMBOL_TRADE_EXECUTION_MARKET: "MARKET",
            mt5.SYMBOL_TRADE_EXECUTION_EXCHANGE: "EXCHANGE",
        }
        return modes.get(mode, f"UNKNOWN_{mode}")

    def _get_swap_mode(self, mode: int) -> str:
        """Convert MT5 swap mode to string."""
        modes = {
            mt5.SYMBOL_SWAP_MODE_DISABLED: "DISABLED",
            mt5.SYMBOL_SWAP_MODE_POINTS: "POINTS",
            mt5.SYMBOL_SWAP_MODE_CURRENCY_SYMBOL: "CURRENCY_SYMBOL",
            mt5.SYMBOL_SWAP_MODE_CURRENCY_MARGIN: "CURRENCY_MARGIN",
            mt5.SYMBOL_SWAP_MODE_CURRENCY_DEPOSIT: "CURRENCY_DEPOSIT",
            mt5.SYMBOL_SWAP_MODE_INTEREST_CURRENT: "INTEREST_CURRENT",
            mt5.SYMBOL_SWAP_MODE_INTEREST_OPEN: "INTEREST_OPEN",
            mt5.SYMBOL_SWAP_MODE_REOPEN_CURRENT: "REOPEN_CURRENT",
            mt5.SYMBOL_SWAP_MODE_REOPEN_BID: "REOPEN_BID",
        }
        return modes.get(mode, f"UNKNOWN_{mode}")
