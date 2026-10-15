"""

    -*- coding: utf-8 -*-
    Created on 06.02.2026.
    File        : connector_router
    Project     : Project-103
    Description : Multi-connector router. Routes orders to appropriate connector based on symbol/strategy.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from typing import Any, Dict, List, Optional, Set

from src.execution.connectors.base_connector import BaseConnector

logger = logging.getLogger(__name__)


class ConnectorRouter(BaseConnector):
    """
    Routes trading operations to appropriate connector based on symbol.

    Allows running multiple connectors simultaneously (e.g., Bybit for crypto + MT5 for gold/forex).
    Implements BaseConnector interface so it can be used as drop-in replacement.
    """

    def __init__(
        self,
        default_connector: Optional[BaseConnector] = None,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
    ):
        """
        Initialize connector router.

        Args:
            default_connector: Default connector for unmapped symbols
            api_key: Not used directly (each connector has its own)
            api_secret: Not used directly
            testnet: Whether to use testnet/demo mode
        """
        super().__init__(api_key, api_secret, testnet)

        self.default_connector = default_connector
        self.connectors: Dict[str, BaseConnector] = {}  # name -> connector
        self.symbol_mapping: Dict[str, str] = {}  # symbol -> connector_name
        self.prefix_mapping: Dict[str, str] = {}  # symbol_prefix -> connector_name

        if default_connector:
            self.connectors["default"] = default_connector

        logger.info("ConnectorRouter initialized")

    def add_connector(
        self,
        name: str,
        connector: BaseConnector,
        symbols: Optional[List[str]] = None,
        prefixes: Optional[List[str]] = None,
        is_default: bool = False,
    ) -> None:
        """
        Add a connector with optional symbol/prefix mapping.

        Args:
            name: Connector name (e.g., "bybit", "mt5")
            connector: Connector instance
            symbols: List of symbols to route to this connector
            prefixes: List of symbol prefixes (e.g., ["XAU", "XAG"] for gold/silver)
            is_default: Set as default connector
        """
        self.connectors[name] = connector

        if symbols:
            for symbol in symbols:
                self.symbol_mapping[symbol] = name

        if prefixes:
            for prefix in prefixes:
                self.prefix_mapping[prefix] = name

        if is_default:
            self.default_connector = connector
            self.connectors["default"] = connector

        logger.info(
            f"Added connector '{name}': symbols={symbols or []}, "
            f"prefixes={prefixes or []}, default={is_default}"
        )

    def get_connector(self, symbol: str) -> Optional[BaseConnector]:
        """
        Get appropriate connector for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Connector for the symbol or default connector
        """
        # Check exact symbol mapping first
        if symbol in self.symbol_mapping:
            connector_name = self.symbol_mapping[symbol]
            return self.connectors.get(connector_name)

        # Check prefix mapping
        for prefix, connector_name in self.prefix_mapping.items():
            if symbol.startswith(prefix):
                return self.connectors.get(connector_name)

        # Fall back to default
        return self.default_connector

    def get_connector_name(self, symbol: str) -> str:
        """Get connector name for a symbol."""
        if symbol in self.symbol_mapping:
            return self.symbol_mapping[symbol]

        for prefix, connector_name in self.prefix_mapping.items():
            if symbol.startswith(prefix):
                return connector_name

        return "default"

    def get_all_symbols(self) -> Set[str]:
        """Get all explicitly mapped symbols."""
        return set(self.symbol_mapping.keys())

    # ==================== BaseConnector Interface ====================

    async def connect(self) -> bool:
        """Connect all registered connectors."""
        success = True

        for name, connector in self.connectors.items():
            if name == "default" and connector in self.connectors.values():
                continue  # Skip duplicate default

            try:
                if await connector.connect():
                    logger.info(f"Connector '{name}' connected")
                else:
                    logger.error(f"Connector '{name}' failed to connect")
                    success = False
            except Exception as e:
                logger.error(f"Error connecting '{name}': {e}")
                success = False

        self.is_connected = success
        return success

    async def disconnect(self) -> bool:
        """Disconnect all registered connectors."""
        success = True

        for name, connector in self.connectors.items():
            if name == "default":
                continue

            try:
                if await connector.disconnect():
                    logger.info(f"Connector '{name}' disconnected")
                else:
                    logger.warning(f"Connector '{name}' disconnect returned False")
            except Exception as e:
                logger.error(f"Error disconnecting '{name}': {e}")
                success = False

        self.is_connected = False
        return success

    async def place_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """Route order to appropriate connector."""
        symbol = order_params.get("symbol", "")
        connector = self.get_connector(symbol)

        if not connector:
            return {"success": False, "error": f"No connector found for symbol {symbol}"}

        return await connector.place_order(order_params)

    async def cancel_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """Route cancel to appropriate connector."""
        symbol = order_params.get("symbol", "")
        connector = self.get_connector(symbol)

        if not connector:
            return {"success": False, "error": f"No connector found for symbol {symbol}"}

        return await connector.cancel_order(order_params)

    async def modify_order(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """Route modify to appropriate connector."""
        symbol = order_params.get("symbol", "")
        connector = self.get_connector(symbol)

        if not connector:
            return {"success": False, "error": f"No connector found for symbol {symbol}"}

        return await connector.modify_order(order_params)

    async def get_order_status(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Route order status to appropriate connector."""
        connector = self.get_connector(symbol)

        if not connector:
            return {}

        return await connector.get_order_status(order_id, symbol)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open orders from all connectors or specific one."""
        if symbol:
            connector = self.get_connector(symbol)
            if connector:
                return await connector.get_open_orders(symbol)
            return []

        # Aggregate from all connectors
        all_orders = []
        seen_connectors = set()

        for name, connector in self.connectors.items():
            if id(connector) in seen_connectors:
                continue
            seen_connectors.add(id(connector))

            try:
                orders = await connector.get_open_orders()
                all_orders.extend(orders)
            except Exception as e:
                logger.error(f"Error getting orders from '{name}': {e}")

        return all_orders

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get positions from all connectors or specific one."""
        if symbol:
            connector = self.get_connector(symbol)
            if connector:
                return await connector.get_positions(symbol)
            return []

        # Aggregate from all connectors
        all_positions = []
        seen_connectors = set()

        for name, connector in self.connectors.items():
            if id(connector) in seen_connectors:
                continue
            seen_connectors.add(id(connector))

            try:
                positions = await connector.get_positions()
                # Add connector source for debugging
                for pos in positions:
                    pos["_connector"] = name
                all_positions.extend(positions)
            except Exception as e:
                logger.error(f"Error getting positions from '{name}': {e}")

        return all_positions

    async def get_account_info(self) -> Dict[str, Any]:
        """Get account info from default connector."""
        if self.default_connector:
            return await self.default_connector.get_account_info()
        return {}

    async def get_balance(self) -> Dict[str, float]:
        """Aggregate balances from all connectors."""
        balances = {}
        seen_connectors = set()

        for name, connector in self.connectors.items():
            if id(connector) in seen_connectors:
                continue
            seen_connectors.add(id(connector))

            try:
                connector_balances = await connector.get_balance()
                # Prefix with connector name to avoid collisions
                for asset, amount in connector_balances.items():
                    key = f"{name}_{asset}" if name != "default" else asset
                    balances[key] = amount
            except Exception as e:
                logger.error(f"Error getting balance from '{name}': {e}")

        return balances

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Route ticker to appropriate connector."""
        connector = self.get_connector(symbol)

        if connector:
            return await connector.get_ticker(symbol)
        return {}

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Route orderbook to appropriate connector."""
        connector = self.get_connector(symbol)

        if connector:
            return await connector.get_orderbook(symbol, limit)
        return {}

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Route recent trades to appropriate connector."""
        connector = self.get_connector(symbol)

        if connector:
            return await connector.get_recent_trades(symbol, limit)
        return []

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[List]:
        """Route klines to appropriate connector."""
        connector = self.get_connector(symbol)

        if connector:
            return await connector.get_klines(symbol, interval, limit, start_time, end_time)
        return []

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Route symbol info to appropriate connector."""
        connector = self.get_connector(symbol)

        if connector:
            return await connector.get_symbol_info(symbol)
        return {}

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get order history from appropriate connector(s)."""
        if symbol:
            connector = self.get_connector(symbol)
            if connector:
                return await connector.get_order_history(symbol, limit)
            return []

        # Aggregate from all
        all_history = []
        seen_connectors = set()

        for name, connector in self.connectors.items():
            if id(connector) in seen_connectors:
                continue
            seen_connectors.add(id(connector))

            try:
                history = await connector.get_order_history(limit=limit)
                all_history.extend(history)
            except Exception as e:
                logger.error(f"Error getting order history from '{name}': {e}")

        return all_history

    # WebSocket subscriptions - route to appropriate connector
    async def subscribe_orderbook(self, symbol: str, callback: callable) -> bool:
        """Subscribe to orderbook on appropriate connector."""
        connector = self.get_connector(symbol)
        if connector and hasattr(connector, "subscribe_orderbook"):
            return await connector.subscribe_orderbook(symbol, callback)
        return False

    async def subscribe_trades(self, symbol: str, callback: callable) -> bool:
        """Subscribe to trades on appropriate connector."""
        connector = self.get_connector(symbol)
        if connector and hasattr(connector, "subscribe_trades"):
            return await connector.subscribe_trades(symbol, callback)
        return False

    async def subscribe_klines(self, symbol: str, interval: str, callback: callable) -> bool:
        """Subscribe to klines on appropriate connector."""
        connector = self.get_connector(symbol)
        if connector and hasattr(connector, "subscribe_klines"):
            return await connector.subscribe_klines(symbol, interval, callback)
        return False

    async def subscribe_account(self, callback: callable) -> bool:
        """Subscribe to account updates on default connector."""
        if self.default_connector and hasattr(self.default_connector, "subscribe_account"):
            return await self.default_connector.subscribe_account(callback)
        return False

    # ==================== Bybit-specific methods (delegate to bybit connector) ====================

    async def set_trading_stop(self, *args, **kwargs):
        """Delegate to Bybit connector if available."""
        bybit = self.connectors.get("bybit") or self.default_connector
        if bybit and hasattr(bybit, "set_trading_stop"):
            return await bybit.set_trading_stop(*args, **kwargs)
        return {"success": False, "error": "set_trading_stop not available"}

    async def get_closed_trade_details(self, symbol: str, *args, **kwargs):
        """Delegate to appropriate connector."""
        connector = self.get_connector(symbol)
        if connector and hasattr(connector, "get_closed_trade_details"):
            return await connector.get_closed_trade_details(symbol=symbol, *args, **kwargs)
        return {}

    async def cancel_all_orders(self, symbol: str, *args, **kwargs):
        """Cancel all orders on appropriate connector."""
        connector = self.get_connector(symbol)
        if connector and hasattr(connector, "cancel_all_orders"):
            return await connector.cancel_all_orders(symbol, *args, **kwargs)
        return {"success": False, "error": "cancel_all_orders not available"}
