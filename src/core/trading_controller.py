"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 03.12.2025.
    File        : trading_controller
    Project     : Project-103
    Description : Central trading controller for runtime management.
                  Provides controls for trading, strategies, and configuration without restart.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from src.monitoring.alerts.alert_manager import TelegramAlert
from src.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)


class TradingState(Enum):
    """Trading state enumeration"""

    ENABLED = "enabled"  # Normal trading
    DISABLED = "disabled"  # Trading disabled, data recording continues
    PAUSED = "paused"  # Temporarily paused (e.g., during config reload)
    EMERGENCY_STOP = "emergency"  # Emergency stop - close all positions


class ControllerEvent(Enum):
    """Controller events for callbacks"""

    TRADING_ENABLED = "trading_enabled"
    TRADING_DISABLED = "trading_disabled"
    TRADING_PAUSED = "trading_paused"
    EMERGENCY_STOP = "emergency_stop"
    STRATEGY_ENABLED = "strategy_enabled"
    STRATEGY_DISABLED = "strategy_disabled"
    CONFIG_RELOADED = "config_reloaded"
    PAIRS_UPDATED = "pairs_updated"


@dataclass
class ControllerStatus:
    """Current controller status"""

    trading_state: TradingState = TradingState.ENABLED
    active_strategies: List[str] = field(default_factory=list)
    active_pairs: List[str] = field(default_factory=list)
    exchange: str = "bybit"
    mode: str = "paper"

    # Timestamps
    started_at: Optional[datetime] = None
    trading_enabled_at: Optional[datetime] = None
    last_config_reload: Optional[datetime] = None

    # Stats
    total_signals_generated: int = 0
    total_trades_executed: int = 0
    emergency_stops: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "trading_state": self.trading_state.value,
            "active_strategies": self.active_strategies,
            "active_pairs": self.active_pairs,
            "exchange": self.exchange,
            "mode": self.mode,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "trading_enabled_at": self.trading_enabled_at.isoformat()
            if self.trading_enabled_at
            else None,
            "last_config_reload": self.last_config_reload.isoformat()
            if self.last_config_reload
            else None,
            "total_signals_generated": self.total_signals_generated,
            "total_trades_executed": self.total_trades_executed,
            "emergency_stops": self.emergency_stops,
            "is_trading_allowed": self.trading_state == TradingState.ENABLED,
        }


