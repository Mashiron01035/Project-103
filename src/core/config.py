"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 18.11.2025.
    File        : config.py
    Project     : Project-103
    Description : Configuration management module. This module handles loading, validation, and management of all system configurations.

    SPDX-License-Identifier: AGPL-3.0

"""

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

# NOTE: .env loading is handled by src/utils/env_loader.py
# Do NOT call load_dotenv() here to avoid double loading

logger = logging.getLogger(__name__)


# ==================== Configuration Data Classes ====================


@dataclass
class DatabaseConfig:
    """Database configuration."""

    url: str = "sqlite:///project103.db"
    pool_size: int = 5
    max_overflow: int = 5
    pool_timeout: int = 30
    echo: bool = False

    def validate(self) -> List[str]:
        """Validate database configuration."""
        errors = []

        if not self.url:
            errors.append("Database URL is required")
        elif not self.url.startswith(("postgresql://", "mysql://", "sqlite://")):
            errors.append("Invalid database URL format")

        if self.pool_size <= 0:
            errors.append("Pool size must be positive")

        if self.max_overflow < 0:
            errors.append("Max overflow must be non-negative")

        return errors


@dataclass
class RedisConfig:
    """Redis configuration."""

    url: str = "redis://localhost:6379"
    max_connections: int = 50
    decode_responses: bool = True
    socket_timeout: int = 5
    password: Optional[str] = None

    def validate(self) -> List[str]:
        """Validate Redis configuration."""
        errors = []

        if not self.url:
            errors.append("Redis URL is required")
        elif not self.url.startswith(("redis://", "rediss://")):
            errors.append("Invalid Redis URL format")

        if self.max_connections <= 0:
            errors.append("Max connections must be positive")

        if self.socket_timeout <= 0:
            errors.append("Socket timeout must be positive")

        return errors


@dataclass
class BybitConfig:
    """Bybit exchange configuration."""

    # API credentials
    api_key: str = ""
    api_secret: str = ""
    api_name: str = ""  # Human-readable name for the account

    # Additional account
    api_key_2: str = ""
    api_secret_2: str = ""
    api_name_2: str = ""

    # Demo account (mainnet with demo trading)
    demo: bool = False
    demo_api_key: str = ""
    demo_api_secret: str = ""
    demo_api_name: str = ""

    # Testnet account
    testnet: bool = False
    testnet_api_key: str = ""
    testnet_api_secret: str = ""
    testnet_api_name: str = ""

    # Connection settings
    rate_limit: int = 50
    timeout: int = 30
    retries: int = 3

    def validate(self) -> List[str]:
        """Validate Bybit configuration."""
        errors = []
        if self.demo:
            if not self.demo_api_key:
                errors.append("Bybit demo API key is required when demo is enabled")
            if not self.demo_api_secret:
                errors.append("Bybit demo API secret is required when demo is enabled")
        elif self.testnet:
            if not self.testnet_api_key:
                errors.append("Bybit testnet API key is required when testnet is enabled")
            if not self.testnet_api_secret:
                errors.append("Bybit testnet API secret is required when testnet is enabled")
        else:
            if not self.api_key:
                errors.append("Bybit API key is required")
            if not self.api_secret:
                errors.append("Bybit API secret is required")
        return errors

    def get_active_credentials(self) -> tuple:
        """Get active API credentials based on mode."""
        if self.demo:
            return self.demo_api_key, self.demo_api_secret, self.demo_api_name
        elif self.testnet:
            return self.testnet_api_key, self.testnet_api_secret, self.testnet_api_name
        else:
            return self.api_key, self.api_secret, self.api_name


@dataclass
class BinanceConfig:
    """Binance exchange configuration."""

    api_key: str = ""
    api_secret: str = ""
    testnet: bool = False
    testnet_api_key: str = ""
    testnet_api_secret: str = ""
    rate_limit: int = 50
    timeout: int = 30
    retries: int = 3

    def validate(self) -> List[str]:
        """Validate Binance configuration."""
        errors = []
        # Binance is optional, validate only if keys provided
        if self.api_key and not self.api_secret:
            errors.append("Binance API secret is required when API key is provided")
        return errors


@dataclass
class MT5Config:
    """MetaTrader 5 configuration."""

    login: str = ""
    password: str = ""
    server: str = ""
    path: str = ""

    def validate(self) -> List[str]:
        """Validate MT5 configuration."""
        errors = []
        # MT5 is optional, validate only if login provided
        if self.login:
            if not self.password:
                errors.append("MT5 password is required when login is provided")
            if not self.server:
                errors.append("MT5 server is required when login is provided")
        return errors


@dataclass
class ExchangeConfig:
    """Combined exchange configuration (legacy compatibility)."""

    api_key: str = ""
    api_secret: str = ""
    testnet: bool = False
    rate_limit: int = 50  # requests per second
    timeout: int = 30
    retries: int = 3

    def validate(self) -> List[str]:
        """Validate exchange configuration."""
        errors = []

        if not self.api_key:
            errors.append("API key is required")

        if not self.api_secret:
            errors.append("API secret is required")

        if self.rate_limit <= 0:
            errors.append("Rate limit must be positive")

        if self.timeout <= 0:
            errors.append("Timeout must be positive")

        if self.retries < 0:
            errors.append("Retries must be non-negative")

        return errors


@dataclass
class SMTPConfig:
    """SMTP email configuration."""

    server: str = "smtp.gmail.com"
    port: int = 587
    username: str = ""
    password: str = ""
    from_email: str = ""
    to_emails: str = ""

    def validate(self) -> List[str]:
        """Validate SMTP configuration."""
        errors = []
        # SMTP is optional, validate only if username provided
        if self.username and not self.password:
            errors.append("SMTP password is required when username is provided")
        return errors


@dataclass
class WebhookConfig:
    """Webhook notification configuration."""

    url: str = ""

    def validate(self) -> List[str]:
        """Validate webhook configuration."""
        return []


@dataclass
class GrafanaConfig:
    """Grafana monitoring configuration."""

    api_key: str = ""
    url: str = "http://localhost:3000"

    def validate(self) -> List[str]:
        """Validate Grafana configuration."""
        return []


@dataclass
class SentryConfig:
    """Sentry error tracking configuration."""

    dsn: str = ""

    def validate(self) -> List[str]:
        """Validate Sentry configuration."""
        return []


@dataclass
class SecurityConfig:
    """Security configuration."""

    jwt_secret_key: str = ""

    def validate(self) -> List[str]:
        """Validate security configuration."""
        return []


@dataclass
class FeesConfig:
    """Trading fees and commissions configuration."""

    maker_fee: float = 0.0002  # 0.02% - limit orders
    taker_fee: float = 0.00055  # 0.055% - market orders (Bybit VIP 0)
    slippage: float = 0.0001  # 0.01% - slippage
    use_tiered: bool = False  # Use volume-based tiered pricing

    def validate(self) -> List[str]:
        """Validate fees configuration."""
        errors = []

        if self.maker_fee < 0 or self.maker_fee > 0.01:
            errors.append("Maker fee must be between 0 and 1%")

        if self.taker_fee < 0 or self.taker_fee > 0.01:
            errors.append("Taker fee must be between 0 and 1%")

        if self.slippage < 0 or self.slippage > 0.01:
            errors.append("Slippage must be between 0 and 1%")

        return errors


@dataclass
class TradingPairConfig:
    """Trading pair configuration."""

    symbol: str = ""
    base_asset: str = ""  # SOL
    quote_asset: str = ""  # USDT
    min_quantity: float = 0.001  # Minimum order size
    max_quantity: float = 1000.0  # Maximum order size
    quantity_step: float = 0.001  # Quantity step size
    tick_size: float = 0.01  # Price tick size
    price_precision: int = 2  # Price decimal places
    quantity_precision: int = 3  # Quantity decimal places
    contract_size: float = 1.0  # 1.0 for crypto, 100 for XAUUSD (1 lot = 100 oz)
    commission_type: str = "percentage"  # "percentage" (crypto) or "per_lot" (MT5)
    commission_per_lot: float = 0.0  # fixed $ per lot, only for per_lot type
    is_active: bool = True
    is_shitcoin: bool = False

    # TP override for different market conditions / strategies
    tp_percent_flat: Optional[List[float]] = None  # TP levels for mean_reversion strategy
    tp_percent_low: Optional[List[float]] = None  # TP levels for low_vol_scalper strategy

    def validate(self) -> List[str]:
        """Validate trading pair configuration."""
        errors = []

        if not self.symbol:
            errors.append("Symbol is required")

        if not self.base_asset or not self.quote_asset:
            errors.append("Base and quote assets are required")

        if self.min_quantity <= 0:
            errors.append("Min quantity must be positive")

        if self.max_quantity <= self.min_quantity:
            errors.append("Max quantity must be greater than min quantity")

        if self.quantity_step <= 0:
            errors.append("Quantity step must be positive")

        if self.price_precision < 0 or self.price_precision > 8:
            errors.append("Price precision must be between 0 and 8")

        if self.quantity_precision < 0 or self.quantity_precision > 8:
            errors.append("Quantity precision must be between 0 and 8")

        return errors


@dataclass
class StrategyConfig:
    """Strategy configuration."""

    name: str = ""
    enabled: bool = True
    timeframes: List[str] = field(default_factory=lambda: ["15m"])
    min_confidence: float = 0.6
    max_positions: int = 1

    # Per-strategy balance (for independent testing)
    account_balance: float = 0.0  # 0 = use global balance from risk config

    # Indicators configuration
    indicators: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Entry and exit conditions
    entry_conditions: Dict[str, Any] = field(default_factory=dict)
    exit_conditions: Dict[str, Any] = field(default_factory=dict)

    # Filters
    filters: Dict[str, Any] = field(default_factory=dict)

    # Risk parameters
    atr_multiplier: float = 1.5
    target_rr: float = 2.0
    min_risk_reward: float = 2.0  # Minimum R:R ratio
    target_rr_levels: List[float] = field(default_factory=lambda: [2.0, 3.0])

    # ATR-based stop loss & trailing
    atr_multiplier_sl: float = 1.5
    atr_multiplier_trailing: float = 1.5

    # Trailing stop activation
    trailing_activation_r: float = 1.0
    move_to_breakeven_r: float = 0.5

    # Position sizing limits
    min_sl_percent: float = 0.3
    max_sl_percent: float = 3.0

    # Cooldown between signals (seconds)
    cooldown_seconds: int = 60

    # Indicator periods (common)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    ema_fast: int = 9
    ema_medium: int = 21
    ema_slow: int = 50
    atr_period: int = 14
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    stochastic_k: int = 14
    stochastic_d: int = 3
    stochastic_smooth: int = 3

    # Entry thresholds
    min_confirmations: int = 4
    rsi_oversold: int = 30
    rsi_overbought: int = 70
    volume_spike_threshold: float = 1.5
    orderflow_imbalance_threshold: float = 0.25

    # Extra parameters for strategy-specific config
    extra: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        """Validate strategy configuration."""
        errors = []

        if not self.name:
            errors.append("Strategy name is required")

        if not self.timeframes:
            errors.append("At least one timeframe is required")

        if self.min_confidence < 0 or self.min_confidence > 1:
            errors.append("Min confidence must be between 0 and 1")

        if self.max_positions <= 0:
            errors.append("Max positions must be positive")

        return errors

    def get(self, key: str, default: Any = None) -> Any:
        """Get config value with fallback to extra dict."""
        if hasattr(self, key):
            return getattr(self, key)
        return self.extra.get(key, default)


@dataclass
class RiskConfig:
    """Risk management configuration."""

    account_balance: float = 10000.0
    max_risk_per_trade: float = 0.01  # 1%
    max_position_percent: float = 0.25  # 25% of balance max per position
    max_daily_drawdown: float = 0.05  # 5%
    max_positions: int = 3

    # Leverage settings
    leverage: int = 1  # Default leverage (1 = no leverage)
    max_leverage: int = 100  # Maximum allowed leverage

    # Hedge mode - allow long + short on same pair
    hedge_mode: bool = False

    # Daily profit target (auto-stop when reached)
    daily_profit_target_enabled: bool = False
    daily_profit_target_percent: float = 10.0  # Stop at +10% daily
    daily_target_mode: str = "stop_new"  # stop_new | close_all | close_profitable

    # Position sizing
    position_sizing_method: str = "fixed_fractional"
    # Sizing balance cap (mirror of backtest --balance-cap): 0 = off;
    # 0<cap<100 = percent of current balance (dynamic); >=100 = hard dollar cap.
    balance_cap: float = 0.0

    # Risk:Reward ratios
    risk_reward_normal: Dict[str, float] = field(
        default_factory=lambda: {"low": 2.0, "medium": 2.5, "high": 3.0}
    )
    risk_reward_shitcoin: Dict[str, float] = field(
        default_factory=lambda: {"low": 3.0, "medium": 4.0, "high": 5.0}
    )

    # Stop loss configuration
    stop_loss_type: str = "dynamic"  # "fixed", "dynamic", "atr"
    atr_multiplier: float = 1.5
    trailing_stop_enabled: bool = True
    trailing_stop_distance: float = 0.005  # 0.5%

    # Dynamic risk based on volatility
    dynamic_risk: Optional[Dict[str, Any]] = None

    def validate(self) -> List[str]:
        """Validate risk configuration."""
        errors = []

        if self.account_balance <= 0:
            errors.append("Account balance must be positive")

        if self.max_risk_per_trade <= 0 or self.max_risk_per_trade > 0.1:
            errors.append("Max risk per trade must be between 0 and 10%")

        if self.max_daily_drawdown <= 0 or self.max_daily_drawdown > 1.0:
            errors.append("Max daily drawdown must be between 0 and 100%")

        if self.max_positions <= 0:
            errors.append("Max positions must be positive")

        if self.leverage < 1 or self.leverage > 125:
            errors.append("Leverage must be between 1 and 125")

        if self.max_leverage < 1 or self.max_leverage > 125:
            errors.append("Max leverage must be between 1 and 125")

        valid_sizing_methods = ["fixed_fractional", "kelly", "fixed_amount", "fixed_balance"]
        if self.position_sizing_method not in valid_sizing_methods:
            errors.append(f"Invalid position sizing method. Must be one of {valid_sizing_methods}")

        valid_stop_loss_types = ["fixed", "dynamic", "atr"]
        if self.stop_loss_type not in valid_stop_loss_types:
            errors.append(f"Invalid stop loss type. Must be one of {valid_stop_loss_types}")

        if self.atr_multiplier <= 0:
            errors.append("ATR multiplier must be positive")

        if self.trailing_stop_distance < 0 or self.trailing_stop_distance > 0.1:
            errors.append("Trailing stop distance must be between 0 and 10%")

        return errors


@dataclass
class AIValidationConfig:
    """AI signal validation configuration."""

    enabled: bool = False
    provider: str = "anthropic"  # "anthropic", "openai", "cli"
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.3
    max_tokens: int = 1024
    timeout: int = 120

    # Validation thresholds
    min_confidence: float = 0.7
    min_rr_ratio: float = 1.5

    def validate(self) -> List[str]:
        """Validate AI configuration."""
        errors = []

        if self.enabled and not self.api_key and self.provider != "cli":
            errors.append("AI API key is required when AI validation is enabled")

        if self.temperature < 0 or self.temperature > 1:
            errors.append("AI temperature must be between 0 and 1")

        return errors


@dataclass
class MonitoringConfig:
    """Monitoring configuration."""

    dashboard_enabled: bool = True
    dashboard_port: int = 8080

    # Telegram configuration
    telegram_enabled: bool = False
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Alert configuration
    alert_on_signal: bool = True
    alert_on_trade: bool = True
    alert_on_error: bool = True
    daily_report_time: str = "22:00"  # UTC

    # Metrics configuration
    metrics_interval: int = 60  # seconds
    health_check_interval: int = 30  # seconds

    def validate(self) -> List[str]:
        """Validate monitoring configuration."""
        errors = []

        if self.dashboard_enabled:
            if self.dashboard_port <= 0 or self.dashboard_port > 65535:
                errors.append("Dashboard port must be between 1 and 65535")

        if self.telegram_enabled:
            if not self.telegram_token:
                errors.append("Telegram token is required when Telegram is enabled")
            if not self.telegram_chat_id:
                errors.append("Telegram chat ID is required when Telegram is enabled")

        # Validate time format
        try:
            datetime.strptime(self.daily_report_time, "%H:%M")
        except ValueError:
            errors.append("Daily report time must be in HH:MM format")

        if self.metrics_interval <= 0:
            errors.append("Metrics interval must be positive")

        if self.health_check_interval <= 0:
            errors.append("Health check interval must be positive")

        return errors


@dataclass
class TelegramBotConfig:
    """Telegram control bot configuration."""

    enabled: bool = True
    allowed_users: List[int] = field(default_factory=list)

    def validate(self) -> List[str]:
        """Validate telegram bot configuration."""
        errors = []

        if self.enabled and not self.allowed_users:
            errors.append("Telegram bot enabled but no allowed users configured")

        return errors


@dataclass
class BotConfig:
    """Main bot configuration."""

    # Basic settings
    mode: str = "paper"  # "paper", "demo", "testnet", "production", "backtest"
    environment: str = "development"  # "development", "staging", "production"
    debug: bool = False

    # Component configurations
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)  # Legacy

    # Exchange-specific configurations
    bybit: BybitConfig = field(default_factory=BybitConfig)
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    mt5: MT5Config = field(default_factory=MT5Config)

    # Trading configuration
    trading_pairs: Dict[str, TradingPairConfig] = field(default_factory=dict)
    active_pairs: List[str] = field(default_factory=list)
    signal_pairs: List[str] = field(default_factory=list)  # Pairs for signal strategies
    grid_pairs: List[str] = field(default_factory=list)  # Pairs for grid trading

    # Strategy configuration
    strategies: Dict[str, StrategyConfig] = field(default_factory=dict)
    active_strategies: List[str] = field(default_factory=list)

    # Risk management
    risk: RiskConfig = field(default_factory=RiskConfig)

    # AI signal validation
    ai: AIValidationConfig = field(default_factory=AIValidationConfig)

    # Trading fees
    fees: FeesConfig = field(default_factory=FeesConfig)

    # Monitoring
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

    # Telegram control bot
    telegram: TelegramBotConfig = field(default_factory=TelegramBotConfig)

    # Notifications
    smtp: SMTPConfig = field(default_factory=SMTPConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)

    # External services
    grafana: GrafanaConfig = field(default_factory=GrafanaConfig)
    sentry: SentryConfig = field(default_factory=SentryConfig)

    # Security
    security: SecurityConfig = field(default_factory=SecurityConfig)

    # Logging configuration
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: str = "logs/project103.log"

    # Performance configuration
    max_workers: int = 4
    event_loop_interval: float = 0.1  # seconds

    # Warmup (replays N candles through strategies at startup)
    warmup: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        """Validate bot configuration."""
        errors = []

        # Validate mode
        valid_modes = ["paper", "demo", "testnet", "production", "backtest"]
        if self.mode not in valid_modes:
            errors.append(f"Invalid mode. Must be one of {valid_modes}")

        # Validate environment
        valid_environments = ["development", "staging", "production"]
        if self.environment not in valid_environments:
            errors.append(f"Invalid environment. Must be one of {valid_environments}")

        # Don't validate component configs if using defaults
        if self.exchange.api_key:  # Only validate if API key is provided
            errors.extend(self.exchange.validate())

        # Validate other components
        errors.extend(self.database.validate())
        errors.extend(self.redis.validate())
        errors.extend(self.risk.validate())
        errors.extend(self.monitoring.validate())

        # Validate trading pairs if any are configured
        if self.active_pairs:
            for pair in self.active_pairs:
                if pair not in self.trading_pairs:
                    errors.append(f"Active pair {pair} not found in trading_pairs")
                else:
                    errors.extend(self.trading_pairs[pair].validate())

        # Validate strategies if any are configured
        if self.active_strategies:
            for strategy in self.active_strategies:
                if strategy not in self.strategies:
                    errors.append(f"Active strategy {strategy} not found in strategies")
                else:
                    errors.extend(self.strategies[strategy].validate())

        # Validate log level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level not in valid_log_levels:
            errors.append(f"Invalid log level. Must be one of {valid_log_levels}")

        # Validate performance settings
        if self.max_workers <= 0:
            errors.append("Max workers must be positive")

        if self.event_loop_interval <= 0:
            errors.append("Event loop interval must be positive")

        return errors


# ==================== Configuration Manager ====================


class ConfigManager:
    """Configuration management class (simplified without external dependencies)."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern implementation."""
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_dir: str = "config/"):
        """
        Initialize configuration manager.

        Args:
            config_dir: Directory containing configuration files
        """
        if hasattr(self, "_initialized"):
            return

        self.config_dir = Path(config_dir)
        self._config: Optional[BotConfig] = None
        self._watchers: List[Callable[[BotConfig], None]] = []
        self._lock = threading.RLock()
        self._initialized = True

    def _load_yaml_file(self, file_path: Path) -> Dict[str, Any]:
        """Load YAML configuration file."""
        if not file_path.exists():
            return {}

        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load_json_file(self, file_path: Path) -> Dict[str, Any]:
        """Load JSON configuration file."""
        if not file_path.exists():
            return {}

        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_environment_variables(self) -> Dict[str, Any]:
        """Load configuration from environment variables (secrets only)."""
        env_config = {}

        # Map environment variables to configuration paths
        # NOTE: Only secrets should be here! Settings go in config.yaml
        env_mapping = {
            # Bybit - Main account
            "BYBIT_API_NAME": "bybit.api_name",
            "BYBIT_API_KEY": "bybit.api_key",
            "BYBIT_API_SECRET": "bybit.api_secret",
            # Bybit - Secondary account
            "BYBIT_API_NAME_2": "bybit.api_name_2",
            "BYBIT_API_KEY_2": "bybit.api_key_2",
            "BYBIT_API_SECRET_2": "bybit.api_secret_2",
            # Bybit - Demo account (mainnet with demo trading)
            "BYBIT_DEMO_API_NAME": "bybit.demo_api_name",
            "BYBIT_DEMO_API_KEY": "bybit.demo_api_key",
            "BYBIT_DEMO_API_SECRET": "bybit.demo_api_secret",
            # Bybit - Testnet account
            "BYBIT_TESTNET_API_NAME": "bybit.testnet_api_name",
            "BYBIT_TESTNET_API_KEY": "bybit.testnet_api_key",
            "BYBIT_TESTNET_API_SECRET": "bybit.testnet_api_secret",
            # Binance
            "BINANCE_API_KEY": "binance.api_key",
            "BINANCE_API_SECRET": "binance.api_secret",
            "BINANCE_TESTNET_API_KEY": "binance.testnet_api_key",
            "BINANCE_TESTNET_API_SECRET": "binance.testnet_api_secret",
            # MT5
            "MT5_LOGIN": "mt5.login",
            "MT5_PASSWORD": "mt5.password",
            "MT5_SERVER": "mt5.server",
            "MT5_PATH": "mt5.path",
            # Database & Cache
            "DATABASE_URL": "database.url",
            "REDIS_URL": "redis.url",
            "REDIS_PASSWORD": "redis.password",
            # Telegram
            "TELEGRAM_BOT_TOKEN": "monitoring.telegram_token",
            "TELEGRAM_CHAT_ID": "monitoring.telegram_chat_id",
            # SMTP
            "SMTP_SERVER": "smtp.server",
            "SMTP_PORT": "smtp.port",
            "SMTP_USERNAME": "smtp.username",
            "SMTP_PASSWORD": "smtp.password",
            "SMTP_FROM_EMAIL": "smtp.from_email",
            "SMTP_TO_EMAILS": "smtp.to_emails",
            # Webhook
            "WEBHOOK_URL": "webhook.url",
            # Grafana
            "GRAFANA_API_KEY": "grafana.api_key",
            # Sentry
            "SENTRY_DSN": "sentry.dsn",
            # Security
            "JWT_SECRET_KEY": "security.jwt_secret_key",
        }

        # Legacy mapping (applied after main mapping for backward compatibility)
        legacy_mapping = {
            "BYBIT_API_KEY": "exchange.api_key",
            "BYBIT_API_SECRET": "exchange.api_secret",
        }

        # Boolean environment variables
        bool_vars = {"DEBUG", "BYBIT_TESTNET", "BINANCE_TESTNET"}

        # Integer environment variables
        int_vars = {"SMTP_PORT"}

        # Float environment variables
        float_vars = set()

        # Process main mapping
        for env_var, config_path in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None and value != "":
                # Convert string values to appropriate types
                if env_var in bool_vars:
                    value = value.lower() in ["true", "1", "yes"]
                elif env_var in int_vars:
                    value = int(value)
                elif env_var in float_vars:
                    value = float(value)

                # Set nested configuration value
                self._set_nested_value(env_config, config_path, value)

        # Process legacy mapping for backward compatibility
        for env_var, config_path in legacy_mapping.items():
            value = os.getenv(env_var)
            if value is not None and value != "":
                self._set_nested_value(env_config, config_path, value)

        return env_config

    def _set_nested_value(self, data: Dict, path: str, value: Any) -> None:
        """Set a nested dictionary value using dot notation."""
        keys = path.split(".")
        current = data

        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    def _merge_configs(self, *configs: Dict[str, Any]) -> Dict[str, Any]:
        """Merge multiple configuration dictionaries."""
        result = {}

        for config in configs:
            self._deep_merge(result, config)

        return result

    def _deep_merge(self, target: Dict, source: Dict) -> Dict:
        """Deep merge source dictionary into target."""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                self._deep_merge(target[key], value)
            else:
                target[key] = value

        return target

    def _create_config_object(self, config_dict: Dict[str, Any]) -> BotConfig:
        """Create BotConfig object from dictionary."""
        # Create component configurations
        database_config = DatabaseConfig(**config_dict.get("database", {}))
        redis_config = RedisConfig(**config_dict.get("redis", {}))
        exchange_config = ExchangeConfig(**config_dict.get("exchange", {}))

        # Create exchange-specific configurations
        bybit_config = BybitConfig(**config_dict.get("bybit", {}))
        binance_config = BinanceConfig(**config_dict.get("binance", {}))
        mt5_config = MT5Config(**config_dict.get("mt5", {}))

        # Create trading pair configurations
        trading_pairs = {}
        for symbol, pair_config in config_dict.get("trading_pairs", {}).items():
            trading_pairs[symbol] = TradingPairConfig(**pair_config)

        # Create strategy configurations
        strategies = {}
        for name, strategy_config in config_dict.get("strategies", {}).items():
            # Get known fields from StrategyConfig dataclass
            known_fields = set(StrategyConfig.__dataclass_fields__.keys())
            known_fields.discard("name")  # We set name separately

            # Separate known and extra parameters
            known_params = {}
            extra_params = {}

            for key, value in strategy_config.items():
                if key in known_fields:
                    known_params[key] = value
                else:
                    extra_params[key] = value

            # Create config with known params, store extras
            strategies[name] = StrategyConfig(name=name, extra=extra_params, **known_params)

        # Create risk configuration
        risk_config = RiskConfig(**config_dict.get("risk", {}))

        # Create AI validation configuration
        ai_dict = config_dict.get("ai", {})
        # Support env var for API key
        if not ai_dict.get("api_key"):
            ai_dict["api_key"] = os.getenv("ANTHROPIC_API_KEY", os.getenv("OPENAI_API_KEY", ""))
        ai_config = AIValidationConfig(**ai_dict)

        # Create fees configuration
        fees_config = FeesConfig(**config_dict.get("fees", {}))

        # Create monitoring configuration
        monitoring_config = MonitoringConfig(**config_dict.get("monitoring", {}))

        # Create telegram bot configuration
        telegram_dict = config_dict.get("telegram", {})
        # Read allowed_users from env (comma-separated list of user IDs)
        env_allowed_users = os.getenv("TELEGRAM_ALLOWED_USERS", "")
        if env_allowed_users:
            telegram_dict["allowed_users"] = [
                int(uid.strip()) for uid in env_allowed_users.split(",") if uid.strip()
            ]
        telegram_config = TelegramBotConfig(**telegram_dict)

        # Create notification configurations
        smtp_config = SMTPConfig(**config_dict.get("smtp", {}))
        webhook_config = WebhookConfig(**config_dict.get("webhook", {}))

        # Create external service configurations
        grafana_config = GrafanaConfig(**config_dict.get("grafana", {}))
        sentry_config = SentryConfig(**config_dict.get("sentry", {}))

        # Create security configuration
        security_config = SecurityConfig(**config_dict.get("security", {}))

        # Create main configuration
        bot_config = BotConfig(
            mode=config_dict.get("mode", "paper"),
            environment=config_dict.get("environment", "development"),
            debug=config_dict.get("debug", False),
            database=database_config,
            redis=redis_config,
            exchange=exchange_config,
            bybit=bybit_config,
            binance=binance_config,
            mt5=mt5_config,
            trading_pairs=trading_pairs,
            active_pairs=config_dict.get("active_pairs", []),
            signal_pairs=config_dict.get("signal_pairs", []),
            grid_pairs=config_dict.get("grid_pairs", []),
            strategies=strategies,
            active_strategies=config_dict.get("active_strategies", []),
            risk=risk_config,
            ai=ai_config,
            fees=fees_config,
            monitoring=monitoring_config,
            telegram=telegram_config,
            smtp=smtp_config,
            webhook=webhook_config,
            grafana=grafana_config,
            sentry=sentry_config,
            security=security_config,
            log_level=config_dict.get("log_level", "INFO"),
            log_format=config_dict.get(
                "log_format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            ),
            log_file=config_dict.get("log_file", "logs/project103.log"),
            max_workers=config_dict.get("max_workers", 4),
            event_loop_interval=config_dict.get("event_loop_interval", 0.1),
            warmup=config_dict.get("warmup", {}),
        )

        return bot_config

    def load_config(self) -> BotConfig:
        """
        Load all configuration files and environment variables.

        Returns:
            BotConfig: Complete configuration object
        """
        with self._lock:
            try:
                # Create default config if no config files exist
                if not self.config_dir.exists() or not any(self.config_dir.glob("*.yaml")):
                    logger.warning("No configuration files found, using defaults")
                    self._config = BotConfig()

                    # Add demo trading pair
                    self._config.trading_pairs["SOLUSDT"] = TradingPairConfig(
                        symbol="SOLUSDT",
                        base_asset="SOL",
                        quote_asset="USDT",
                        min_quantity=0.01,
                        max_quantity=1000.0,
                        quantity_step=0.01,
                        price_precision=2,
                        quantity_precision=2,
                        is_active=True,
                        is_shitcoin=False,
                    )
                    self._config.active_pairs = ["SOLUSDT"]

                    # Add demo strategy
                    self._config.strategies["momentum"] = StrategyConfig(
                        name="momentum",
                        enabled=True,
                        timeframes=["15m", "1h"],
                        min_confidence=0.6,
                        max_positions=1,
                    )
                    self._config.active_strategies = ["momentum"]

                    return self._config

                # Load base configuration
                base_config = {}
                base_config_file = self.config_dir / "config.yaml"
                if base_config_file.exists():
                    base_config = self._load_yaml_file(base_config_file)

                # Load additional configuration files
                additional_configs = []

                # Trading pairs configuration
                pairs_file = self.config_dir / "trading_pairs.yaml"
                if pairs_file.exists():
                    pairs_config = self._load_yaml_file(pairs_file)
                    additional_configs.append({"trading_pairs": pairs_config})

                # Strategies configuration
                strategies_file = self.config_dir / "strategies.yaml"
                if strategies_file.exists():
                    strategies_config = self._load_yaml_file(strategies_file)
                    additional_configs.append({"strategies": strategies_config})

                # Risk parameters
                risk_file = self.config_dir / "risk_params.yaml"
                if risk_file.exists():
                    risk_config = self._load_yaml_file(risk_file)
                    additional_configs.append({"risk": risk_config})

                # Load environment variables (highest priority)
                env_vars = self._load_environment_variables()
                additional_configs.append(env_vars)

                # Merge all configurations
                final_config = self._merge_configs(base_config, *additional_configs)

                # Create configuration object
                self._config = self._create_config_object(final_config)

                # Notify watchers
                self._notify_watchers()

                logger.info("Configuration loaded successfully")
                return self._config

            except Exception as e:
                logger.error(f"Failed to load configuration: {e}")
                # Return default config on error
                self._config = BotConfig()
                return self._config

    def reload_config(self) -> BotConfig:
        """
        Reload configuration without stopping the system.

        Returns:
            BotConfig: Updated configuration
        """
        logger.info("Reloading configuration...")
        return self.load_config()

    def get_config(self) -> BotConfig:
        """
        Get current configuration.

        Returns:
            BotConfig: Current configuration
        """
        with self._lock:
            if not self._config:
                return self.load_config()
            return self._config

    def update_config(self, updates: Dict[str, Any]) -> BotConfig:
        """
        Update configuration with partial updates.

        Args:
            updates: Dictionary with configuration updates

        Returns:
            BotConfig: Updated configuration
        """
        with self._lock:
            if not self._config:
                self._config = BotConfig()

            # Convert current config to dictionary
            current_dict = asdict(self._config)

            # Apply updates
            self._deep_merge(current_dict, updates)

            # Create new configuration object
            self._config = self._create_config_object(current_dict)

            # Notify watchers
            self._notify_watchers()

            logger.info("Configuration updated successfully")
            return self._config

    def watch_changes(self, callback: Callable[[BotConfig], None]) -> None:
        """
        Register callback for configuration changes.

        Args:
            callback: Function to call when configuration changes
        """
        with self._lock:
            if callback not in self._watchers:
                self._watchers.append(callback)
                logger.debug(f"Registered configuration watcher: {callback.__name__}")

    def _notify_watchers(self) -> None:
        """Notify all registered watchers of configuration change."""
        if not self._config:
            return

        for watcher in self._watchers:
            try:
                watcher(self._config)
            except Exception as e:
                logger.error(f"Error notifying watcher {watcher.__name__}: {e}")

    def save_config(self, file_path: Optional[str] = None) -> None:
        """
        Save current configuration to file.

        Args:
            file_path: Optional path to save configuration
        """
        with self._lock:
            if not self._config:
                logger.error("No configuration to save")
                return

            # Determine save path
            if file_path:
                save_path = Path(file_path)
            else:
                save_path = self.config_dir / "config.saved.yaml"

            # Convert configuration to dictionary
            config_dict = asdict(self._config)

            # Ensure directory exists
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # Save to file
            with open(save_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    config_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True
                )

            logger.info(f"Configuration saved to: {save_path}")

    def get_config_value(self, path: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-notation path.

        Args:
            path: Dot-notation path (e.g., "risk.max_risk_per_trade")
            default: Default value if path not found

        Returns:
            Configuration value or default
        """
        with self._lock:
            if not self._config:
                return default

            current = asdict(self._config)
            keys = path.split(".")

            for key in keys:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return default

            return current


# ==================== Helper Functions ====================

# Singleton instance
_config_manager = ConfigManager()


def get_config() -> BotConfig:
    """
    Get current configuration (convenience function).

    Returns:
        BotConfig: Current configuration
    """
    return _config_manager.get_config()


def load_config(config_dir: str = "config/") -> BotConfig:
    """
    Load configuration (convenience function).

    Args:
        config_dir: Configuration directory

    Returns:
        BotConfig: Loaded configuration
    """
    config_manager = ConfigManager(config_dir)
    return config_manager.load_config()


def reload_config() -> BotConfig:
    """
    Reload configuration (convenience function).

    Returns:
        BotConfig: Reloaded configuration
    """
    return _config_manager.reload_config()
