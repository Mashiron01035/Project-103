"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 19.06.2026.
    File        : initialization.py
    Project     : Project-103
    Description : Component initialization for TradingBot — each _initialize_* step brings up one subsystem (config, monitoring, storage, connector, data provider, strategies, signal lifecycle, controller); initialize() sequences them in main.py.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from src.core.config import get_config
from src.core.exceptions import ConfigurationError, ConnectionError
from src.core.trading_controller import TradingController
from src.data.providers.bybit_provider import BybitDataProvider
from src.data.providers.mt5_provider import MT5DataProvider
from src.data.storage.database import DatabaseManager
from src.data.storage.redis_cache import RedisCache
from src.execution.connectors.bybit_connector import BybitConnector
from src.execution.connectors.connector_router import ConnectorRouter
from src.execution.connectors.mock_connector import MockConnector
from src.execution.connectors.mt5_connector import MT5Connector
from src.execution.signal_lifecycle_manager import SignalLifecycleConfig, SignalLifecycleManager
from src.models.position import PositionManager
from src.monitoring import HealthChecker, PerformanceTracker, TradingLogger
from src.monitoring.alerts import AlertManager, TelegramNotifier
from src.strategies import StrategyManager, create_strategy, list_available_strategies
from src.utils import load_env_for_trading

logger = logging.getLogger(__name__)


