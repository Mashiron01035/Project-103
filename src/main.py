"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : main
    Project     : Project-103
    Description : Main entry point for the Trading Bot. Supports multiple modes: production, paper trading, backtest, and interactive console.

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.bot.initialization import InitializationMixin
from src.bot.loops import LoopsMixin
from src.bot.resilience import ResilienceMixin

# TradingBot behaviour mixins (see src/bot/)
from src.bot.signals import SignalsMixin

# Core imports. The init/loops/signals/resilience logic now lives in src/bot/
# mixins; main keeps only what it uses directly plus the classes referenced in
# TradingBot.__init__ type hints.
from src.core.config import BotConfig
from src.core.trading_controller import TradingController

# Data imports (type hints in __init__)
from src.data.storage.database import DatabaseManager
from src.data.storage.redis_cache import RedisCache

# Execution imports
from src.execution.signal_lifecycle_manager import SignalLifecycleManager
from src.execution.signal_router import SignalRouter

# Models
from src.models.position import PositionManager

# Monitoring imports
from src.monitoring import HealthChecker, PerformanceTracker, TradingLogger
from src.monitoring.alerts import AlertManager, TelegramControlBot
from src.monitoring.dashboard import TradingDashboard

# Strategies
from src.strategies import StrategyManager

# Create logs directory early
Path("logs").mkdir(exist_ok=True)

# Initialize TradingLogger FIRST (configures root logger with all handlers)
# This must happen before any logging.getLogger() calls
_trading_logger = TradingLogger(log_level="INFO")

# Now get module logger - it will use TradingLogger's handlers via root
logger = logging.getLogger(__name__)


@dataclass
class BotState:
    """Bot runtime state"""

    is_running: bool = False
    is_initialized: bool = False
    start_time: Optional[float] = None
    mode: str = "paper"  # paper, demo, testnet, production, backtest
    exchange: str = "bybit"  # bybit, mt5, mock


@dataclass
class CLIArguments:
    """Command-line arguments"""

    mode: str = "paper"  # paper, demo, testnet, production, backtest
    exchange: str = "bybit"
    config_dir: str = "config/"
    no_dashboard: bool = False
    no_telegram: bool = False
    log_level: str = "INFO"
    console: bool = False
    backtest_start: Optional[str] = None
    backtest_end: Optional[str] = None
    demo: bool = False  # deprecated
    testnet: bool = False  # deprecated