class TradingController:
    """
    Central controller for runtime trading management.

    Features:
    - Enable/disable trading globally
    - Enable/disable individual strategies
    - Add/remove trading pairs
    - Hot reload configuration
    - Emergency stop
    - Event callbacks for components
    """

    def __init__(self, config_dir: str = "config/"):
        """
        Initialize trading controller.

        Args:
            config_dir: Path to configuration directory
        """
        self.config_dir = Path(config_dir)
        self.status = ControllerStatus()

        # Event callbacks
        self._callbacks: Dict[ControllerEvent, List[Callable]] = {
            event: [] for event in ControllerEvent
        }

        # References to managed components (set by TradingBot)
        self.strategy_manager = None
        self.data_provider = None
        self.signal_lifecycle = None
        self.alert_manager = None

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        logger.info("TradingController initialized")

    # ==================== Trading Control ====================

    async def enable_trading(self) -> Dict[str, Any]:
        """
        Enable trading.

        Returns:
            Status dict with result
        """
        async with self._lock:
            if self.status.trading_state == TradingState.ENABLED:
                return {"success": True, "message": "Trading already enabled"}

            previous_state = self.status.trading_state
            self.status.trading_state = TradingState.ENABLED
            self.status.trading_enabled_at = utcnow()

            logger.info("🟢 Trading ENABLED")

            await self._emit_event(
                ControllerEvent.TRADING_ENABLED,
                {"previous_state": previous_state.value, "timestamp": utcnow().isoformat()},
            )

            return {
                "success": True,
                "message": "Trading enabled",
                "state": self.status.trading_state.value,
            }

    async def disable_trading(self, reason: str = "Manual disable") -> Dict[str, Any]:
        """
        Disable trading (data recording continues).

        Args:
            reason: Reason for disabling

        Returns:
            Status dict with result
        """
        async with self._lock:
            if self.status.trading_state == TradingState.DISABLED:
                return {"success": True, "message": "Trading already disabled"}

            previous_state = self.status.trading_state
            self.status.trading_state = TradingState.DISABLED

            logger.info(f"🔴 Trading DISABLED: {reason}")

            await self._emit_event(
                ControllerEvent.TRADING_DISABLED,
                {
                    "previous_state": previous_state.value,
                    "reason": reason,
                    "timestamp": utcnow().isoformat(),
                },
            )

            return {
                "success": True,
                "message": f"Trading disabled: {reason}",
                "state": self.status.trading_state.value,
            }

    async def pause_trading(self, reason: str = "Paused") -> Dict[str, Any]:
        """
        Temporarily pause trading.

        Args:
            reason: Reason for pausing

        Returns:
            Status dict
        """
        async with self._lock:
            previous_state = self.status.trading_state
            self.status.trading_state = TradingState.PAUSED

            logger.info(f"⏸️ Trading PAUSED: {reason}")

            await self._emit_event(
                ControllerEvent.TRADING_PAUSED,
                {"previous_state": previous_state.value, "reason": reason},
            )

            return {
                "success": True,
                "message": f"Trading paused: {reason}",
                "state": self.status.trading_state.value,
            }

    async def emergency_stop(self, reason: str = "Emergency stop triggered") -> Dict[str, Any]:
        """
        Emergency stop - disable trading and optionally close all positions.

        Args:
            reason: Reason for emergency stop

        Returns:
            Status dict with actions taken
        """
        async with self._lock:
            self.status.trading_state = TradingState.EMERGENCY_STOP
            self.status.emergency_stops += 1

            logger.critical(f"🚨 EMERGENCY STOP: {reason}")

            actions_taken = []

            # Close all open positions.
            # This used to be a STUB: an empty try that just appended
            # "Requested position closure" to the report. It went out as
            # "EMERGENCY STOP EXECUTED" although nothing was closed.
            if self.signal_lifecycle:
                try:
                    result = await self.signal_lifecycle.close_all_positions(reason)
                    n_ok = len(result.get("closed", []))
                    n_bad = len(result.get("failed", []))
                    if result.get("requested", 0) == 0:
                        actions_taken.append("no open positions")
                    else:
                        actions_taken.append(f"positions closed: {n_ok}")
                    if n_bad:
                        # This must be seen first: some positions remain on the
                        # account and need manual intervention.
                        actions_taken.append(
                            f"⚠️ NOT CLOSED {n_bad} — close manually: "
                            + ", ".join(f.get("symbol", "?") for f in result.get("failed", []))
                        )
                except Exception as e:
                    logger.error(f"Error closing positions: {e}", exc_info=True)
                    actions_taken.append(f"⚠️ position close failed: {e}")
            else:
                actions_taken.append("⚠️ lifecycle not connected — positions NOT closed")

            await self._emit_event(
                ControllerEvent.EMERGENCY_STOP,
                {"reason": reason, "actions": actions_taken, "timestamp": utcnow().isoformat()},
            )

            await self._send_alert(
                "🚨 EMERGENCY STOP",
                f"Emergency stop triggered!\nReason: {reason}\nActions: {', '.join(actions_taken)}",
                priority="critical",
            )

            return {
                "success": True,
                "message": f"Emergency stop executed: {reason}",
                "actions_taken": actions_taken,
                "state": self.status.trading_state.value,
            }

    def is_trading_allowed(self) -> bool:
        """Check if trading is currently allowed."""
        return self.status.trading_state == TradingState.ENABLED

    # ==================== Strategy Control ====================

    async def enable_strategy(self, strategy_name: str) -> Dict[str, Any]:
        """
        Enable a specific strategy.

        Args:
            strategy_name: Name of the strategy

        Returns:
            Status dict
        """
        if not self.strategy_manager:
            return {"success": False, "error": "Strategy manager not initialized"}

        try:
            self.strategy_manager.activate_strategy(strategy_name)

            if strategy_name not in self.status.active_strategies:
                self.status.active_strategies.append(strategy_name)

            logger.info(f"✅ Strategy enabled: {strategy_name}")

            await self._emit_event(ControllerEvent.STRATEGY_ENABLED, {"strategy": strategy_name})

            return {
                "success": True,
                "message": f"Strategy '{strategy_name}' enabled",
                "active_strategies": self.status.active_strategies,
            }

        except Exception as e:
            logger.error(f"Error enabling strategy {strategy_name}: {e}")
            return {"success": False, "error": str(e)}

    async def disable_strategy(self, strategy_name: str) -> Dict[str, Any]:
        """
        Disable a specific strategy.

        Args:
            strategy_name: Name of the strategy

        Returns:
            Status dict
        """
        if not self.strategy_manager:
            return {"success": False, "error": "Strategy manager not initialized"}

        try:
            self.strategy_manager.deactivate_strategy(strategy_name)

            if strategy_name in self.status.active_strategies:
                self.status.active_strategies.remove(strategy_name)

            logger.info(f"⛔ Strategy disabled: {strategy_name}")

            await self._emit_event(ControllerEvent.STRATEGY_DISABLED, {"strategy": strategy_name})

            return {
                "success": True,
                "message": f"Strategy '{strategy_name}' disabled",
                "active_strategies": self.status.active_strategies,
            }

        except Exception as e:
            logger.error(f"Error disabling strategy {strategy_name}: {e}")
            return {"success": False, "error": str(e)}

    def list_strategies(self) -> Dict[str, Any]:
        """
        List all strategies and their status.

        Returns:
            Dict with strategy information
        """
        if not self.strategy_manager:
            return {"strategies": [], "error": "Strategy manager not initialized"}

        all_strategies = self.strategy_manager.list_strategies(active_only=False)
        active_strategies = self.strategy_manager.list_strategies(active_only=True)

        strategies_info = []
        for name in all_strategies:
            metrics = self.strategy_manager.get_metrics(name).get(name)

            strategies_info.append(
                {
                    "name": name,
                    "enabled": name in active_strategies,
                    "total_signals": metrics.total_signals if metrics else 0,
                    "win_rate": metrics.win_rate if metrics else 0,
                    "total_pnl": metrics.total_pnl if metrics else 0,
                }
            )

        return {
            "strategies": strategies_info,
            "total": len(all_strategies),
            "active": len(active_strategies),
        }

    # ==================== Pairs Control ====================

    async def add_pair(self, symbol: str) -> Dict[str, Any]:
        """
        Add a trading pair.

        Args:
            symbol: Trading pair symbol (e.g., BTCUSDT)

        Returns:
            Status dict
        """
        if symbol in self.status.active_pairs:
            return {"success": True, "message": f"Pair {symbol} already active"}

        # Subscribe to data streams if data provider is available
        if self.data_provider:
            try:
                from src.data.providers.base_provider import DataSubscription

                # Subscribe to candles
                for timeframe in ["1m", "15m"]:
                    sub = DataSubscription(
                        symbol=symbol, data_type="candle", params={"timeframe": timeframe}
                    )
                    await self.data_provider.subscribe(sub)

                # Subscribe to orderbook
                ob_sub = DataSubscription(
                    symbol=symbol, data_type="orderbook", params={"depth": 50}
                )
                await self.data_provider.subscribe(ob_sub)

                # Subscribe to ticker
                ticker_sub = DataSubscription(symbol=symbol, data_type="ticker")
                await self.data_provider.subscribe(ticker_sub)

            except Exception as e:
                logger.error(f"Error subscribing to {symbol}: {e}")
                return {"success": False, "error": str(e)}

        self.status.active_pairs.append(symbol)

        logger.info(f"➕ Added pair: {symbol}")

        await self._emit_event(
            ControllerEvent.PAIRS_UPDATED,
            {"action": "add", "symbol": symbol, "active_pairs": self.status.active_pairs},
        )

        return {
            "success": True,
            "message": f"Pair {symbol} added",
            "active_pairs": self.status.active_pairs,
        }

    async def remove_pair(self, symbol: str) -> Dict[str, Any]:
        """
        Remove a trading pair.

        Args:
            symbol: Trading pair symbol

        Returns:
            Status dict
        """
        if symbol not in self.status.active_pairs:
            return {"success": False, "error": f"Pair {symbol} not active"}

        # Unsubscribe from data streams if data provider is available
        if self.data_provider:
            try:
                from src.data.providers.base_provider import DataSubscription

                # Unsubscribe from all data types
                for data_type in ["candle", "orderbook", "ticker"]:
                    sub = DataSubscription(symbol=symbol, data_type=data_type)
                    await self.data_provider.unsubscribe(sub)

            except Exception as e:
                logger.warning(f"Error unsubscribing from {symbol}: {e}")

        self.status.active_pairs.remove(symbol)

        logger.info(f"➖ Removed pair: {symbol}")

        await self._emit_event(
            ControllerEvent.PAIRS_UPDATED,
            {"action": "remove", "symbol": symbol, "active_pairs": self.status.active_pairs},
        )

        return {
            "success": True,
            "message": f"Pair {symbol} removed",
            "active_pairs": self.status.active_pairs,
        }

    # ==================== Configuration ====================

    async def reload_config(self) -> Dict[str, Any]:
        """
        Reload configuration from files.

        Returns:
            Status dict with changes applied
        """
        async with self._lock:
            # Pause trading during reload
            was_enabled = self.status.trading_state == TradingState.ENABLED
            if was_enabled:
                await self.pause_trading("Configuration reload")

            try:
                changes = []

                # Reload main config
                config_path = self.config_dir / "config.yaml"
                if config_path.exists():
                    with open(config_path, "r", encoding="utf-8") as f:
                        new_config = yaml.safe_load(f)

                    # Update active pairs if changed
                    new_pairs = new_config.get("active_pairs", [])
                    if set(new_pairs) != set(self.status.active_pairs):
                        changes.append(f"Pairs updated: {new_pairs}")
                        # Note: Full implementation would update subscriptions

                    # Update active strategies if changed
                    new_strategies = new_config.get("active_strategies", [])
                    if set(new_strategies) != set(self.status.active_strategies):
                        changes.append(f"Strategies updated: {new_strategies}")

                self.status.last_config_reload = utcnow()

                logger.info(f"🔄 Configuration reloaded. Changes: {changes}")

                await self._emit_event(
                    ControllerEvent.CONFIG_RELOADED,
                    {"changes": changes, "timestamp": utcnow().isoformat()},
                )

                # Resume trading if it was enabled
                if was_enabled:
                    await self.enable_trading()

                return {"success": True, "message": "Configuration reloaded", "changes": changes}

            except Exception as e:
                logger.error(f"Error reloading config: {e}")

                # Resume trading if it was enabled
                if was_enabled:
                    await self.enable_trading()

                return {"success": False, "error": str(e)}

    def get_config_summary(self) -> Dict[str, Any]:
        """
        Get current configuration summary.

        Returns:
            Config summary dict
        """
        try:
            config_path = self.config_dir / "config.yaml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)

                return {
                    "mode": config.get("mode", "unknown"),
                    "active_pairs": config.get("active_pairs", []),
                    "active_strategies": config.get("active_strategies", []),
                    "data_recording": config.get("data_recording", {}).get("enabled", False),
                    "risk": {
                        "max_risk_per_trade": config.get("risk", {}).get("max_risk_per_trade", 0),
                        "max_positions": config.get("risk", {}).get("max_positions", 0),
                    },
                }
        except Exception as e:
            logger.error(f"Error reading config: {e}")

        return {"error": "Could not read configuration"}

    # ==================== Status ====================

    def get_status(self) -> Dict[str, Any]:
        """
        Get full controller status.

        Returns:
            Status dict
        """
        status_dict = self.status.to_dict()

        # Add strategy details if available
        if self.strategy_manager:
            status_dict["strategies"] = self.list_strategies()

        # Add config summary
        status_dict["config"] = self.get_config_summary()

        return status_dict

    # ==================== Events ====================

    def on_event(self, event: ControllerEvent, callback: Callable) -> None:
        """
        Register callback for controller event.

        Args:
            event: Event type
            callback: Async callback function
        """
        self._callbacks[event].append(callback)

    async def _emit_event(self, event: ControllerEvent, data: Dict[str, Any]) -> None:
        """
        Emit event to all registered callbacks.

        Args:
            event: Event type
            data: Event data
        """
        for callback in self._callbacks[event]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event, data)
                else:
                    callback(event, data)
            except Exception as e:
                logger.error(f"Error in event callback for {event}: {e}")

    async def _send_alert(self, title: str, message: str, priority: str = "medium") -> None:
        """Send alert via alert manager if available."""
        if self.alert_manager:
            try:
                alert = TelegramAlert(
                    type="control", priority=priority, title=title, message=message
                )
                await self.alert_manager.send_alert(alert)
            except Exception as e:
                logger.error(f"Error sending alert: {e}")

    # ==================== Component Registration ====================

    def register_components(
        self, strategy_manager=None, data_provider=None, signal_lifecycle=None, alert_manager=None
    ) -> None:
        """
        Register bot components for control.

        Args:
            strategy_manager: StrategyManager instance
            data_provider: DataProvider instance
            signal_lifecycle: SignalLifecycleManager instance
            alert_manager: AlertManager instance
        """
        if strategy_manager:
            self.strategy_manager = strategy_manager
            self.status.active_strategies = strategy_manager.list_strategies(active_only=True)

        if data_provider:
            self.data_provider = data_provider

        if signal_lifecycle:
            self.signal_lifecycle = signal_lifecycle

        if alert_manager:
            self.alert_manager = alert_manager

        logger.info("Components registered with TradingController")