class InitializationMixin:
    async def _load_environment(self) -> None:
        """Load environment variables"""
        logger.info("Loading environment variables...")

        try:
            # Load .env file with validation
            loader = load_env_for_trading(exchange=self.args.exchange)

            # Check for required variables
            if not loader.validate():
                logger.warning(f"Missing variables: {loader.missing_vars}")

            logger.info("✓ Environment variables loaded")

        except Exception as e:
            logger.error(f"Failed to load environment: {e}")
            raise ConfigurationError(f"Environment loading failed: {e}")

    async def _load_configuration(self) -> None:
        """Load bot configuration"""
        logger.info("Loading configuration...")

        try:
            # Load config from config.py (which reads env vars loaded by env_loader)
            self.config = get_config()

            # Determine effective mode: CLI arg (if explicitly set) > config file > default
            # CLI default is "paper", so if args.mode == "paper" and config.mode != "paper",
            # use config.mode (user set it in config file)
            effective_mode = self.args.mode
            if self.args.mode == "paper" and self.config.mode != "paper":
                # User didn't specify --mode but set mode in config file
                effective_mode = self.config.mode
                logger.info(f"  Using mode from config: {effective_mode}")

            # Update args and state with effective mode
            self.args.mode = effective_mode
            self.state.mode = effective_mode

            # Apply trading mode based on effective mode
            if effective_mode == "demo":
                self.config.bybit.demo = True
                self.config.bybit.testnet = False
            elif effective_mode == "testnet":
                self.config.bybit.testnet = True
                self.config.bybit.demo = False
            elif effective_mode in ("production", "paper", "backtest"):
                # Use live credentials for production, paper uses simulation anyway
                self.config.bybit.demo = False
                self.config.bybit.testnet = False

            # Backwards compatibility: --demo and --testnet flags override --mode
            if hasattr(self.args, "demo") and self.args.demo:
                self.config.bybit.demo = True
                self.config.bybit.testnet = False
                logger.warning("  --demo flag is deprecated, use --mode demo instead")
            elif hasattr(self.args, "testnet") and self.args.testnet:
                self.config.bybit.testnet = True
                self.config.bybit.demo = False
                logger.warning("  --testnet flag is deprecated, use --mode testnet instead")

            # Debug: verify risk config loaded correctly
            logger.info(
                f"  Risk config: leverage={self.config.risk.leverage}x, hedge_mode={self.config.risk.hedge_mode}"
            )

            # Validate configuration based on mode
            errors = []
            # Validate API keys for modes that execute real trades
            if self.args.mode in ("demo", "testnet", "production"):
                if self.args.exchange == "bybit":
                    errors.extend(self.config.bybit.validate())
                elif self.args.exchange == "mt5":
                    if not self.config.mt5.login:
                        errors.append("MT5 login required")

            if errors:
                for error in errors:
                    logger.error(f"Configuration error: {error}")
                raise ConfigurationError(f"Configuration validation failed: {errors}")

            logger.info("✓ Configuration loaded")

            # Show trading mode
            if self.args.exchange == "bybit":
                if self.args.mode == "demo":
                    mode_desc = "DEMO - Real trades on Bybit Demo Account"
                    account_name = self.config.bybit.demo_api_name
                elif self.args.mode == "testnet":
                    mode_desc = "TESTNET - Real trades on Bybit Testnet"
                    account_name = self.config.bybit.testnet_api_name
                elif self.args.mode == "production":
                    mode_desc = "PRODUCTION - Real trades on Bybit Live"
                    account_name = self.config.bybit.api_name
                elif self.args.mode == "paper":
                    mode_desc = "PAPER - Local simulation (no real trades)"
                    account_name = None
                elif self.args.mode == "backtest":
                    mode_desc = "BACKTEST - Historical data testing"
                    account_name = None
                else:
                    mode_desc = self.args.mode.upper()
                    account_name = None

                logger.info(
                    f"TradingBot initialized in {self.args.mode.upper()} mode on {self.args.exchange}"
                )
                logger.info(f"  Mode: {mode_desc}")
                if account_name:
                    logger.info(f"  Account: {account_name}")
            else:
                logger.info(
                    f"TradingBot initialized in {self.args.mode.upper()} mode on {self.args.exchange}"
                )

            logger.info(f"  Exchange: {self.args.exchange}")
            logger.info(f"  Environment: {self.config.environment}")

        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise

    async def _initialize_monitoring(self) -> None:
        """Initialize monitoring components"""
        logger.info("Initializing monitoring...")

        try:
            # Trading logger (reuse global instance, reconfigure if needed)
            self.logger = TradingLogger(log_level=self.args.log_level, enable_json=False)
            # Update root logger level if different from default
            if self.args.log_level != "INFO":
                logging.getLogger().setLevel(getattr(logging, self.args.log_level))

            # Performance tracker
            initial_balance = (
                self.config.risk.account_balance if hasattr(self.config, "risk") else 10000.0
            )
            self.performance_tracker = PerformanceTracker(initial_balance=initial_balance)

            # Health checker
            self.health_checker = HealthChecker(check_interval=30)

            # Alert manager
            self.alert_manager = AlertManager()

            # Telegram notifier (if configured)
            if not self.args.no_telegram and hasattr(self.config, "monitoring"):
                if (
                    hasattr(self.config.monitoring, "telegram_token")
                    and self.config.monitoring.telegram_token
                ):
                    telegram = TelegramNotifier(
                        bot_token=self.config.monitoring.telegram_token,
                        chat_id=self.config.monitoring.telegram_chat_id,
                    )
                    self.alert_manager.register_notifier(telegram)
                    logger.info("  ✓ Telegram notifier configured")

            logger.info("✓ Monitoring initialized")

        except Exception as e:
            logger.error(f"Failed to initialize monitoring: {e}")
            raise

    async def _initialize_storage(self) -> None:
        """Initialize database and cache"""
        logger.info("Initializing storage...")

        try:
            # Database (synchronous - connection established in constructor)
            if hasattr(self.config, "database"):
                # Use DATABASE_URL from environment if available (for Docker)
                database_url = os.environ.get("DATABASE_URL", "")
                if database_url:
                    logger.info("  Using DATABASE_URL from environment")
                else:
                    database_url = self.config.database.url

                # Retry logic for database connection (wait for PostgreSQL to be ready)
                max_retries = 10
                retry_delay = 3  # seconds

                for attempt in range(1, max_retries + 1):
                    try:
                        self.database = DatabaseManager(database_url)
                        logger.info("  ✓ Database connected")
                        break
                    except Exception as db_error:
                        if attempt < max_retries:
                            logger.warning(
                                f"  Database connection attempt {attempt}/{max_retries} failed: {db_error}"
                            )
                            logger.info(f"  Retrying in {retry_delay} seconds...")
                            await asyncio.sleep(retry_delay)
                        else:
                            logger.error(
                                f"  Database connection failed after {max_retries} attempts. "
                                f"Position recovery will use exchange API only."
                            )
                            self.database = None

                # Register health check (only if database connected)
                if self.database:

                    async def check_database():
                        session = None
                        try:
                            session = self.database.get_session()
                            from sqlalchemy import text

                            session.execute(text("SELECT 1"))
                            return True
                        except Exception:
                            return False
                        finally:
                            if session:
                                session.close()

                    self.health_checker.register_component("database", check_database)

            # Redis cache
            if hasattr(self.config, "redis"):
                # Parse REDIS_URL from environment if available (for Docker)
                redis_url = os.environ.get("REDIS_URL", "")
                if redis_url:
                    # Parse redis://host:port/db format
                    from urllib.parse import urlparse

                    parsed = urlparse(redis_url)
                    redis_host = parsed.hostname or "localhost"
                    redis_port = parsed.port or 6379
                    redis_db = int(parsed.path.strip("/") or 0)
                    logger.info(f"  Using REDIS_URL: {redis_host}:{redis_port}/{redis_db}")
                else:
                    redis_host = (
                        self.config.redis.host
                        if hasattr(self.config.redis, "host")
                        else "localhost"
                    )
                    redis_port = (
                        self.config.redis.port if hasattr(self.config.redis, "port") else 6379
                    )
                    redis_db = 0

                self.cache = RedisCache(host=redis_host, port=redis_port, db=redis_db)
                if await self.cache.connect():
                    logger.info("  ✓ Redis cache connected")

                    # Register health check
                    async def check_redis():
                        return await self.cache.ping()

                    self.health_checker.register_component("redis", check_redis)
                else:
                    logger.warning("  Redis connection failed, continuing without cache")
                    self.cache = None

            logger.info("✓ Storage initialized")

        except Exception as e:
            logger.warning(f"Storage initialization warning: {e}")
            logger.info("  Continuing without storage...")

    async def _initialize_connector(self) -> None:
        """Initialize exchange connector"""
        logger.info(f"Initializing {self.args.exchange} connector...")

        try:
            # Check for multi-connector mode
            multi_cfg = getattr(self.config, "multi_connector", None)
            if multi_cfg and getattr(multi_cfg, "enabled", False):
                await self._initialize_multi_connector(multi_cfg)
                return

            if self.args.exchange == "bybit":
                bybit_cfg = self.config.bybit

                # Get credentials based on mode
                api_key, api_secret, account_name = bybit_cfg.get_active_credentials()

                # Determine connector mode based on args.mode
                use_demo = self.args.mode == "demo"
                use_testnet = self.args.mode == "testnet"

                # For paper mode, we still need connector for market data
                # but trades will be simulated locally
                if self.args.mode == "paper":
                    logger.info("  Paper trading mode - trades will be simulated locally")

                self.connector = BybitConnector(
                    api_key=api_key,
                    api_secret=api_secret,
                    testnet=use_testnet,
                    demo=use_demo,
                    account_name=account_name,
                )

            elif self.args.exchange == "mt5":
                # Get MT5 credentials from env only
                mt5_login = os.environ.get("MT5_LOGIN")
                mt5_password = os.environ.get("MT5_PASSWORD")
                mt5_server = os.environ.get("MT5_SERVER")
                mt5_path = os.environ.get("MT5_PATH")

                self.connector = MT5Connector(
                    login=int(mt5_login) if mt5_login else None,
                    password=mt5_password,
                    server=mt5_server,
                    path=mt5_path if mt5_path else None,
                )

            elif self.args.exchange == "mock":
                self.connector = MockConnector()
                logger.info("  Using mock connector (no real trading)")

            else:
                raise ValueError(f"Unsupported exchange: {self.args.exchange}")

            # Connect to exchange
            if await self.connector.connect():
                logger.info(f"  ✓ Connected to {self.args.exchange}")
            else:
                raise ConnectionError(self.args.exchange, "Failed to connect")

            # Register health check
            async def check_exchange():
                return self.connector.is_connected

            self.health_checker.register_component(f"exchange_{self.args.exchange}", check_exchange)

        except Exception as e:
            logger.error(f"Failed to initialize connector: {e}")
            raise

    async def _initialize_multi_connector(self, multi_cfg) -> None:
        """Initialize multi-connector router for simultaneous crypto + MT5 trading."""
        logger.info("Initializing multi-connector router...")

        router = ConnectorRouter()
        connectors_cfg = getattr(multi_cfg, "connectors", {})

        # Initialize Bybit connector if enabled
        bybit_mc_cfg = connectors_cfg.get("bybit", {})
        if bybit_mc_cfg.get("enabled", False):
            bybit_cfg = self.config.bybit
            api_key, api_secret, account_name = bybit_cfg.get_active_credentials()

            use_demo = self.args.mode == "demo"
            use_testnet = self.args.mode == "testnet"

            bybit_connector = BybitConnector(
                api_key=api_key,
                api_secret=api_secret,
                testnet=use_testnet,
                demo=use_demo,
                account_name=account_name,
            )

            is_default = bybit_mc_cfg.get("is_default", True)
            symbols = bybit_mc_cfg.get("symbols", [])
            prefixes = bybit_mc_cfg.get("prefixes", [])

            router.add_connector(
                name="bybit",
                connector=bybit_connector,
                symbols=symbols if symbols else None,
                prefixes=prefixes if prefixes else None,
                is_default=is_default,
            )
            logger.info(f"  Added Bybit connector (default={is_default})")

        # Initialize MT5 connector if enabled
        mt5_mc_cfg = connectors_cfg.get("mt5", {})
        if mt5_mc_cfg.get("enabled", False):
            try:
                # Get MT5 credentials from env only
                mt5_login = os.environ.get("MT5_LOGIN")
                mt5_password = os.environ.get("MT5_PASSWORD")
                mt5_server = os.environ.get("MT5_SERVER")
                mt5_path = os.environ.get("MT5_PATH")

                mt5_connector = MT5Connector(
                    login=int(mt5_login) if mt5_login else None,
                    password=mt5_password,
                    server=mt5_server,
                    path=mt5_path if mt5_path else None,
                )

                symbols = mt5_mc_cfg.get("symbols", [])
                prefixes = mt5_mc_cfg.get("prefixes", [])
                is_default = mt5_mc_cfg.get("is_default", False)

                router.add_connector(
                    name="mt5",
                    connector=mt5_connector,
                    symbols=symbols if symbols else None,
                    prefixes=prefixes if prefixes else None,
                    is_default=is_default,
                )
                logger.info(f"  Added MT5 connector (symbols={symbols}, prefixes={prefixes})")

            except ImportError as e:
                logger.warning(f"  MT5 connector not available: {e}")
            except Exception as e:
                logger.error(f"  Failed to initialize MT5 connector: {e}")

        # Connect all
        if await router.connect():
            self.connector = router
            logger.info("  ✓ Multi-connector router connected")
        else:
            raise ConnectionError("Multi-connector", "Failed to connect all connectors")

        # Register health check
        async def check_router():
            return router.is_connected

        self.health_checker.register_component("exchange_multi", check_router)

    async def _initialize_data_provider(self) -> None:
        """Initialize data provider and data recorder"""
        logger.info("Initializing data provider...")

        try:
            if self.args.exchange == "bybit":
                from src.data.providers.base_provider import DataProviderConfig

                # Check if data recording is disabled - if so, disable WebSocket
                # (data-collector service handles all market data via its own WebSocket)
                recording_config = getattr(self.config, "data_recording", None)
                ws_enabled = not (
                    recording_config and not getattr(recording_config, "enabled", True)
                )

                if not ws_enabled:
                    logger.info("  WebSocket disabled (data_recording.enabled=false)")
                    logger.info("  Bot will use REST API only; data-collector handles market data")

                provider_config = DataProviderConfig(
                    exchange="bybit",
                    testnet=self.config.bybit.testnet,
                    ws_enabled=ws_enabled,  # Disable WS if data_recording is disabled
                )

                self.data_provider = BybitDataProvider(provider_config)

            elif self.args.exchange == "mt5":
                from src.data.providers.base_provider import DataProviderConfig

                provider_config = DataProviderConfig(exchange="mt5")
                self.data_provider = MT5DataProvider(provider_config)

            if self.data_provider:
                await self.data_provider.connect()
                logger.info("  ✓ Data provider connected")

        except Exception as e:
            logger.error(f"Failed to initialize data provider: {e}")
            raise

    async def _initialize_position_manager(self) -> None:
        """Initialize position manager"""
        logger.info("Initializing position manager...")

        try:
            self.position_manager = PositionManager()
            logger.info("✓ Position manager initialized")

        except Exception as e:
            logger.error(f"Failed to initialize position manager: {e}")
            raise

    async def _initialize_strategies(self) -> None:
        """Initialize trading strategies"""
        logger.info("Initializing strategies...")

        try:
            self.strategy_manager = StrategyManager()

            # Load active strategies from config (NO DEFAULT - if not in config, don't load any)
            active_strategies = getattr(self.config, "active_strategies", [])

            if not active_strategies:
                logger.info("  No active strategies configured in config")
                logger.info("✓ Strategies initialized (0 active)")
                return

            # Load strategy configs from YAML
            import yaml

            strategies_config_path = Path(self.args.config_dir) / "strategies.yaml"
            strategies_config = {}
            if strategies_config_path.exists():
                with open(strategies_config_path, encoding="utf-8") as f:
                    strategies_config = yaml.safe_load(f) or {}

            # Log available strategies from registry
            available = list_available_strategies()
            logger.debug(f"  Available strategies in registry: {available}")

            # Register strategies using registry
            for strategy_name in active_strategies:
                try:
                    config = strategies_config.get(strategy_name, {})

                    # Create strategy using registry
                    strategy = create_strategy(strategy_name, config)

                    if strategy is not None:
                        self.strategy_manager.register_strategy(strategy)
                        logger.info(f"  ✓ Registered strategy: {strategy_name}")
                    else:
                        logger.warning(
                            f"  Unknown strategy: {strategy_name}. " f"Available: {available}"
                        )

                except Exception as e:
                    logger.error(f"  Failed to register strategy {strategy_name}: {e}")

            # Pass data provider to strategies that need it (for BTC trend analysis, etc.)
            if self.data_provider:
                for strategy_name in self.strategy_manager.list_strategies(active_only=True):
                    strategy = self.strategy_manager.get_strategy(strategy_name)
                    if strategy and hasattr(strategy, "set_data_provider"):
                        strategy.set_data_provider(self.data_provider)
                        logger.debug(f"  Data provider set for strategy: {strategy_name}")

            # Collect pairs from all active strategies
            all_strategy_pairs = set()
            for strategy_name in active_strategies:
                strategy_config = strategies_config.get(strategy_name, {})
                pairs = strategy_config.get("pairs", [])
                if pairs:
                    all_strategy_pairs.update(pairs)
                    logger.info(f"  Strategy '{strategy_name}' pairs: {len(pairs)}")

            self.strategy_pairs = list(all_strategy_pairs)
            if self.strategy_pairs:
                logger.info(f"  ✓ Total strategy pairs collected: {len(self.strategy_pairs)}")

            logger.info(
                f"✓ Strategies initialized ({len(self.strategy_manager.list_strategies(active_only=True))} active)"
            )

        except Exception as e:
            logger.warning(f"Strategy initialization failed: {e}")
            logger.info("  Continuing without strategies...")

    async def _initialize_signal_lifecycle(self) -> None:
        """Initialize signal lifecycle manager for tracking signals and outcomes."""
        logger.info("Initializing signal lifecycle manager...")

        try:
            # Get settings from risk config
            leverage = getattr(self.config.risk, "leverage", 1)
            hedge_mode = getattr(self.config.risk, "hedge_mode", False)
            risk_per_trade = getattr(self.config.risk, "max_risk_per_trade", 0.01)

            # Get balance: from exchange for real modes, from config for paper
            if self.args.mode in ("demo", "testnet", "production") and self.connector:
                try:
                    balances = await self.connector.get_balance()
                    # Try USDT first (crypto), then USD (MT5/forex), then any currency
                    account_balance = balances.get("USDT", 0.0) or balances.get("USD", 0.0)
                    if account_balance <= 0:
                        # Try first available currency balance
                        for key, val in balances.items():
                            if (
                                not key.endswith("_free")
                                and not key.endswith("_margin")
                                and val > 0
                            ):
                                account_balance = val
                                break
                    if account_balance <= 0:
                        # Fallback to config if no balance found
                        account_balance = getattr(self.config.risk, "account_balance", 10000.0)
                        logger.warning(
                            f"  No balance on exchange, using config: ${account_balance:.2f}"
                        )
                except Exception as e:
                    logger.warning(f"  Failed to get balance from exchange: {e}")
                    account_balance = getattr(self.config.risk, "account_balance", 10000.0)
            else:
                # Paper mode: use balance from config
                account_balance = getattr(self.config.risk, "account_balance", 10000.0)

            # Get daily profit target settings from risk config
            daily_target_enabled = getattr(self.config.risk, "daily_profit_target_enabled", False)
            daily_target_percent = getattr(self.config.risk, "daily_profit_target_percent", 10.0)
            daily_target_mode = getattr(self.config.risk, "daily_target_mode", "stop_new")
            max_position_percent = getattr(self.config.risk, "max_position_percent", 0.25)
            max_positions = getattr(self.config.risk, "max_positions", 20)
            position_sizing_method = getattr(
                self.config.risk, "position_sizing_method", "fixed_balance"
            )

            # Get dynamic risk settings
            dynamic_risk_cfg = getattr(self.config.risk, "dynamic_risk", None)
            if dynamic_risk_cfg:
                dynamic_risk_enabled = getattr(dynamic_risk_cfg, "enabled", True)
                dynamic_risk_ranging = getattr(dynamic_risk_cfg, "ranging_multiplier", 0.5)
                dynamic_risk_trending = getattr(dynamic_risk_cfg, "trending_multiplier", 1.0)
                dynamic_risk_high_vol = getattr(dynamic_risk_cfg, "high_vol_multiplier", 0.75)
                dynamic_risk_threshold = getattr(dynamic_risk_cfg, "btc_threshold", 0.015)
            else:
                dynamic_risk_enabled = True
                dynamic_risk_ranging = 0.5
                dynamic_risk_trending = 1.0
                dynamic_risk_high_vol = 0.75
                dynamic_risk_threshold = 0.015

            # Determine if we should simulate or execute real orders
            # Paper mode = simulate, others (demo/testnet/production) = real execution
            simulate_execution = self.args.mode == "paper"

            # For real modes, use exchange balance (don't load from DB)
            use_exchange_balance = self.args.mode in ("demo", "testnet", "production")

            # Get config from settings or use defaults
            # AI validation settings (safe access for backwards compatibility)
            ai_cfg = getattr(self.config, "ai", None)
            ai_enabled = getattr(ai_cfg, "enabled", False) if ai_cfg else False
            # API key: config.yaml -> env ANTHROPIC_API_KEY -> env OPENAI_API_KEY
            ai_api_key = getattr(ai_cfg, "api_key", "") if ai_cfg else ""
            if not ai_api_key:
                ai_api_key = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get(
                    "OPENAI_API_KEY", ""
                )
            ai_model = (
                getattr(ai_cfg, "model", "claude-sonnet-4-20250514")
                if ai_cfg
                else "claude-sonnet-4-20250514"
            )
            ai_provider = getattr(ai_cfg, "provider", "anthropic") if ai_cfg else "anthropic"

            lifecycle_config = SignalLifecycleConfig(
                min_confidence=0.3,
                min_risk_reward=0.1,
                simulate_execution=simulate_execution,
                slippage_percent=0.01,
                leverage=leverage,  # Apply leverage to P&L
                hedge_mode=hedge_mode,  # Allow long + short simultaneously
                hedge_disable_expiry=True,  # Don't close by time in hedge mode
                max_positions=max_positions,  # Max total open positions from risk config
                signal_expiry_seconds=3600,  # 1 hour
                monitoring_interval=1.0,
                # Balance and risk from config
                initial_balance=account_balance,  # Use balance from exchange or config
                use_exchange_balance=use_exchange_balance,  # True = don't load balance from DB
                risk_per_trade=risk_per_trade,  # Use risk from config.yaml
                max_position_percent=max_position_percent,  # Max position as % of balance
                position_sizing_method=position_sizing_method,  # "fixed_fractional" or "fixed_balance"
                break_even_activation_percent=1.0,  # Activate BE at 1% profit
                break_even_lock_profit_percent=0.3,  # Lock 0.3% profit (buffer for slippage)
                # Daily profit target
                daily_profit_target_enabled=daily_target_enabled,
                daily_profit_target_percent=daily_target_percent,
                daily_target_mode=daily_target_mode,
                # Dynamic risk
                dynamic_risk_enabled=dynamic_risk_enabled,
                dynamic_risk_ranging_multiplier=dynamic_risk_ranging,
                dynamic_risk_trending_multiplier=dynamic_risk_trending,
                dynamic_risk_high_vol_multiplier=dynamic_risk_high_vol,
                dynamic_risk_btc_threshold=dynamic_risk_threshold,
                # AI validation
                ai_validation_enabled=ai_enabled,
                ai_api_key=ai_api_key,
                ai_model=ai_model,
                ai_provider=ai_provider,
            )

            logger.info(f"  Leverage: {leverage}x")
            logger.info(f"  Hedge Mode: {'ON' if hedge_mode else 'OFF'}")
            logger.info(f"  Initial Balance: ${account_balance:.2f}")
            logger.info(f"  Risk Per Trade: {risk_per_trade*100:.1f}%")
            logger.info(f"  Max Position: {max_position_percent*100:.0f}% of balance")
            logger.info(f"  Position Sizing: {position_sizing_method}")
            logger.info(
                f"  Execution Mode: {'SIMULATED (paper)' if simulate_execution else 'REAL ORDERS'}"
            )
            if ai_enabled:
                logger.info(f"  AI Validation: ON (provider: {ai_provider}, model: {ai_model})")
            else:
                logger.info("  AI Validation: OFF")
            if daily_target_enabled:
                logger.info(f"  Daily Target: {daily_target_percent}% (auto-stop enabled)")

            # Pass connector for real execution (None for paper trading)
            execution_connector = None if simulate_execution else self.connector

            self.signal_lifecycle = SignalLifecycleManager(
                database=self.database,
                config=lifecycle_config,
                connector=execution_connector,
                strategy_manager=self.strategy_manager,
                alert_manager=self.alert_manager,
            )

            # Connect data provider for dynamic risk calculation
            if self.data_provider:
                self.signal_lifecycle.data_provider = self.data_provider
                logger.info("  Dynamic risk: enabled (BTC volatility-based)")

            # Start the lifecycle manager
            await self.signal_lifecycle.start()

            logger.info("✓ Signal lifecycle manager initialized")

            # Recover any open positions from previous session
            if self.connector and self.args.mode in ("demo", "testnet", "production"):
                await self._recover_positions()

        except Exception as e:
            logger.warning(f"Signal lifecycle initialization failed: {e}")
            logger.info("  Continuing without signal tracking...")

    async def _initialize_trading_controller(self) -> None:
        """Initialize trading controller for runtime management."""
        logger.info("Initializing trading controller...")

        try:
            self.trading_controller = TradingController(config_dir=self.args.config_dir)

            # Register components for control
            self.trading_controller.register_components(
                strategy_manager=self.strategy_manager,
                data_provider=self.data_provider,
                signal_lifecycle=self.signal_lifecycle,
                alert_manager=self.alert_manager,
            )

            # Set initial state
            self.trading_controller.status.mode = self.state.mode
            self.trading_controller.status.exchange = self.state.exchange
            self.trading_controller.status.started_at = datetime.now()

            # Set active pairs from config (all pairs for display)
            signal_pairs = getattr(self.config, "signal_pairs", [])
            grid_pairs = getattr(self.config, "grid_pairs", [])
            active_pairs = getattr(self.config, "active_pairs", [])
            all_pairs = (
                list(set(signal_pairs + grid_pairs))
                if (signal_pairs or grid_pairs)
                else active_pairs
            )
            self.trading_controller.status.active_pairs = all_pairs

            # Auto-enable trading on startup
            await self.trading_controller.enable_trading()
            logger.info("  Trading starts ENABLED")

            logger.info("✓ Trading controller initialized")

        except Exception as e:
            logger.warning(f"Trading controller initialization failed: {e}")
            logger.info("  Continuing without runtime control...")

    async def _run_health_checks(self) -> None:
        """Run initial health checks"""
        logger.info("Running health checks...")

        try:
            health = await self.health_checker.check_all()

            logger.info(f"  System status: {health.status}")
            logger.info(f"  CPU usage: {health.cpu_usage:.1f}%")
            logger.info(f"  Memory usage: {health.memory_usage:.1f}%")

            for component in health.components:
                status_icon = "✓" if component.status == "healthy" else "✗"
                logger.info(f"  {status_icon} {component.name}: {component.status}")

            if health.status == "unhealthy":
                logger.warning("System health check failed, but continuing...")

        except Exception as e:
            logger.warning(f"Health check failed: {e}")