class TradingBot(SignalsMixin, ResilienceMixin, LoopsMixin, InitializationMixin):
    """Main Trading Bot orchestrator"""

    def __init__(self, args: CLIArguments):
        """
        Initialize Trading Bot

        Args:
            args: Command-line arguments
        """
        self.args = args
        self.state = BotState(mode=args.mode, exchange=args.exchange)

        # Core components
        self.config: Optional[BotConfig] = None
        self.logger: Optional[TradingLogger] = None

        # Data components
        self.data_provider = None
        self.database: Optional[DatabaseManager] = None
        self.cache: Optional[RedisCache] = None

        # Execution components
        self.connector = None
        self.position_manager: Optional[PositionManager] = None

        # Monitoring components
        self.performance_tracker: Optional[PerformanceTracker] = None
        self.health_checker: Optional[HealthChecker] = None
        self.alert_manager: Optional[AlertManager] = None
        self.dashboard: Optional[TradingDashboard] = None
        self.telegram_bot: Optional[TelegramControlBot] = None

        # Strategy components
        self.strategy_manager: Optional[StrategyManager] = None

        # Signal lifecycle manager
        self.signal_lifecycle: Optional[SignalLifecycleManager] = None

        # Confluence strategy pairs (dynamically loaded)
        self.confluence_pairs: list = []
        self._confluence_config: Dict[str, Any] = {}

        # Strategy pairs (collected from strategies.yaml for all active strategies)
        self.strategy_pairs: list = []

        # Trading controller for runtime management
        self.trading_controller: Optional[TradingController] = None

        # Async tasks
        self.tasks: list = []

        # Shutdown event
        self.shutdown_event = asyncio.Event()

        # Signal router (multi-connector routing)
        self.signal_router: Optional[SignalRouter] = None

    async def initialize(self) -> None:
        """Initialize all components"""
        import time as _time

        logger.info("=" * 60)
        logger.info("TRADING BOT INITIALIZATION")
        logger.info("=" * 60)

        init_start = _time.monotonic()
        step_timings: list = []

        async def _timed_step(name: str, coro):
            """Run an init step and record its duration."""
            t0 = _time.monotonic()
            await coro
            elapsed = _time.monotonic() - t0
            step_timings.append((name, elapsed))
            if elapsed >= 1.0:
                logger.info(f"  [{name}: {elapsed:.1f}s]")

        try:
            await _timed_step("env", self._load_environment())
            await _timed_step("config", self._load_configuration())
            await _timed_step("monitoring", self._initialize_monitoring())
            await _timed_step("storage", self._initialize_storage())
            await _timed_step("connector", self._initialize_connector())
            await _timed_step("data_provider", self._initialize_data_provider())
            await _timed_step("position_mgr", self._initialize_position_manager())
            await _timed_step("strategies", self._initialize_strategies())
            await _timed_step("signal_lifecycle", self._initialize_signal_lifecycle())
            await _timed_step("trading_ctrl", self._initialize_trading_controller())
            await _timed_step("health_check", self._run_health_checks())

            total_elapsed = _time.monotonic() - init_start
            self.state.is_initialized = True
            self.state.start_time = _time.time()

            # Build startup summary
            active_strats = []
            if self.strategy_manager:
                active_strats = self.strategy_manager.list_strategies(active_only=True)
            strat_info = ", ".join(active_strats) if active_strats else "none"

            balance_str = "N/A"
            if self.signal_lifecycle:
                bal = self.signal_lifecycle.stats.get("current_balance", 0)
                balance_str = f"${bal:.2f}"

            leverage = getattr(self.config.risk, "leverage", 1) if self.config else 1
            wal_info = ""
            if self.signal_lifecycle and self.signal_lifecycle.wal:
                wal_info = f" | WAL: {self.signal_lifecycle.wal.get_stats_str()}"

            slow_steps = [
                f"{name} {elapsed:.1f}s" for name, elapsed in step_timings if elapsed >= 2.0
            ]
            slow_info = f" | Slow: {', '.join(slow_steps)}" if slow_steps else ""

            logger.info("=" * 60)
            logger.info(f"INITIALIZATION COMPLETE ({total_elapsed:.1f}s)")
            logger.info(
                f"  Mode: {self.state.mode.upper()} | Exchange: {self.state.exchange} | "
                f"Balance: {balance_str} | Leverage: {leverage}x"
            )
            logger.info(
                f"  Strategies: {strat_info} | Pairs: {len(self.strategy_pairs)}{wal_info}{slow_info}"
            )
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Initialization failed: {e}", exc_info=True)
            raise

    async def shutdown(self) -> None:
        """Graceful shutdown"""
        logger.info("=" * 60)
        logger.info("SHUTTING DOWN TRADING BOT")
        logger.info("=" * 60)

        self.state.is_running = False

        try:
            # Cancel background tasks
            for task in self.tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            # Close positions (if in production and configured to)
            if self.position_manager and hasattr(self.config, "risk"):
                if getattr(self.config.risk, "close_on_shutdown", False):
                    logger.info("Closing all positions...")
                    # Implementation depends on your position manager

            # Stop signal lifecycle manager
            if self.signal_lifecycle:
                await self.signal_lifecycle.stop()
                stats = self.signal_lifecycle.get_stats()
                logger.info(
                    f"✓ Signal lifecycle stopped - "
                    f"Signals: {stats['signals_received']}, "
                    f"Executed: {stats['signals_executed']}, "
                    f"Closed: {stats['signals_closed']}, "
                    f"Win Rate: {stats['win_rate']:.1%}, "
                    f"Balance: ${stats.get('current_balance', 0):.2f} (P&L: ${stats.get('total_pnl_usdt', 0):+.2f})"
                )

            # Send session summary to Telegram before stopping
            if self.telegram_bot and self.signal_lifecycle and self.state.start_time:
                try:
                    stats = self.signal_lifecycle.get_stats()
                    duration_hours = (time.time() - self.state.start_time) / 3600

                    await self.telegram_bot.send_session_summary(
                        duration_hours=duration_hours,
                        start_balance=stats.get("start_balance", 0),
                        end_balance=stats.get("current_balance", 0),
                        total_pnl=stats.get("total_pnl_usdt", 0),
                        total_pnl_percent=stats.get("total_pnl", 0),
                        signals_closed=stats.get("signals_closed", 0),
                        winners=stats.get("winners", 0),
                        losers=stats.get("losers", 0),
                        best_trade=stats.get("best_trade"),
                        worst_trade=stats.get("worst_trade"),
                    )
                except Exception as e:
                    logger.warning(f"Failed to send session summary: {e}")

            # Disconnect components
            if self.data_provider:
                await self.data_provider.disconnect()
                logger.info("✓ Data provider disconnected")

            if self.connector:
                await self.connector.disconnect()
                logger.info("✓ Connector disconnected")

            if self.cache:
                await self.cache.close()
                logger.info("✓ Cache closed")

            if self.database:
                await self.database.close()
                logger.info("✓ Database closed")

            # Send shutdown notification
            if self.alert_manager:
                from src.monitoring.alerts.alert_manager import TelegramAlert

                snapshot = self.performance_tracker.get_performance_snapshot("24h")

                await self.alert_manager.send_alert(
                    TelegramAlert(
                        type="system",
                        priority="medium",
                        title="Trading Bot Stopped",
                        message="Bot shutdown completed",
                        data={
                            "Uptime": f"{int((time.time() - self.state.start_time) / 3600)}h",
                            "Total Trades": snapshot.total_trades,
                            "Net P&L": f"${snapshot.net_pnl:.2f}",
                            "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
                )

            logger.info("=" * 60)
            logger.info("SHUTDOWN COMPLETE ✓")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)


def parse_arguments() -> CLIArguments:
    """
    Parse command-line arguments.

    Environment variables can be used as defaults:
    - BOT_MODE: Trading mode (paper, demo, testnet, production, backtest)
    - BOT_EXCHANGE: Exchange (bybit, mt5, mock)
    - LOG_LEVEL: Logging level
    - DASHBOARD_ENABLED: Enable dashboard (true/false)
    - TELEGRAM_ENABLED: Enable telegram (true/false)
    """
    parser = argparse.ArgumentParser(
        description="Trading Bot - Automated Trading System",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Get defaults from environment variables
    default_mode = os.environ.get("BOT_MODE", "paper")
    default_exchange = os.environ.get("BOT_EXCHANGE", "bybit")
    default_log_level = os.environ.get("LOG_LEVEL", "INFO")
    dashboard_disabled = os.environ.get("DASHBOARD_ENABLED", "true").lower() == "false"
    try:
        import yaml

        with open("config/config.yaml", encoding="utf-8") as f:
            _cfg = yaml.safe_load(f) or {}
        _mon = _cfg.get("monitoring", {})
        if not _mon.get("dashboard_enabled", True):
            dashboard_disabled = True
    except Exception:
        pass
    telegram_disabled = os.environ.get("TELEGRAM_ENABLED", "true").lower() == "false"

    parser.add_argument(
        "--mode",
        choices=["paper", "demo", "testnet", "production", "backtest"],
        default=default_mode,
        help="Trading mode: paper (local simulation), demo (Bybit demo account), "
        "testnet (Bybit testnet), production (live trading), backtest (historical test). "
        "Env: BOT_MODE",
    )

    parser.add_argument(
        "--exchange",
        choices=["bybit", "mt5", "mock"],
        default=default_exchange,
        help="Exchange to trade on (env: BOT_EXCHANGE)",
    )

    # Keep --demo and --testnet for backwards compatibility (deprecated)
    parser.add_argument(
        "--demo", action="store_true", default=False, help="[DEPRECATED] Use --mode demo instead"
    )

    parser.add_argument(
        "--testnet",
        action="store_true",
        default=False,
        help="[DEPRECATED] Use --mode testnet instead",
    )

    parser.add_argument("--config-dir", default="config/", help="Configuration directory")

    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        default=dashboard_disabled,
        help="Disable web dashboard (env: DASHBOARD_ENABLED=false)",
    )

    parser.add_argument(
        "--no-telegram",
        action="store_true",
        default=telegram_disabled,
        help="Disable Telegram notifications (env: TELEGRAM_ENABLED=false)",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=default_log_level,
        help="Logging level (env: LOG_LEVEL)",
    )

    parser.add_argument("--console", action="store_true", help="Start interactive console")

    parser.add_argument("--backtest-start", help="Backtest start date (YYYY-MM-DD)")

    parser.add_argument("--backtest-end", help="Backtest end date (YYYY-MM-DD)")

    args = parser.parse_args()

    return CLIArguments(
        mode=args.mode,
        exchange=args.exchange,
        config_dir=args.config_dir,
        no_dashboard=args.no_dashboard,
        no_telegram=args.no_telegram,
        log_level=args.log_level,
        console=args.console,
        backtest_start=args.backtest_start,
        backtest_end=args.backtest_end,
    )


async def main():
    """Main entry point"""
    # Parse arguments
    args = parse_arguments()

    # Banner
    print(
        """
╔══════════════════════════════════════════════╗
║              TRADING BOT v1.0.0              ║
║              Project-103 System              ║
╚══════════════════════════════════════════════╝
    """
    )

    # Check .env file
    project_root = Path(__file__).resolve().parent.parent
    env_file = project_root / ".env"
    env_example_file = project_root / ".env.example"
    in_docker = Path("/.dockerenv").exists() or os.environ.get("ENVIRONMENT") is not None

    if not env_file.exists() and not in_docker:
        logger.warning(".env file not found!")
        logger.info("Creating .env from template...")

        # Copy .env.example to .env
        if env_example_file.exists():
            import shutil

            shutil.copy(".env.example", ".env")
            logger.info("✓ Created .env from .env.example")
            logger.info("\nPlease edit .env file with your configuration and restart.")
            return
        else:
            logger.error(".env.example not found. Cannot proceed.")
            return

    # Create bot instance
    bot = TradingBot(args)

    # Setup signal handlers — prefer asyncio-native loop.add_signal_handler()
    # (reliable on Linux/macOS), fall back to signal.signal() on Windows.
    loop = asyncio.get_running_loop()

    def shutdown_from_signal(sig):
        logger.info(f"Received signal {sig}, initiating shutdown")
        bot.shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_from_signal, sig)
        except (NotImplementedError, RuntimeError):
            # Windows doesn't support add_signal_handler for SIGTERM.
            signal.signal(sig, lambda s, f: bot.shutdown_event.set())

    # Run bot
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
