"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : bybit_connector
    Project     : Project-103
    Description : Bybit exchange connector. This module implements the Bybit exchange connector.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

from src.execution.connectors.base_connector import BaseConnector
from src.utils.network import create_robust_session

logger = logging.getLogger(__name__)


def safe_float(val, default: float = 0.0) -> float:
    """
    Safely convert a value to float.

    Handles None, empty strings, and other edge cases from API responses.
    """
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default: int = 0) -> int:
    """
    Safely convert a value to int.

    Handles None, empty strings, and other edge cases from API responses.
    """
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


class BybitConnector(BaseConnector):
    """Bybit exchange connector implementation."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        demo: bool = False,
        account_name: str = "",
    ):
        """
        Initialize Bybit connector.

        Args:
            api_key: API key
            api_secret: API secret
            testnet: Whether to use testnet
            demo: Whether to use demo trading (mainnet with demo account)
            account_name: Human-readable account name for logging
        """
        super().__init__(api_key, api_secret, testnet)

        self.demo = demo
        self.account_name = account_name or "Unknown"

        # Set endpoints based on mode
        if testnet:
            self.base_url = "https://api-testnet.bybit.com"
            self.ws_public_url = "wss://stream-testnet.bybit.com"
            self.ws_private_url = "wss://stream-testnet.bybit.com"
        elif demo:
            # Demo uses dedicated demo API endpoint
            # Public WS uses mainnet (demo doesn't support public streams)
            # Private WS uses demo stream
            self.base_url = "https://api-demo.bybit.com"  # Demo REST API
            self.ws_public_url = "wss://stream.bybit.com"  # Public data from mainnet
            self.ws_private_url = "wss://stream-demo.bybit.com"  # Private (orders) from demo
        else:
            # Live mainnet
            self.base_url = "https://api.bybit.com"
            self.ws_public_url = "wss://stream.bybit.com"
            self.ws_private_url = "wss://stream.bybit.com"

        # Legacy compatibility
        self.ws_url = self.ws_public_url

        # API paths
        self.api_version = "/v5"

        # Session for HTTP requests
        self.session: Optional[aiohttp.ClientSession] = None

        # WebSocket connections
        self.ws_public: Optional[aiohttp.ClientWebSocketResponse] = None
        self.ws_private: Optional[aiohttp.ClientWebSocketResponse] = None

        # Subscription callbacks
        self.callbacks = {
            "orderbook": {},
            "trades": {},
            "klines": {},
            "account": None,
        }

        # Request ID counter
        self.request_id = 0

        # Determine mode string for logging
        if demo:
            mode_str = "demo (mainnet)"
        elif testnet:
            mode_str = "testnet"
        else:
            mode_str = "live (mainnet)"

        logger.info(f"BybitConnector initialized: {mode_str}, account: {self.account_name}")

    async def connect(self) -> bool:
        """
        Establish connection to Bybit.

        Returns:
            bool: Connection success status
        """
        try:
            # Create HTTP session
            if not self.session:
                self.session = create_robust_session()

            # Test connection
            response = await self._make_request("GET", "/v5/market/time")

            if response and "result" in response:
                self.is_connected = True
                logger.info("Connected to Bybit successfully")

                # Start WebSocket connections
                await self._connect_websockets()

                return True

            logger.error("Failed to connect to Bybit")
            return False

        except Exception as e:
            logger.error(f"Error connecting to Bybit: {e}")
            return False

    async def disconnect(self) -> bool:
        """
        Disconnect from Bybit.

        Returns:
            bool: Disconnection success status
        """
        try:
            # Close WebSocket connections
            if self.ws_public:
                await self.ws_public.close()
                self.ws_public = None

            if self.ws_private:
                await self.ws_private.close()
                self.ws_private = None

            # Close HTTP session
            if self.session:
                await self.session.close()
                self.session = None

            self.is_connected = False
            logger.info("Disconnected from Bybit")
            return True

        except Exception as e:
            logger.error(f"Error disconnecting from Bybit: {e}")
            return False

    async def place_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place an order on Bybit.

        Args:
            order_params: Order parameters

        Returns:
            Dict: Order placement result
        """
        try:
            # Prepare Bybit order parameters
            bybit_params = {
                "category": "linear",  # USDT perpetual
                "symbol": order_params["symbol"],
                "side": self._convert_side(order_params["side"]),
                "orderType": self._convert_order_type(order_params["type"]),
                "qty": str(order_params["quantity"]),
                "timeInForce": order_params.get("time_in_force", "GTC"),
                "reduceOnly": order_params.get("reduce_only", False),
                "closeOnTrigger": order_params.get("close_on_trigger", False),
            }

            # Add price for limit orders
            if order_params["type"] in ["LIMIT", "STOP_LIMIT", "TAKE_PROFIT_LIMIT"]:
                bybit_params["price"] = str(order_params["price"])

            # Add stop price for stop orders
            if "stop_price" in order_params:
                bybit_params["triggerPrice"] = str(order_params["stop_price"])
                bybit_params["triggerBy"] = order_params.get("trigger_by", "LastPrice")

                # Set triggerDirection based on order type and side
                # 1 = trigger when price rises, 2 = trigger when price falls
                order_type_upper = order_params["type"].upper()
                side_upper = order_params["side"].upper()

                if order_type_upper in ("STOP_MARKET", "STOP_LIMIT"):
                    # Stop loss: triggers in unfavorable direction
                    # LONG SL: triggers when price falls (direction=2)
                    # SHORT SL: triggers when price rises (direction=1)
                    bybit_params["triggerDirection"] = 2 if side_upper == "SELL" else 1
                elif order_type_upper in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT_LIMIT"):
                    # Take profit: triggers in favorable direction
                    # LONG TP: triggers when price rises (direction=1)
                    # SHORT TP: triggers when price falls (direction=2)
                    bybit_params["triggerDirection"] = 1 if side_upper == "SELL" else 2

            # Add stop loss if provided
            if "stop_loss" in order_params:
                bybit_params["stopLoss"] = str(order_params["stop_loss"])
                bybit_params["slTriggerBy"] = "LastPrice"

            # Add take profit if provided
            if "take_profit" in order_params:
                bybit_params["takeProfit"] = str(order_params["take_profit"])
                bybit_params["tpTriggerBy"] = "LastPrice"

            # Set position index for Hedge Mode
            # If position_idx is directly provided, use it
            # Otherwise, calculate from position_mode if provided
            if "position_idx" in order_params:
                bybit_params["positionIdx"] = order_params["position_idx"]
            elif order_params.get("position_mode"):
                bybit_params["positionIdx"] = self._get_position_idx(
                    order_params["side"], order_params.get("position_mode")
                )

            # Make API request
            response = await self._make_request("POST", "/v5/order/create", data=bybit_params)

            if response and response.get("retCode") == 0:
                result = response.get("result", {})
                return {
                    "success": True,
                    "order_id": result.get("orderId"),
                    "client_order_id": result.get("orderLinkId"),
                    "status": self._convert_order_status(result.get("status")),
                    "price": safe_float(result.get("price")),
                    "quantity": safe_float(result.get("qty")),
                    "timestamp": safe_int(result.get("createdTime")),
                    "raw_response": result,
                }
            else:
                return {
                    "success": False,
                    "error": response.get("retMsg", "Unknown error"),
                    "error_code": response.get("retCode"),
                    "raw_response": response,
                }

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_code": "EXCEPTION",
            }

    async def cancel_order(
        self,
        order_params: Optional[Dict[str, Any]] = None,
        *,
        symbol: Optional[str] = None,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Cancel an order on Bybit.

        Supports both dict-style and keyword arguments:
            cancel_order({"symbol": "BTCUSDT", "order_id": "123"})
            cancel_order(symbol="BTCUSDT", order_id="123")

        Args:
            order_params: Order cancellation parameters (dict style)
            symbol: Trading symbol (keyword style)
            order_id: Order ID (keyword style)
            client_order_id: Client order ID (keyword style)

        Returns:
            Dict: Cancellation result
        """
        try:
            # Support both dict and keyword arguments
            if order_params:
                _symbol = order_params.get("symbol")
                _order_id = order_params.get("order_id")
                _client_order_id = order_params.get("client_order_id")
            else:
                _symbol = symbol
                _order_id = order_id
                _client_order_id = client_order_id

            if not _symbol:
                return {
                    "success": False,
                    "error": "Symbol required",
                }

            bybit_params = {
                "category": "linear",
                "symbol": _symbol,
            }

            # Use order ID or client order ID
            if _order_id:
                bybit_params["orderId"] = _order_id
            elif _client_order_id:
                bybit_params["orderLinkId"] = _client_order_id
            else:
                return {
                    "success": False,
                    "error": "Order ID required",
                }

            response = await self._make_request("POST", "/v5/order/cancel", data=bybit_params)

            if response and response.get("retCode") == 0:
                return {
                    "success": True,
                    "order_id": response.get("result", {}).get("orderId"),
                    "raw_response": response,
                }
            else:
                return {
                    "success": False,
                    "error": response.get("retMsg", "Unknown error"),
                    "error_code": response.get("retCode"),
                    "raw_response": response,
                }

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_code": "EXCEPTION",
            }

    async def modify_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Modify an existing order on Bybit.

        Args:
            order_params: Order modification parameters

        Returns:
            Dict: Modification result
        """
        try:
            bybit_params = {
                "category": "linear",
                "symbol": order_params["symbol"],
            }

            # Order identification
            if "order_id" in order_params:
                bybit_params["orderId"] = order_params["order_id"]
            elif "client_order_id" in order_params:
                bybit_params["orderLinkId"] = order_params["client_order_id"]

            # Modifications
            if "price" in order_params:
                bybit_params["price"] = str(order_params["price"])

            if "quantity" in order_params:
                bybit_params["qty"] = str(order_params["quantity"])

            if "trigger_price" in order_params:
                bybit_params["triggerPrice"] = str(order_params["trigger_price"])

            if "stop_loss" in order_params:
                bybit_params["stopLoss"] = str(order_params["stop_loss"])

            if "take_profit" in order_params:
                bybit_params["takeProfit"] = str(order_params["take_profit"])

            response = await self._make_request("POST", "/v5/order/amend", data=bybit_params)

            if response and response.get("retCode") == 0:
                return {
                    "success": True,
                    "order_id": response.get("result", {}).get("orderId"),
                    "raw_response": response,
                }
            else:
                return {
                    "success": False,
                    "error": response.get("retMsg", "Unknown error"),
                    "error_code": response.get("retCode"),
                    "raw_response": response,
                }

        except Exception as e:
            logger.error(f"Error modifying order: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_code": "EXCEPTION",
            }

    async def set_trading_stop(
        self,
        symbol: str,
        position_idx: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop: Optional[float] = None,
        sl_trigger_by: str = "LastPrice",
        tp_trigger_by: str = "LastPrice",
    ) -> Dict[str, Any]:
        """
        Set or update trading stop (SL/TP) on an existing position.

        This is used to update stop loss after break-even activation.

        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            position_idx: Position index for hedge mode (1=Long, 2=Short)
            stop_loss: New stop loss price (None = don't change)
            take_profit: New take profit price (None = don't change)
            trailing_stop: Trailing stop distance in price (None = don't use)
            sl_trigger_by: SL trigger price type (LastPrice, MarkPrice, IndexPrice)
            tp_trigger_by: TP trigger price type

        Returns:
            Dict: Result with success status
        """
        try:
            bybit_params = {
                "category": "linear",
                "symbol": symbol,
                "positionIdx": position_idx,
            }

            if stop_loss is not None:
                bybit_params["stopLoss"] = str(stop_loss)
                bybit_params["slTriggerBy"] = sl_trigger_by

            if take_profit is not None:
                bybit_params["takeProfit"] = str(take_profit)
                bybit_params["tpTriggerBy"] = tp_trigger_by

            if trailing_stop is not None:
                bybit_params["trailingStop"] = str(trailing_stop)

            response = await self._make_request(
                "POST", "/v5/position/trading-stop", data=bybit_params
            )

            if response and response.get("retCode") == 0:
                logger.info(
                    f"Trading stop updated: {symbol} idx={position_idx} "
                    f"SL={stop_loss} TP={take_profit}"
                )
                return {
                    "success": True,
                    "raw_response": response,
                }
            else:
                error_msg = response.get("retMsg", "Unknown error") if response else "No response"
                error_code = response.get("retCode") if response else "NO_RESPONSE"
                logger.error(
                    f"Failed to set trading stop: {error_msg} | "
                    f"Symbol: {symbol}, idx: {position_idx}, SL: {stop_loss}, TP: {take_profit} | "
                    f"Code: {error_code}"
                )
                return {
                    "success": False,
                    "error": error_msg,
                    "error_code": error_code,
                    "raw_response": response,
                }

        except Exception as e:
            logger.error(f"Error setting trading stop: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_code": "EXCEPTION",
            }

    async def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """
        Get order details from Bybit.

        Alias for get_order_status with swapped argument order.

        Args:
            symbol: Trading symbol
            order_id: Order ID

        Returns:
            Dict: Order details
        """
        return await self.get_order_status(order_id=order_id, symbol=symbol)

    async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Get order status from Bybit.

        Args:
            order_id: Order ID
            symbol: Trading symbol

        Returns:
            Dict: Order status
        """
        try:
            params = {
                "category": "linear",
                "symbol": symbol,
                "orderId": order_id,
            }

            response = await self._make_request("GET", "/v5/order/realtime", params=params)

            if response and response.get("retCode") == 0:
                orders = response.get("result", {}).get("list", [])
                if orders:
                    order = orders[0]
                    return {
                        "order_id": order.get("orderId"),
                        "symbol": order.get("symbol"),
                        "side": order.get("side"),
                        "type": order.get("orderType"),
                        "status": self._convert_order_status(order.get("orderStatus")),
                        "quantity": safe_float(order.get("qty")),
                        "filled_quantity": safe_float(order.get("cumExecQty")),
                        "price": safe_float(order.get("price")),
                        "average_price": safe_float(order.get("avgPrice")),
                        "created_time": safe_int(order.get("createdTime")),
                        "updated_time": safe_int(order.get("updatedTime")),
                        "raw_response": order,
                    }

            return {}

        except Exception as e:
            logger.error(f"Error getting order status: {e}")
            return {}

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all open orders from Bybit.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Open orders
        """
        try:
            params = {
                "category": "linear",
                "settleCoin": "USDT",
            }

            if symbol:
                params["symbol"] = symbol

            response = await self._make_request("GET", "/v5/order/realtime", params=params)

            if response and response.get("retCode") == 0:
                orders = response.get("result", {}).get("list", [])
                return [self._format_order(order) for order in orders]

            return []

        except Exception as e:
            logger.error(f"Error getting open orders: {e}")
            return []

    async def get_conditional_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get conditional orders (SL/TP) from Bybit.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Conditional orders (stop loss, take profit)
        """
        all_orders = []

        # Query both StopOrder and tpslOrder types
        # StopOrder = conditional stop orders
        # tpslOrder = take profit / stop loss orders attached to positions
        for order_filter in ["StopOrder", "tpslOrder"]:
            try:
                params = {
                    "category": "linear",
                    "settleCoin": "USDT",
                    "orderFilter": order_filter,
                }

                if symbol:
                    params["symbol"] = symbol

                response = await self._make_request("GET", "/v5/order/realtime", params=params)

                if response and response.get("retCode") == 0:
                    orders = response.get("result", {}).get("list", [])
                    all_orders.extend([self._format_order(order) for order in orders])
                else:
                    ret_code = response.get("retCode") if response else "no response"
                    ret_msg = response.get("retMsg") if response else ""
                    logger.warning(
                        f"get_conditional_orders({symbol}): {order_filter} failed: {ret_code} - {ret_msg}"
                    )

            except Exception as e:
                logger.error(f"Error getting {order_filter} orders: {e}")

        return all_orders

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get open positions from Bybit.

        Args:
            symbol: Optional symbol filter

        Returns:
            List[Dict]: Open positions
        """
        try:
            params = {
                "category": "linear",
                "settleCoin": "USDT",
            }

            if symbol:
                params["symbol"] = symbol

            response = await self._make_request("GET", "/v5/position/list", params=params)

            if response and response.get("retCode") == 0:
                positions = response.get("result", {}).get("list", [])
                formatted_positions = []

                for pos in positions:
                    size = safe_float(pos.get("size"))
                    if size > 0:
                        formatted_positions.append(
                            {
                                "symbol": pos.get("symbol"),
                                "side": pos.get("side"),
                                "size": size,
                                "entry_price": safe_float(pos.get("avgPrice")),
                                "mark_price": safe_float(pos.get("markPrice")),
                                "unrealized_pnl": safe_float(pos.get("unrealisedPnl")),
                                "realized_pnl": safe_float(pos.get("realisedPnl")),
                                "margin": safe_float(pos.get("positionIM")),
                                "leverage": safe_float(pos.get("leverage"), 1.0),
                                "liquidation_price": safe_float(pos.get("liqPrice")),
                                "stop_loss": safe_float(pos.get("stopLoss")),
                                "take_profit": safe_float(pos.get("takeProfit")),
                                "trailing_stop": safe_float(pos.get("trailingStop")),
                                "created_time": safe_int(pos.get("createdTime")),
                                "updated_time": safe_int(pos.get("updatedTime")),
                                "raw_response": pos,
                            }
                        )

                return formatted_positions

            return []

        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []

    async def get_account_info(self) -> Dict[str, Any]:
        """
        Get account information from Bybit.

        Returns:
            Dict: Account information
        """
        try:
            params = {
                "accountType": "UNIFIED",
            }

            response = await self._make_request("GET", "/v5/account/wallet-balance", params=params)

            if response and response.get("retCode") == 0:
                accounts = response.get("result", {}).get("list", [])
                if accounts:
                    account = accounts[0]
                    return {
                        "account_type": account.get("accountType"),
                        "total_equity": safe_float(account.get("totalEquity")),
                        "total_wallet_balance": safe_float(account.get("totalWalletBalance")),
                        "total_margin_balance": safe_float(account.get("totalMarginBalance")),
                        "available_balance": safe_float(account.get("totalAvailableBalance")),
                        "total_pnl": safe_float(account.get("totalPerpUPL")),
                        "total_initial_margin": safe_float(account.get("totalInitialMargin")),
                        "total_maintenance_margin": safe_float(
                            account.get("totalMaintenanceMargin")
                        ),
                        "account_im_rate": safe_float(account.get("accountIMRate")),
                        "account_mm_rate": safe_float(account.get("accountMMRate")),
                        "coins": account.get("coin", []),
                        "raw_response": account,
                    }

            return {}

        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return {}

    async def get_balance(self) -> Dict[str, float]:
        """
        Get account balance from Bybit.

        Returns:
            Dict: Asset balances
        """
        try:
            account_info = await self.get_account_info()

            if account_info:
                balances = {}
                for coin_data in account_info.get("coins", []):
                    symbol = coin_data.get("coin")
                    # Use walletBalance or equity
                    balance = safe_float(coin_data.get("walletBalance") or coin_data.get("equity"))
                    if balance > 0:
                        balances[symbol] = balance

                return balances

            return {}

        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return {}

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get ticker data from Bybit.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Ticker data
        """
        try:
            params = {
                "category": "linear",
                "symbol": symbol,
            }

            response = await self._make_request("GET", "/v5/market/tickers", params=params)

            if response and response.get("retCode") == 0:
                tickers = response.get("result", {}).get("list", [])
                if tickers:
                    ticker = tickers[0]
                    return {
                        "symbol": ticker.get("symbol"),
                        "last_price": safe_float(ticker.get("lastPrice")),
                        "bid_price": safe_float(ticker.get("bid1Price")),
                        "ask_price": safe_float(ticker.get("ask1Price")),
                        "bid_size": safe_float(ticker.get("bid1Size")),
                        "ask_size": safe_float(ticker.get("ask1Size")),
                        "volume_24h": safe_float(ticker.get("volume24h")),
                        "turnover_24h": safe_float(ticker.get("turnover24h")),
                        "high_24h": safe_float(ticker.get("highPrice24h")),
                        "low_24h": safe_float(ticker.get("lowPrice24h")),
                        "prev_close": safe_float(ticker.get("prevPrice24h")),
                        "price_change_24h": safe_float(ticker.get("price24hPcnt")),
                        "mark_price": safe_float(ticker.get("markPrice")),
                        "index_price": safe_float(ticker.get("indexPrice")),
                        "open_interest": safe_float(ticker.get("openInterest")),
                        "funding_rate": safe_float(ticker.get("fundingRate")),
                        "next_funding_time": safe_int(ticker.get("nextFundingTime")),
                        "raw_response": ticker,
                    }

            return {}

        except Exception as e:
            logger.error(f"Error getting ticker: {e}")
            return {}

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get order book from Bybit.

        Args:
            symbol: Trading symbol
            limit: Order book depth

        Returns:
            Dict: Order book data
        """
        try:
            params = {
                "category": "linear",
                "symbol": symbol,
                "limit": min(limit, 200),  # Bybit max is 200
            }

            response = await self._make_request("GET", "/v5/market/orderbook", params=params)

            if response and response.get("retCode") == 0:
                result = response.get("result", {})
                return {
                    "symbol": symbol,
                    "timestamp": int(result.get("ts", 0)),
                    "bids": [[float(p), float(q)] for p, q in result.get("b", [])],
                    "asks": [[float(p), float(q)] for p, q in result.get("a", [])],
                    "raw_response": result,
                }

            return {}

        except Exception as e:
            logger.error(f"Error getting orderbook: {e}")
            return {}

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get recent trades from Bybit.

        Args:
            symbol: Trading symbol
            limit: Number of trades

        Returns:
            List[Dict]: Recent trades
        """
        try:
            params = {
                "category": "linear",
                "symbol": symbol,
                "limit": min(limit, 1000),  # Bybit max is 1000
            }

            response = await self._make_request("GET", "/v5/market/recent-trade", params=params)

            if response and response.get("retCode") == 0:
                trades = response.get("result", {}).get("list", [])
                formatted_trades = []

                for trade in trades:
                    formatted_trades.append(
                        {
                            "id": trade.get("execId"),
                            "symbol": symbol,
                            "price": safe_float(trade.get("price")),
                            "quantity": safe_float(trade.get("size")),
                            "side": trade.get("side"),
                            "time": safe_int(trade.get("time")),
                            "is_buyer_maker": trade.get("isBlockTrade", False),
                            "raw_response": trade,
                        }
                    )

                return formatted_trades

            return []

        except Exception as e:
            logger.error(f"Error getting recent trades: {e}")
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
        Get kline/candle data from Bybit.

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
            params = {
                "category": "linear",
                "symbol": symbol,
                "interval": self._convert_interval(interval),
                "limit": min(limit, 1000),  # Bybit max is 1000
            }

            if start_time:
                params["start"] = start_time

            if end_time:
                params["end"] = end_time

            response = await self._make_request("GET", "/v5/market/kline", params=params)

            if response and response.get("retCode") == 0:
                klines = response.get("result", {}).get("list", [])
                # Bybit returns newest first, reverse to get chronological order
                klines.reverse()

                formatted_klines = []
                for kline in klines:
                    formatted_klines.append(
                        [
                            int(kline[0]),  # timestamp
                            float(kline[1]),  # open
                            float(kline[2]),  # high
                            float(kline[3]),  # low
                            float(kline[4]),  # close
                            float(kline[5]),  # volume
                            float(kline[6]),  # turnover
                        ]
                    )

                return formatted_klines

            return []

        except Exception as e:
            logger.error(f"Error getting klines: {e}")
            return []

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get symbol trading rules from Bybit.

        Args:
            symbol: Trading symbol

        Returns:
            Dict: Symbol information
        """
        try:
            params = {
                "category": "linear",
                "symbol": symbol,
            }

            response = await self._make_request("GET", "/v5/market/instruments-info", params=params)

            if response and response.get("retCode") == 0:
                instruments = response.get("result", {}).get("list", [])
                if instruments:
                    info = instruments[0]

                    lot_size = info.get("lotSizeFilter", {})
                    price_filter = info.get("priceFilter", {})

                    return {
                        "symbol": info.get("symbol"),
                        "base_asset": info.get("baseCoin"),
                        "quote_asset": info.get("quoteCoin"),
                        "status": info.get("status"),
                        "min_quantity": safe_float(lot_size.get("minOrderQty")),
                        "max_quantity": safe_float(lot_size.get("maxOrderQty")),
                        "max_market_quantity": safe_float(
                            lot_size.get("maxMktOrderQty")
                        ),  # For market orders
                        "quantity_step": safe_float(lot_size.get("qtyStep")),
                        "min_price": safe_float(price_filter.get("minPrice")),
                        "max_price": safe_float(price_filter.get("maxPrice")),
                        "price_tick": safe_float(price_filter.get("tickSize")),
                        "min_notional": safe_float(
                            lot_size.get("minNotionalValue") or lot_size.get("minOrderAmt")
                        ),
                        "max_leverage": safe_float(
                            info.get("leverageFilter", {}).get("maxLeverage"), 1.0
                        ),
                        "contract_size": safe_float(info.get("contractSize"), 1.0),
                        "delivery_time": info.get("deliveryTime"),
                        "launch_time": info.get("launchTime"),
                        "raw_response": info,
                    }

            return {}

        except Exception as e:
            logger.error(f"Error getting symbol info: {e}")
            return {}

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get order history from Bybit.

        Args:
            symbol: Optional symbol filter
            limit: Maximum number of orders

        Returns:
            List[Dict]: Historical orders
        """
        try:
            params = {
                "category": "linear",
                "limit": min(limit, 50),  # Bybit max is 50 per request
            }

            if symbol:
                params["symbol"] = symbol

            response = await self._make_request("GET", "/v5/order/history", params=params)

            if response and response.get("retCode") == 0:
                orders = response.get("result", {}).get("list", [])
                return [self._format_order(order) for order in orders]

            return []

        except Exception as e:
            logger.error(f"Error getting order history: {e}")
            return []

    # ==================== WebSocket Methods ====================

    async def subscribe_orderbook(self, symbol: str, callback: callable) -> bool:
        """Subscribe to order book updates."""
        try:
            self.callbacks["orderbook"][symbol] = callback

            if self.ws_public:
                subscribe_msg = {"op": "subscribe", "args": [f"orderbook.50.{symbol}"]}
                await self.ws_public.send_json(subscribe_msg)
                return True

            return False

        except Exception as e:
            logger.error(f"Error subscribing to orderbook: {e}")
            return False

    async def subscribe_trades(self, symbol: str, callback: callable) -> bool:
        """Subscribe to trade updates."""
        try:
            self.callbacks["trades"][symbol] = callback

            if self.ws_public:
                subscribe_msg = {"op": "subscribe", "args": [f"publicTrade.{symbol}"]}
                await self.ws_public.send_json(subscribe_msg)
                return True

            return False

        except Exception as e:
            logger.error(f"Error subscribing to trades: {e}")
            return False

    async def subscribe_klines(self, symbol: str, interval: str, callback: callable) -> bool:
        """Subscribe to kline updates."""
        try:
            key = f"{symbol}_{interval}"
            self.callbacks["klines"][key] = callback

            if self.ws_public:
                subscribe_msg = {"op": "subscribe", "args": [f"kline.{interval}.{symbol}"]}
                await self.ws_public.send_json(subscribe_msg)
                return True

            return False

        except Exception as e:
            logger.error(f"Error subscribing to klines: {e}")
            return False

    async def subscribe_account(self, callback: callable) -> bool:
        """Subscribe to account updates."""
        try:
            self.callbacks["account"] = callback

            if self.ws_private:
                subscribe_msg = {
                    "op": "subscribe",
                    "args": ["position", "execution", "order", "wallet"],
                }
                await self.ws_private.send_json(subscribe_msg)
                return True

            return False

        except Exception as e:
            logger.error(f"Error subscribing to account updates: {e}")
            return False

    # ==================== Private Methods ====================

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        max_retries: int = 3,
    ) -> Optional[Dict]:
        """Make HTTP request to Bybit API with retry logic."""
        await self._wait_for_rate_limit()

        last_error = None

        for attempt in range(max_retries + 1):
            try:
                url = f"{self.base_url}{endpoint}"

                # Check if session exists
                if not self.session or self.session.closed:
                    self.session = create_robust_session()

                # Add authentication (regenerate for each attempt due to timestamp)
                headers = self._get_auth_headers(method, endpoint, params, data)

                # For GET requests, build query string manually with sorted params
                # to ensure signature matches (aiohttp may reorder params)
                request_params = params
                if method == "GET" and params:
                    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
                    url = f"{url}?{query_string}"
                    request_params = None  # Don't pass params separately

                # Measure request latency
                start_time = time.time()

                async with self.session.request(
                    method, url, params=request_params, json=data, headers=headers
                ) as response:
                    result = await response.json()

                    # Log latency for important endpoints
                    latency_ms = (time.time() - start_time) * 1000
                    if endpoint in [
                        "/v5/order/create",
                        "/v5/order/cancel",
                        "/v5/position/trading-stop",
                    ]:
                        logger.debug(f"⏱️ API latency {endpoint}: {latency_ms:.0f}ms")
                        # Track high latency
                        if latency_ms > 500:
                            logger.warning(f"⚠️ High API latency {endpoint}: {latency_ms:.0f}ms")

                    return result

            except (
                aiohttp.ClientConnectorError,
                aiohttp.ServerDisconnectedError,
                aiohttp.ClientOSError,
                asyncio.TimeoutError,
                OSError,
            ) as e:
                last_error = e
                if attempt < max_retries:
                    delay = min(1.0 * (2**attempt), 30.0)  # Exponential backoff, max 30s
                    logger.warning(
                        f"Network error on {endpoint} (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Network error after {max_retries + 1} attempts on {endpoint}: {e}"
                    )

            except Exception as e:
                logger.error(f"Request error on {endpoint}: {e}")
                return None

        # All retries exhausted
        if last_error:
            logger.error(f"All {max_retries + 1} retries exhausted for {endpoint}: {last_error}")
        else:
            logger.error(
                f"Request to {endpoint} failed with no response after {max_retries + 1} attempts"
            )
        return None

    def _get_auth_headers(
        self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None
    ) -> Dict[str, str]:
        """Generate authentication headers."""
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"

        # Create signature payload
        if method == "GET" and params:
            query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
            param_str = f"{timestamp}{self.api_key}{recv_window}{query_string}"
        elif data:
            param_str = f"{timestamp}{self.api_key}{recv_window}{json.dumps(data)}"
        else:
            param_str = f"{timestamp}{self.api_key}{recv_window}"

        # Generate signature
        signature = hmac.new(
            self.api_secret.encode("utf-8"), param_str.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type": "application/json",
        }

        # Note: X-BAPI-DEMO header is NOT needed when using api-demo.bybit.com
        # It's only needed when using api.bybit.com with demo trading
        # Since we use api-demo.bybit.com for demo mode, no extra header needed

        return headers

    async def _connect_websockets(self) -> None:
        """Establish WebSocket connections."""
        try:
            # Connect to public WebSocket (uses mainnet even for demo - demo doesn't support public streams)
            if not self.ws_public:
                public_url = f"{self.ws_public_url}/v5/public/linear"
                self.ws_public = await self.session.ws_connect(public_url)
                asyncio.create_task(self._handle_ws_messages(self.ws_public, "public"))

            # Connect to private WebSocket (uses demo stream for demo mode)
            if not self.ws_private:
                private_url = f"{self.ws_private_url}/v5/private"
                self.ws_private = await self.session.ws_connect(private_url)
                await self._authenticate_ws(self.ws_private)
                asyncio.create_task(self._handle_ws_messages(self.ws_private, "private"))

            logger.info("WebSocket connections established")

        except Exception as e:
            logger.error(f"Error connecting WebSockets: {e}")

    async def _authenticate_ws(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Authenticate private WebSocket."""
        expires = int((time.time() + 10) * 1000)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            f"GET/realtime{expires}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        auth_msg = {"op": "auth", "args": [self.api_key, expires, signature]}

        await ws.send_json(auth_msg)

    async def _handle_ws_messages(self, ws: aiohttp.ClientWebSocketResponse, ws_type: str) -> None:
        """Handle incoming WebSocket messages."""
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._process_ws_message(data, ws_type)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")

        except Exception as e:
            logger.error(f"Error handling WebSocket messages: {e}")

    async def _process_ws_message(self, data: Dict, ws_type: str) -> None:
        """Process WebSocket message."""
        try:
            topic = data.get("topic", "")

            if "orderbook" in topic:
                symbol = topic.split(".")[-1]
                if symbol in self.callbacks["orderbook"]:
                    await self.callbacks["orderbook"][symbol](data)

            elif "publicTrade" in topic:
                symbol = topic.split(".")[-1]
                if symbol in self.callbacks["trades"]:
                    await self.callbacks["trades"][symbol](data)

            elif "kline" in topic:
                parts = topic.split(".")
                interval = parts[1]
                symbol = parts[2]
                key = f"{symbol}_{interval}"
                if key in self.callbacks["klines"]:
                    await self.callbacks["klines"][key](data)

            elif ws_type == "private" and self.callbacks["account"]:
                await self.callbacks["account"](data)

        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}")

    def _convert_side(self, side: str) -> str:
        """Convert order side to Bybit format."""
        return "Buy" if side.upper() == "BUY" else "Sell"

    def _convert_order_type(self, order_type: str) -> str:
        """Convert order type to Bybit format."""
        mapping = {
            "MARKET": "Market",
            "LIMIT": "Limit",
            "STOP_MARKET": "Market",  # with trigger price
            "STOP_LIMIT": "Limit",  # with trigger price
            "TAKE_PROFIT_MARKET": "Market",  # with trigger price (TP)
            "TAKE_PROFIT_LIMIT": "Limit",  # with trigger price (TP)
        }
        return mapping.get(order_type.upper(), "Market")

    def _convert_order_status(self, status: str) -> str:
        """Convert Bybit order status to standard format."""
        mapping = {
            "Created": "NEW",
            "New": "NEW",
            "Rejected": "REJECTED",
            "PartiallyFilled": "PARTIALLY_FILLED",
            "Filled": "FILLED",
            "Cancelled": "CANCELLED",
            "Untriggered": "PENDING",
            "Triggered": "TRIGGERED",
            "Deactivated": "CANCELLED",
        }
        return mapping.get(status, status)

    def _convert_interval(self, interval: str) -> str:
        """Convert interval to Bybit format."""
        mapping = {
            "1m": "1",
            "3m": "3",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "2h": "120",
            "4h": "240",
            "6h": "360",
            "12h": "720",
            "1d": "D",
            "1w": "W",
            "1M": "M",
        }
        return mapping.get(interval, "1")

    def _get_position_idx(self, side: str, position_mode: str) -> int:
        """Get position index for Bybit."""
        if position_mode == "one_way":
            return 0
        elif position_mode == "hedge":
            return 1 if side.upper() == "BUY" else 2
        return 0

    def _format_order(self, order: Dict) -> Dict[str, Any]:
        """Format Bybit order to standard format."""
        return {
            "order_id": order.get("orderId"),
            "client_order_id": order.get("orderLinkId"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("orderType"),
            "stop_order_type": order.get("stopOrderType", ""),
            "status": self._convert_order_status(order.get("orderStatus")),
            "quantity": safe_float(order.get("qty")),
            "filled_quantity": safe_float(order.get("cumExecQty")),
            "price": safe_float(order.get("price")),
            "average_price": safe_float(order.get("avgPrice")),
            "stop_price": safe_float(order.get("triggerPrice")),
            "time_in_force": order.get("timeInForce"),
            "reduce_only": order.get("reduceOnly", False),
            "close_on_trigger": order.get("closeOnTrigger", False),
            "position_idx": safe_int(order.get("positionIdx")),  # 0=one-way, 1=long, 2=short
            "created_time": safe_int(order.get("createdTime")),
            "updated_time": safe_int(order.get("updatedTime")),
            "raw_response": order,
        }

    async def get_wallet_balance_detailed(self, coin: str = "USDT") -> Dict[str, Any]:
        """
        Get detailed wallet balance for a specific coin.

        Returns balance breakdown:
        - wallet_balance: Total balance (includes uPnL)
        - available_balance: Available for trading (excludes margin & uPnL)
        - unrealized_pnl: Current unrealized P&L
        - cum_realized_pnl: Cumulative realized P&L

        Args:
            coin: Coin symbol (default USDT)

        Returns:
            Dict with balance details
        """
        try:
            account_info = await self.get_account_info()

            if not account_info:
                return {}

            # Find specific coin data
            for coin_data in account_info.get("coins", []):
                if coin_data.get("coin") == coin:
                    return {
                        "coin": coin,
                        "wallet_balance": safe_float(coin_data.get("walletBalance")),
                        "available_balance": safe_float(coin_data.get("availableToWithdraw")),
                        "unrealized_pnl": safe_float(coin_data.get("unrealisedPnl")),
                        "cum_realized_pnl": safe_float(coin_data.get("cumRealisedPnl")),
                        "locked": safe_float(coin_data.get("locked")),
                        "bonus": safe_float(coin_data.get("bonus")),
                        "equity": safe_float(coin_data.get("equity")),
                        "raw_response": coin_data,
                    }

            return {}

        except Exception as e:
            logger.error(f"Error getting detailed balance: {e}")
            return {}

    async def get_closed_pnl(
        self,
        symbol: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get closed P&L records from Bybit.

        Args:
            symbol: Filter by symbol (optional)
            start_time: Start timestamp in ms (optional)
            end_time: End timestamp in ms (optional)
            limit: Max records (default 50, max 100)

        Returns:
            List of closed P&L records
        """
        try:
            params = {
                "category": "linear",
                "limit": min(limit, 100),
            }

            if symbol:
                params["symbol"] = symbol
            if start_time:
                params["startTime"] = start_time
            if end_time:
                params["endTime"] = end_time

            response = await self._make_request("GET", "/v5/position/closed-pnl", params=params)

            if response and response.get("retCode") == 0:
                records = response.get("result", {}).get("list", [])
                return [
                    {
                        "symbol": r.get("symbol"),
                        "order_id": r.get("orderId"),
                        "side": r.get("side"),
                        "qty": safe_float(r.get("qty")),
                        "entry_price": safe_float(r.get("avgEntryPrice")),
                        "exit_price": safe_float(r.get("avgExitPrice")),
                        "closed_pnl": safe_float(r.get("closedPnl")),
                        "fill_count": safe_int(r.get("fillCount")),
                        "leverage": safe_float(r.get("leverage"), 1.0),
                        "created_time": safe_int(r.get("createdTime")),
                        "updated_time": safe_int(r.get("updatedTime")),
                        "raw_response": r,
                    }
                    for r in records
                ]

            return []

        except Exception as e:
            logger.error(f"Error getting closed PnL: {e}")
            return []

    async def get_funding_history(
        self,
        symbol: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get funding fee history from Bybit.

        Args:
            symbol: Filter by symbol (optional)
            start_time: Start timestamp in ms (optional)
            end_time: End timestamp in ms (optional)
            limit: Max records (default 50, max 100)

        Returns:
            List of funding fee records
        """
        try:
            params = {
                "category": "linear",
                "limit": min(limit, 100),
            }

            if symbol:
                params["symbol"] = symbol
            if start_time:
                params["startTime"] = start_time
            if end_time:
                params["endTime"] = end_time

            response = await self._make_request(
                "GET",
                "/v5/account/transaction-log",
                params={**params, "type": "TRANSFER_IN"},  # Funding is in transaction log
            )

            # Try dedicated funding endpoint
            response = await self._make_request(
                "GET", "/v5/position/list", params={"category": "linear", "settleCoin": "USDT"}
            )

            # Get funding from execution list
            params["execType"] = "Funding"
            response = await self._make_request("GET", "/v5/execution/list", params=params)

            if response and response.get("retCode") == 0:
                records = response.get("result", {}).get("list", [])
                return [
                    {
                        "symbol": r.get("symbol"),
                        "side": r.get("side"),
                        "qty": safe_float(r.get("execQty")),
                        "price": safe_float(r.get("execPrice")),
                        "funding_fee": safe_float(r.get("execFee")),
                        "funding_rate": safe_float(r.get("feeRate")),
                        "exec_time": safe_int(r.get("execTime")),
                        "raw_response": r,
                    }
                    for r in records
                ]

            return []

        except Exception as e:
            logger.error(f"Error getting funding history: {e}")
            return []

    async def get_trade_history(
        self,
        symbol: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get trade execution history from Bybit.

        Args:
            symbol: Filter by symbol (optional)
            start_time: Start timestamp in ms (optional)
            end_time: End timestamp in ms (optional)
            limit: Max records (default 50, max 100)

        Returns:
            List of trade execution records
        """
        try:
            params = {
                "category": "linear",
                "limit": min(limit, 100),
            }

            if symbol:
                params["symbol"] = symbol
            if start_time:
                params["startTime"] = start_time
            if end_time:
                params["endTime"] = end_time

            response = await self._make_request("GET", "/v5/execution/list", params=params)

            if response and response.get("retCode") == 0:
                records = response.get("result", {}).get("list", [])
                return [
                    {
                        "symbol": r.get("symbol"),
                        "order_id": r.get("orderId"),
                        "exec_id": r.get("execId"),
                        "side": r.get("side"),
                        "qty": safe_float(r.get("execQty")),
                        "price": safe_float(r.get("execPrice")),
                        "exec_type": r.get("execType"),  # Trade, Funding, etc.
                        "exec_fee": safe_float(r.get("execFee")),
                        "fee_rate": safe_float(r.get("feeRate")),
                        "is_maker": r.get("isMaker", False),
                        "exec_time": safe_int(r.get("execTime")),
                        "closed_pnl": safe_float(r.get("closedPnl")),
                        "raw_response": r,
                    }
                    for r in records
                ]

            return []

        except Exception as e:
            logger.error(f"Error getting trade history: {e}")
            return []

    async def get_closed_trade_details(
        self,
        symbol: str,
        entry_order_id: Optional[str] = None,
        entry_time: Optional[int] = None,
        entry_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Get comprehensive details about a closed trade including PnL, fees, and funding.

        Args:
            symbol: Trading pair symbol
            entry_order_id: Order ID of entry (optional, for matching)
            entry_time: Entry timestamp in ms (to filter funding fees)
            entry_price: Entry price (for matching closed PnL record)

        Returns:
            Dict with:
                - closed_pnl: Realized PnL from exchange (USDT)
                - exit_price: Actual exit price
                - open_fee: Entry commission (USDT)
                - close_fee: Exit commission (USDT)
                - funding_fee: Total funding fees during position (USDT)
                - total_fees: Sum of all fees
                - net_pnl: closed_pnl - total_fees
                - fee_details: List of individual fee records
        """
        import time

        result = {
            "closed_pnl": None,
            "exit_price": None,
            "open_fee": 0.0,
            "close_fee": 0.0,
            "funding_fee": 0.0,
            "total_fees": 0.0,
            "net_pnl": None,
            "fee_details": [],
            "source": "exchange",
        }

        try:
            end_time = int(time.time() * 1000)
            # If entry_time provided, search from entry; otherwise last 24 hours
            start_time = entry_time if entry_time else (end_time - 24 * 60 * 60 * 1000)

            # 1. Get closed PnL record
            closed_pnl_records = await self.get_closed_pnl(
                symbol=symbol, start_time=start_time, limit=20
            )

            # Find matching record
            matched_pnl_record = None
            for record in closed_pnl_records:
                # Match by entry price (within 0.5% tolerance)
                if entry_price:
                    price_diff = abs(record["entry_price"] - entry_price)
                    if entry_price > 0 and price_diff / entry_price < 0.005:
                        matched_pnl_record = record
                        break
                # Or match by order ID
                elif entry_order_id and record.get("order_id") == entry_order_id:
                    matched_pnl_record = record
                    break
                # Or just take the most recent one for this symbol
                elif not entry_price and not entry_order_id:
                    matched_pnl_record = record
                    break

            if matched_pnl_record:
                result["closed_pnl"] = matched_pnl_record["closed_pnl"]
                result["exit_price"] = matched_pnl_record["exit_price"]
                logger.debug(
                    f"Found closed PnL record for {symbol}: "
                    f"PnL={matched_pnl_record['closed_pnl']:.4f} USDT"
                )

            # 2. Get trade executions (for open/close fees)
            trade_records = await self.get_trade_history(
                symbol=symbol, start_time=start_time, limit=50
            )

            # Separate entry and exit trades
            entry_fees = 0.0
            exit_fees = 0.0

            for trade in trade_records:
                if trade.get("exec_type") == "Trade":
                    fee = abs(trade.get("exec_fee", 0))
                    exec_time = trade.get("exec_time", 0)

                    # Determine if this is entry or exit based on time
                    # Trades close to entry_time are entry fees, later ones are exit fees
                    if (
                        entry_time and abs(exec_time - entry_time) < 60000
                    ):  # Within 1 minute of entry
                        entry_fees += fee
                    else:
                        exit_fees += fee

                    result["fee_details"].append(
                        {
                            "type": "trade",
                            "fee": fee,
                            "time": exec_time,
                            "price": trade.get("price"),
                            "is_maker": trade.get("is_maker", False),
                        }
                    )

            result["open_fee"] = entry_fees
            result["close_fee"] = exit_fees

            # 3. Get funding fee history
            funding_records = await self.get_funding_history(
                symbol=symbol, start_time=start_time, limit=50
            )

            total_funding = 0.0
            for funding in funding_records:
                fee = funding.get("funding_fee", 0)
                total_funding += fee  # Can be positive or negative
                result["fee_details"].append(
                    {
                        "type": "funding",
                        "fee": fee,
                        "time": funding.get("exec_time"),
                        "rate": funding.get("funding_rate"),
                    }
                )

            result["funding_fee"] = total_funding

            # 4. Calculate totals
            result["total_fees"] = (
                result["open_fee"] + result["close_fee"] + abs(result["funding_fee"])
            )

            if result["closed_pnl"] is not None:
                # Net PnL = exchange reported PnL (already includes fees)
                # Note: Bybit's closedPnl already accounts for trading fees
                # but we track them separately for transparency
                result["net_pnl"] = result["closed_pnl"]

            pnl_str = f"{result['closed_pnl']:.4f}" if result["closed_pnl"] is not None else "N/A"
            logger.info(
                f"📊 Trade details for {symbol}: "
                f"PnL={pnl_str} USDT, "
                f"Open fee={result['open_fee']:.4f}, "
                f"Close fee={result['close_fee']:.4f}, "
                f"Funding={result['funding_fee']:.4f}"
            )

            return result

        except Exception as e:
            logger.error(f"Error getting closed trade details for {symbol}: {e}")
            return result
