"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 30.11.2025.
    File        : signal_lifecycle_manager
    Project     : Project-103
    Description : Orchestrates the complete signal lifecycle from generation to outcome tracking.
                  Connects all existing modules: SignalValidator, SignalAggregator, SignalHistory,
                  RiskManager, OrderBuilder, PositionTracker.
                  Includes Break-Even stop management with re-entry capability.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.data.storage.database import DatabaseManager
from src.data.storage.signal_wal import SignalWAL, WALEntryType
from src.execution.limit_order_manager import (
    LimitOrderManager,
    PendingLimitOrder,
    PendingOrderStatus,
)
from src.execution.trailing_stop import (
    BreakEvenConfig,
    BreakEvenManager,
    BreakEvenState,
    BreakEvenStatus,
    ReentryOrder,
)
from src.models.signal import Signal
from src.signals.history import SignalHistory, SignalOutcome
from src.signals.validator import SignalValidator
from src.utils.datetime_utils import utcnow
from src.utils.log_tags import SYS, P, S  # Position, Signal, Order, System tags

# Optional AI signal validation — not part of the public build. Guarded so the
# engine runs without the proprietary src.ai package.
try:
    from src.ai import AIConfig, AIRequest, AISignalValidator, ValidationResult

    _AI_AVAILABLE = True
except ImportError:
    AISignalValidator = AIConfig = AIRequest = ValidationResult = None
    _AI_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class TrackedSignal:
    """Signal being tracked for outcome."""

    signal: Signal
    signal_id: str
    symbol: str
    side: str  # BUY or SELL
    entry_price: float
    stop_loss: float
    take_profit: List[float]
    created_at: datetime
    status: str = "PENDING"  # PENDING, EXECUTED, CLOSED, EXPIRED

    # AI-adjusted values (if AI suggested different prices)
    ai_adjusted_entry: Optional[float] = None  # AI-recommended entry price

    # Strategy info (for per-strategy balance tracking)
    strategy_name: str = ""
    strategy_balance: float = 0.0  # Balance allocated to this strategy (0 = use global)

    # Execution details
    executed_at: Optional[datetime] = None
    executed_price: Optional[float] = None
    exchange_order_id: Optional[str] = None  # Order ID from exchange (Bybit orderId)
    position_ticket: Optional[int] = None  # MT5 position ticket (for multi-position SL/TP)

    # Exchange order IDs for SL/TP (for modifying/cancelling)
    sl_order_id: Optional[str] = None  # Stop Loss order ID on exchange
    tp_order_ids: List[str] = field(default_factory=list)  # Take Profit order IDs [TP1, TP2, ...]
    trailing_order_id: Optional[str] = None  # Trailing stop order ID on exchange
    be_sl_synced: bool = False  # True if SL was updated to BE price on exchange

    # Partial close tracking
    remaining_percent: float = 100.0  # How much of position is still open (100%, 60%, 20%)
    realized_pnl: float = 0.0  # Accumulated P&L from partial closes (percent)
    realized_pnl_usdt: float = 0.0  # Accumulated P&L in USDT
    partial_closes: List[Dict] = field(default_factory=list)  # History of partial exits
    current_tp_index: int = 0  # Which TP level we're targeting (0, 1, 2)

    # Position sizing
    position_size_usdt: float = 0.0  # Position size in USDT
    risk_amount_usdt: float = 0.0  # Risk amount in USDT (what we lose if SL hit)

    # Tracking max profit
    max_profit_percent: float = 0.0  # Maximum profit reached before close

    # Trailing Take Profit (for remaining position after TP2)
    trailing_tp_active: bool = False  # Is trailing TP mode active
    trailing_tp_stop: Optional[float] = None  # Current trailing stop price
    trailing_tp_max_price: Optional[float] = None  # Highest price (LONG) or lowest (SHORT)
    trailing_tp_last_sync: float = 0.0  # Timestamp of last exchange sync (debounce)
    trailing_tp_pending_price: Optional[float] = None  # Pending price to sync

    # Outcome
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # STOP_LOSS, TAKE_PROFIT_1, TAKE_PROFIT_2, EXPIRED
    pnl: Optional[float] = None
    pnl_percent: Optional[float] = None

    # Exchange-reported fees and PnL (fetched at close)
    exchange_pnl: Optional[float] = None  # Closed PnL from exchange (USDT)
    open_fee: float = 0.0  # Entry commission (USDT)
    close_fee: float = 0.0  # Exit commission (USDT)
    funding_fee: float = 0.0  # Total funding fees during position (USDT)
    total_fees: float = 0.0  # open_fee + close_fee + funding_fee
    net_pnl: Optional[float] = None  # exchange_pnl - total_fees (real profit)

    # DCA (Dollar Cost Averaging) - add to losing positions
    dca_enabled: bool = False  # True if DCA is enabled for this signal
    dca_level: int = 0  # Current DCA level (0 = initial, 1 = first DCA, etc.)
    dca_max_levels: int = 4  # Maximum number of DCA entries
    dca_threshold: float = 0.8  # Loss % to trigger next DCA level
    dca_size_multiplier: float = 1.5  # Size multiplier per DCA level
    dca_recalc_tp: bool = True  # Recalculate TP based on new avg entry
    dca_sl_tighten_factor: float = 0.8  # SL tightening after DCA
    dca_use_trend_filter: bool = False  # Skip DCA if strong trend against
    dca_rsi_filter: bool = False  # Only DCA if entry RSI was extreme
    dca_rsi_extreme_threshold: float = 25.0  # RSI threshold for filter
    dca_require_retracement: bool = False  # DCA only on price bounce back
    dca_extreme_price: Optional[float] = None  # Tracking price for retracement
    dca_touched_levels: List[int] = field(default_factory=list)  # Levels touched
    initial_quantity: float = 0.0  # Original position size (for DCA calc)
    original_entry_price: float = 0.0  # First entry price before DCA
    original_stop_loss: float = 0.0  # Original SL before DCA adjustments
    entry_rsi: float = 50.0  # RSI at entry (for DCA filter)


@dataclass
class SignalLifecycleConfig:
    """Configuration for signal lifecycle manager."""

    # Signal validation
    min_confidence: float = 0.3
    min_risk_reward: float = 1.0
    max_signal_age_seconds: int = 300  # 5 minutes

    # Position simulation (paper trading)
    simulate_execution: bool = True
    slippage_percent: float = 0.01  # 0.01%

    # Leverage (multiplies P&L)
    leverage: int = 10  # 1 = no leverage, 10 = 10x leverage

    # Tracking
    max_tracked_signals: int = 100
    signal_expiry_seconds: int = 3600  # 1 hour

    # Hedge mode - allows both long and short on same symbol
    hedge_mode: bool = True  # If True, can have long + short simultaneously
    hedge_disable_expiry: bool = True  # If True, don't close by time in hedge mode
    max_positions: int = 20  # Maximum total open positions (from risk config)

    # Monitoring interval
    monitoring_interval: float = 1.0  # Check every second

    # Position sync with exchange (detect external closes like SL/TP fills)
    position_sync_interval: float = 30.0  # Sync with exchange every 30 seconds
    position_sync_enabled: bool = True  # Enable position synchronization

    # Balance sync with exchange
    balance_sync_enabled: bool = True  # Sync balance with exchange
    balance_sync_interval: float = 60.0  # Sync balance every 60 seconds
    balance_sync_log_diff: bool = True  # Log difference between bot and exchange balance

    # Break-Even Stop settings
    break_even_enabled: bool = True
    break_even_activation_percent: float = 1.0  # Activate BE at 1.0% profit
    break_even_lock_profit_ratio: float = 0.5  # Lock 50% of TP1 distance (matches backtest)
    break_even_lock_profit_percent: float = 0.8  # Fallback: fixed % lock when no TP prices
    break_even_maker_fee: float = 0.02  # Maker fee %
    break_even_taker_fee: float = 0.055  # Taker fee %
    break_even_buffer_percent: float = 0.01  # Buffer above BE price

    # Re-entry settings
    reentry_enabled: bool = True
    reentry_price_offset_percent: float = 0.3  # Re-enter 0.3% better than BE trigger
    reentry_max_count: int = 2  # Max re-entries per signal
    reentry_expiry_seconds: int = 1800  # 30 min expiry for re-entry orders

    # Re-entry improvements (B)
    reentry_require_better_price: bool = True  # Only re-enter at better price than original
    reentry_min_improvement_percent: float = 0.3  # Minimum 0.3% improvement required
    reentry_use_limit_order: bool = True  # Use limit order (not market) for better fills

    # Partial close settings (scale out)
    partial_close_enabled: bool = True
    partial_close_percents: List[float] = field(default_factory=lambda: [40.0, 20.0, 40.0])
    # [40%, 20%, 40%] means:
    # - TP1: close 40% of position (fixed take profit)
    # - TP2: close 20% of position (fixed take profit)
    # - Remaining 40%: trailing take profit (follows price)

    # Trailing Take Profit settings (for remaining position after TP2)
    trailing_tp_enabled: bool = True
    trailing_tp_distance_percent: float = 1.0  # 1% trailing distance from max price
    trailing_tp_min_profit_percent: float = 2.0  # Minimum profit (BE level, ~TP1)

    # Virtual balance & Position sizing (realistic paper trading)
    initial_balance: float = 10000.0  # Starting balance in USDT
    use_exchange_balance: bool = (
        False  # True = use initial_balance from exchange (don't load from DB)
    )
    risk_per_trade: float = 0.01  # Risk 1% of balance per trade
    use_compounding: bool = True  # Reinvest profits
    # Sizing balance cap — mirror of backtest risk_config.balance_cap (src/backtesting/models.py):
    #   cap >= 100 → DOLLAR mode: sizing balance = min(balance, cap)
    #   0 < cap < 100 → PERCENT mode (dynamic): sizing balance = balance * cap / 100
    #   cap == 0 → disabled. Only affects position sizing, not P&L accounting.
    sizing_balance_cap: float = 0.0
    min_position_size: float = 10.0  # Minimum position size in USDT
    max_position_percent: float = 0.25  # Max 25% of balance per position
    position_sizing_method: str = "fixed_balance"  # "fixed_fractional" or "fixed_balance"

    # Dynamic risk based on volatility
    dynamic_risk_enabled: bool = False
    dynamic_risk_ranging_multiplier: float = 0.5  # 50% risk in ranging/flat market
    dynamic_risk_trending_multiplier: float = 1.0  # 100% risk in trending market

    # Limit order trigger buffer (trigger fires early, limit at exact price)
    # This gives price "room" to reach the limit, reducing slippage
    # If buffer = 0.5%, SL at $100 will trigger at $100.50 (LONG) or $99.50 (SHORT)
    sl_trigger_buffer_percent: float = 0.3  # 0.3% buffer for trailing stop trigger
    dynamic_risk_high_vol_multiplier: float = 0.75  # 75% risk in high volatility
    dynamic_risk_btc_threshold: float = 0.015  # 1.5% - deviation from MA for trend detection

    # Daily profit target (auto-stop when reached)
    daily_profit_target_enabled: bool = False
    daily_profit_target_percent: float = 10.0  # Stop trading at +10% daily
    daily_target_mode: str = "stop_new"  # stop_new | close_profitable | close_all
    daily_profit_close_winners: bool = True  # Legacy: used when mode=close_profitable
    daily_profit_wait_losers_be: bool = True  # Legacy: used when mode=close_profitable

    # Smart Limit Entry settings (use limit orders for better fees)
    limit_entry_enabled: bool = True  # Enable smart limit entry (maker fee 0.02% vs taker 0.055%)
    limit_entry_timeout_seconds: int = 300  # 5 min timeout (signal becomes stale after ~1 candle)
    limit_entry_min_spread: float = (
        0.01  # Min spread % to use limit (below this = instant fill likely)
    )
    limit_entry_max_spread: float = 0.5  # Max spread % to use limit (above this = too risky)
    limit_entry_check_interval: float = 5.0  # Check pending orders every 5 seconds

    # Directional cooldown - prevent mass correlated entries
    # If N positions open in same direction, wait M minutes before opening more
    directional_cooldown_enabled: bool = False
    directional_cooldown_threshold: int = (
        3  # After this many same-direction positions, apply cooldown
    )
    directional_cooldown_minutes: int = (
        30  # Wait this many minutes before opening more in same direction
    )

    # DCA (Dollar Cost Averaging) - global settings (can be overridden per-signal from strategy)
    dca_enabled: bool = True  # Master switch for DCA
    dca_check_interval: float = 5.0  # Check DCA conditions every N seconds
    dca_min_balance_percent: float = 0.1  # Need at least 10% of balance available for DCA

    ai_validation_enabled: bool = False
    ai_api_key: str = ""
    ai_model: str = "claude-sonnet-4-20250514"
    ai_provider: str = "anthropic"  # "anthropic", "openai", "cli"


class SignalLifecycleManager:
    """
    Manages the complete lifecycle of trading signals.

    Flow:
    1. Signal Generation (from StrategyManager)
    2. Signal Validation (SignalValidator)
    3. Risk Evaluation (RiskManager)
    4. Signal Recording (SignalHistory + Database)
    5. Execution Simulation (paper trading)
    6. Position Monitoring (price tracking)
    7. Outcome Recording (when SL/TP hit)
    """

    def __init__(
        self,
        database: Optional[DatabaseManager] = None,
        config: Optional[SignalLifecycleConfig] = None,
        connector: Optional[Any] = None,
        strategy_manager: Optional[Any] = None,
        alert_manager: Optional[Any] = None,
    ):
        """
        Initialize SignalLifecycleManager.

        Args:
            database: Database manager for persistent storage
            config: Lifecycle configuration
            connector: Exchange connector for real order execution (None = paper trading)
            strategy_manager: Strategy manager for notify_trade_closed callbacks
            alert_manager: Optional AlertManager for critical Telegram notifications
                (e.g. batch-close partial failures). None means alerts-only-in-log.
        """
        self.database = database
        self.config = config or SignalLifecycleConfig()
        self.connector = connector  # For real order execution
        self.strategy_manager = strategy_manager  # For trade close notifications
        self.alert_manager = alert_manager  # For critical Telegram notifications

        # Write-Ahead Log for database fallback (ensures signals are not lost if DB unavailable)
        self.wal = SignalWAL(
            wal_dir="data/wal",
            max_retries=10,
            retry_interval=30.0,
        )

        # Signal components
        self.signal_validator = SignalValidator(
            {
                "min_confidence": self.config.min_confidence,
                "min_risk_reward": self.config.min_risk_reward,
                "max_signal_age": self.config.max_signal_age_seconds,
            }
        )
        self.signal_history = SignalHistory()

        # Tracked signals (for outcome monitoring)
        self.tracked_signals: Dict[str, TrackedSignal] = {}

        # Current prices (updated externally)
        self.current_prices: Dict[str, float] = {}
        self.current_highs: Dict[str, float] = {}
        self.current_lows: Dict[str, float] = {}

        # Locks for thread-safe operations (prevent race conditions)
        self._tracked_signals_lock = asyncio.Lock()  # Protects tracked_signals dict
        self._position_operation_locks: Dict[
            str, asyncio.Lock
        ] = {}  # Per-symbol locks for position operations

        # Break-Even Manager
        self.break_even_manager: Optional[BreakEvenManager] = None
        if self.config.break_even_enabled:
            be_config = BreakEvenConfig(
                enabled=True,
                activation_profit_percent=self.config.break_even_activation_percent,
                lock_profit_ratio=self.config.break_even_lock_profit_ratio,
                lock_profit_percent=self.config.break_even_lock_profit_percent,
                maker_fee_percent=self.config.break_even_maker_fee,
                taker_fee_percent=self.config.break_even_taker_fee,
                buffer_percent=self.config.break_even_buffer_percent,
                enable_reentry=self.config.reentry_enabled,
                reentry_price_offset_percent=self.config.reentry_price_offset_percent,
                max_reentries=self.config.reentry_max_count,
                reentry_expiry_seconds=self.config.reentry_expiry_seconds,
                # Re-entry improvements (B)
                reentry_require_better_price=self.config.reentry_require_better_price,
                reentry_min_improvement_percent=self.config.reentry_min_improvement_percent,
                reentry_use_limit_order=self.config.reentry_use_limit_order,
                min_signal_time_remaining_seconds=300,
                ignore_signal_expiry_for_reentry=True,  # Allow re-entry even after signal expired
            )
            self.break_even_manager = BreakEvenManager(be_config)

            # Set callbacks
            self.break_even_manager.on_be_activated = self._on_break_even_activated
            self.break_even_manager.on_be_triggered = self._on_break_even_triggered
            self.break_even_manager.on_reentry_created = self._on_reentry_created

        # Limit Order Manager (for smart entry with maker fees)
        self.limit_order_manager: Optional[LimitOrderManager] = None
        if self.config.limit_entry_enabled and self.connector:
            limit_config = {
                "limit_entry": {
                    "enabled": True,
                    "timeout_seconds": self.config.limit_entry_timeout_seconds,
                    "min_spread_percent": self.config.limit_entry_min_spread,
                    "max_spread_percent": self.config.limit_entry_max_spread,
                    "check_interval": self.config.limit_entry_check_interval,
                    "min_liquidity": 0.3,
                    "min_fill_prob": 0.5,
                    "price_offset": 0.02,
                }
            }
            self.limit_order_manager = LimitOrderManager(
                connector=self.connector,
                config=limit_config,
                on_order_filled=self._on_limit_order_filled,
                on_order_timeout=self._on_limit_order_timeout,
            )

        # Statistics
        self.stats = {
            "signals_received": 0,
            "signals_validated": 0,
            "signals_rejected": 0,
            "signals_executed": 0,
            "signals_closed": 0,
            "winners": 0,
            "losers": 0,
            "total_pnl": 0.0,  # Sum of P&L percentages (legacy)
            "break_even_activated": 0,
            "break_even_triggered": 0,
            "reentries_created": 0,
            "reentries_filled": 0,
            # Virtual balance tracking (global fallback)
            "initial_balance": self.config.initial_balance,
            "start_balance": self.config.initial_balance,  # Alias for session summary
            "current_balance": self.config.initial_balance,
            "total_pnl_usdt": 0.0,  # Actual P&L in USDT
            "max_balance": self.config.initial_balance,  # Peak balance
            "max_drawdown_usdt": 0.0,  # Max drawdown in USDT
            "max_drawdown_percent": 0.0,  # Max drawdown %
            # Best/worst trades for session summary
            "best_trade": None,  # {'symbol': str, 'pnl': float}
            "worst_trade": None,  # {'symbol': str, 'pnl': float}
        }

        # Per-strategy balance tracking (for independent strategy testing)
        # Format: {'strategy_name': {'balance': float, 'initial': float, 'pnl': float, ...}}
        self.strategy_balances: Dict[str, Dict[str, float]] = {}

        # Running state
        self.is_running = False
        self._monitoring_task: Optional[asyncio.Task] = None

        # Session tracking for balance persistence
        # Use TradingLogger's session_id for consistency across all components
        from src.monitoring.logger import TradingLogger

        self.session_id = TradingLogger._session_id or str(uuid.uuid4())[:8]

        # Daily profit target tracking
        self.daily_target_reached = False
        self.daily_target_reached_at: Optional[datetime] = None
        self.daily_start_balance: float = self.config.initial_balance
        self.daily_start_time: datetime = utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Telegram notification callbacks (set externally)
        self.on_signal_opened: Optional[callable] = None  # async callback(tracked_signal)
        self.on_partial_close: Optional[
            callable
        ] = None  # async callback(signal_id, tp_level, partial_info, new_sl)
        self.on_signal_closed: Optional[
            callable
        ] = None  # async callback(signal_id, exit_reason, total_pnl, roi, final_partial)

        # Data provider for dynamic risk calculation (set externally)
        self.data_provider = None

        # Cache for BTC trend (avoid too many API calls)
        self._btc_trend_cache: Optional[str] = None
        self._btc_deviation_cache: Optional[float] = None
        self._btc_cache_time: float = 0
        self._btc_cache_ttl: float = 60.0  # Cache for 60 seconds

        # Directional cooldown tracking
        # Records when threshold was reached per direction: {'BUY': datetime, 'SELL': datetime}
        self._directional_cooldown_until: Dict[str, datetime] = {}

        # Fan batch trailing state: {batch_id: {'trailing_active': bool, 'peak_pnl': float}}
        self._fan_batch_state: Dict[str, Dict[str, Any]] = {}
        self._is_candle_close_check: bool = False

        # Cache for exchange positions (to avoid duplicate position opening)
        self._exchange_positions_cache: Dict[str, Dict[str, Any]] = {}  # {symbol: {side: position}}
        self._exchange_positions_cache_time: float = 0
        self._exchange_positions_cache_ttl: float = 5.0  # Cache for 5 seconds

        # Position sync tracking
        self._last_position_sync: float = 0
        self._last_balance_sync: float = 0
        self._sync_iteration_counter: int = 0

        # Exchange balance tracking
        self._exchange_balance: Optional[float] = None
        self._exchange_balance_available: Optional[float] = None

        self.ai_validator: Optional["AISignalValidator"] = None
        if config.ai_validation_enabled and _AI_AVAILABLE:
            ai_config = AIConfig(
                provider=config.ai_provider,
                api_key=config.ai_api_key,
                model=config.ai_model or "claude-sonnet-4-20250514",
            )
            self.ai_validator = AISignalValidator(ai_config)
        elif config.ai_validation_enabled and not _AI_AVAILABLE:
            logger.warning(
                "ai_validation_enabled but src.ai not available — AI validation disabled"
            )

        logger.info(
            f"SignalLifecycleManager initialized (BE: {self.config.break_even_enabled}, "
            f"Reentry: {self.config.reentry_enabled})"
        )

    async def _fetch_candles(self, symbol: str, timeframe: str, limit: int = 100):
        if not self.database:
            return None

        try:
            from sqlalchemy import text

            def _sync_fetch():
                session = self.database.get_session()
                try:
                    query = text(
                        """
                        SELECT timestamp, open, high, low, close, volume
                        FROM market_data.candles
                        WHERE symbol = :symbol AND timeframe = :timeframe
                        ORDER BY timestamp DESC
                        LIMIT :limit
                    """
                    )
                    result = session.execute(
                        query, {"symbol": symbol, "timeframe": timeframe, "limit": limit}
                    )
                    return result.fetchall()
                finally:
                    session.close()

            rows = await asyncio.to_thread(_sync_fetch)

            if not rows:
                return None

            from src.models.candle import Candle

            candles = []
            for row in reversed(rows):
                ts = row[0]
                ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else int(ts)
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=ts_ms,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                )
            return candles

        except Exception as e:
            logger.error(f"Failed to fetch candles for {symbol}: {e}")
            return None

    def _get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        """
        Get or create a lock for a specific symbol.

        This ensures that operations on the same symbol are serialized,
        preventing race conditions when multiple async tasks try to
        modify the same position simultaneously.
        """
        if symbol not in self._position_operation_locks:
            self._position_operation_locks[symbol] = asyncio.Lock()
        return self._position_operation_locks[symbol]

    async def start(self) -> None:
        """Start the lifecycle manager."""
        if self.is_running:
            return

        # Load balance from database if available
        self._load_balance_from_db()

        self.is_running = True
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())

        # Start Limit Order Manager if enabled
        if self.limit_order_manager:
            await self.limit_order_manager.start()
            logger.info("LimitOrderManager started for smart entry")

        # Start WAL background sync and register callbacks
        self._register_wal_callbacks()
        await self.wal.start_background_sync()
        logger.info(f"WAL started: {self.wal.get_stats_str()}")

        # Save session start to database
        self._save_balance_to_db(event_type="SESSION_START")

        logger.info(
            f"SignalLifecycleManager started (session: {self.session_id}, "
            f"balance: ${self.stats['current_balance']:.2f})"
        )

    async def stop(self) -> None:
        """Stop the lifecycle manager."""
        self.is_running = False

        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass

        # Stop Limit Order Manager
        if self.limit_order_manager:
            await self.limit_order_manager.stop()
            logger.info("LimitOrderManager stopped")

        # Force sync pending WAL entries and stop background sync
        if self.wal:
            synced = await self.wal.force_sync()
            logger.info(f"WAL force sync: {synced} entries synced")
            await self.wal.stop_background_sync()
            logger.info(f"WAL stopped: {self.wal.get_stats_str()}")

        # Save final balance to database
        self._save_balance_to_db(event_type="SESSION_END")

        # Log final statistics
        stats = self.get_stats()
        win_rate = stats.get("win_rate", 0) * 100
        balance = stats.get("current_balance", 0)
        pnl = stats.get("total_pnl_usdt", 0)
        logger.info(
            f"SignalLifecycleManager stopped. "
            f"Signals: {stats.get('signals_received', 0)}, "
            f"Executed: {stats.get('signals_executed', 0)}, "
            f"Closed: {stats.get('signals_closed', 0)}, "
            f"Win Rate: {win_rate:.1f}%, "
            f"Balance: ${balance:.2f} (P&L: ${pnl:+.2f})"
        )

    def _register_wal_callbacks(self) -> None:
        """Register callbacks for WAL entry sync."""

        # Signal create callback
        async def sync_signal_create(data: Dict[str, Any]) -> bool:
            if not self.database:
                return False
            try:
                return self.database.save_signal(data)
            except Exception as e:
                logger.error(f"WAL sync signal_create failed: {e}")
                return False

        # Signal execution update callback
        async def sync_signal_execute(data: Dict[str, Any]) -> bool:
            if not self.database:
                return False
            try:
                return self.database.update_signal_execution(
                    signal_id=data["signal_id"],
                    executed_price=data["executed_price"],
                    executed_size=data["executed_size"],
                    order_id=data["order_id"],
                )
            except Exception as e:
                logger.error(f"WAL sync signal_execute failed: {e}")
                return False

        # Signal close callback
        async def sync_signal_close(data: Dict[str, Any]) -> bool:
            if not self.database:
                return False
            try:
                return self.database.update_signal_outcome(
                    signal_id=data["signal_id"],
                    exit_price=data["exit_price"],
                    exit_reason=data["exit_reason"],
                    pnl=data["pnl"],
                    pnl_percent=data["pnl_percent"],
                    fees=data.get("fees", 0.0),
                    max_profit_percent=data.get("max_profit_percent"),
                    open_fee=data.get("open_fee"),
                    close_fee=data.get("close_fee"),
                    funding_fee=data.get("funding_fee"),
                    exchange_pnl=data.get("exchange_pnl"),
                    net_pnl=data.get("net_pnl"),
                )
            except Exception as e:
                logger.error(f"WAL sync signal_close failed: {e}")
                return False

        # Order create callback
        async def sync_order_create(data: Dict[str, Any]) -> bool:
            if not self.database:
                return False
            try:
                return self.database.save_order(data)
            except Exception as e:
                logger.error(f"WAL sync order_create failed: {e}")
                return False

        # Order update callback
        async def sync_order_update(data: Dict[str, Any]) -> bool:
            if not self.database:
                return False
            try:
                if self.database.update_order_status(
                    signal_id=data["signal_id"],
                    order_type=data["order_type"],
                    status=data["status"],
                    exchange_order_id=data.get("exchange_order_id"),
                    filled_price=data.get("filled_price"),
                    filled_quantity=data.get("filled_quantity"),
                    commission=data.get("commission"),
                    status_reason=data.get("status_reason"),
                ):
                    return True
                # No order — nothing to update, and none will appear: on MT5 take-profits
                # and trailing live as position properties, no separate orders are
                # created for them. Retrying this 10 times is pointless.
                return not self.database.order_exists(data["signal_id"], data["order_type"])
            except Exception as e:
                logger.error(f"WAL sync order_update failed: {e}")
                return False

        # Register all callbacks
        self.wal.register_sync_callback(WALEntryType.SIGNAL_CREATE, sync_signal_create)
        self.wal.register_sync_callback(WALEntryType.SIGNAL_EXECUTE, sync_signal_execute)
        self.wal.register_sync_callback(WALEntryType.SIGNAL_CLOSE, sync_signal_close)
        self.wal.register_sync_callback(WALEntryType.ORDER_CREATE, sync_order_create)
        self.wal.register_sync_callback(WALEntryType.ORDER_UPDATE, sync_order_update)

        logger.debug("WAL sync callbacks registered")

    # ==================== Exchange Position Check ====================

    async def _get_exchange_positions(self) -> Dict[str, Dict[str, Any]]:
        """
        Get current positions from exchange with caching.

        Returns:
            Dict mapping (symbol, side) to position info
            Format: {'BTCUSDT': {'BUY': {...}, 'SELL': {...}}}
        """
        import time

        # Check cache validity
        now = time.time()
        if (now - self._exchange_positions_cache_time) < self._exchange_positions_cache_ttl:
            return self._exchange_positions_cache

        # No connector = paper trading, no exchange positions
        if not self.connector:
            return {}

        try:
            positions = await self.connector.get_positions()

            # Build cache: {symbol: {side: position}}
            cache = {}
            for pos in positions:
                size = pos.get("size", 0)
                if size > 0:  # Only count open positions
                    symbol = pos.get("symbol", "")
                    # Normalize side: "Buy" -> "BUY", "Sell" -> "SELL"
                    raw_side = pos.get("side", "").upper()
                    side = "BUY" if raw_side in ("BUY", "LONG") else "SELL"

                    if symbol not in cache:
                        cache[symbol] = {}
                    cache[symbol][side] = pos

            self._exchange_positions_cache = cache
            self._exchange_positions_cache_time = now

            return cache

        except Exception as e:
            logger.warning(f"Failed to get exchange positions: {e}")
            # Return stale cache only if it's less than 30s old, otherwise empty
            cache_age = now - self._exchange_positions_cache_time
            if cache_age < 30.0:
                return self._exchange_positions_cache
            logger.warning(
                f"Exchange positions cache too stale ({cache_age:.0f}s), returning empty"
            )
            return {}

    async def _has_exchange_position(self, symbol: str, side: str) -> bool:
        """
        Check if there's already an open position on exchange for symbol/side.

        This prevents opening duplicate positions when:
        - Bot restarts and positions weren't recovered to tracked_signals
        - External positions exist from previous sessions

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            side: "BUY" or "SELL"

        Returns:
            True if position exists on exchange
        """
        positions = await self._get_exchange_positions()
        symbol_positions = positions.get(symbol, {})
        return side in symbol_positions

    def _exceeds_strategy_position_cap(
        self,
        symbol: str,
        strategy_name: str,
        metadata: Optional[Dict[str, Any]],
        is_fan_batch: bool,
    ) -> bool:
        """Per-strategy position cap counted ACROSS BOTH SIDES.

        Parity with the backtest engine, which counts a strategy's own positions
        on the symbol regardless of direction and caps them by `max_positions`
        (see backtest_engine._run_strategy). Without this, under hedge_mode a
        strategy with max_positions=1 can hold a long and a short at the same
        time in live while the backtest allows only one of them. Measured on a
        trend strategy over 15 months: 224 opposing pairs, 312 -> 472 trades,
        mean R per trade halved.

        The rule only ever TIGHTENS the existing gate: same-side duplicates are
        already rejected by the caller, fan/batch signals are exempt so grid
        strategies keep both legs, and a missing `max_positions` in metadata
        leaves behaviour unchanged.

        Returns:
            True if the signal must be rejected.
        """
        if is_fan_batch:
            return False

        strategy_max = (metadata or {}).get("max_positions")
        if not strategy_max:
            return False

        try:
            strategy_max = int(strategy_max)
        except (TypeError, ValueError):
            return False
        if strategy_max <= 0:
            return False

        own_open = sum(
            1
            for s in self.tracked_signals.values()
            if s.symbol == symbol and s.status == "EXECUTED" and s.strategy_name == strategy_name
        )
        if own_open + 1 > strategy_max:
            logger.debug(
                f"Signal rejected: {strategy_name} already holds {own_open} "
                f"position(s) for {symbol} across both sides "
                f"(max_positions={strategy_max})"
            )
            return True
        return False

    def invalidate_position_cache(self) -> None:
        """Force refresh of exchange positions cache on next check."""
        self._exchange_positions_cache_time = 0

    # ==================== Daily Profit Target ====================

    def _check_daily_reset(self) -> None:
        """Reset daily tracking at midnight UTC."""
        now = utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if today_start > self.daily_start_time:
            # New day - reset tracking
            self.daily_start_time = today_start
            self.daily_start_balance = self.stats["current_balance"]
            self.daily_target_reached = False
            self.daily_target_reached_at = None

            # Save daily reset to database
            self._save_balance_to_db(event_type="DAILY_RESET")

            logger.info(
                f"📅 New trading day started. Daily balance reset to ${self.daily_start_balance:.2f}"
            )

    def get_daily_pnl_percent(self) -> float:
        """Get current daily P&L as percentage."""
        if self.daily_start_balance <= 0:
            return 0.0
        return (
            (self.stats["current_balance"] - self.daily_start_balance) / self.daily_start_balance
        ) * 100

    def _check_daily_target(self) -> bool:
        """
        Check if daily profit target has been reached.

        Returns:
            True if target reached (and wasn't reached before)
        """
        if not self.config.daily_profit_target_enabled:
            return False

        if self.daily_target_reached:
            return False  # Already handled

        daily_pnl = self.get_daily_pnl_percent()

        if daily_pnl >= self.config.daily_profit_target_percent:
            self.daily_target_reached = True
            self.daily_target_reached_at = utcnow()
            logger.info(
                f"{SYS.HEALTH} DAILY TARGET +{daily_pnl:.1f}% (target {self.config.daily_profit_target_percent}%)"
            )
            return True

        return False

    async def _handle_daily_target_reached(self) -> None:
        """Handle daily target reached based on configured mode."""
        if not self.daily_target_reached:
            return

        mode = self.config.daily_target_mode

        if mode == "stop_new":
            open_count = sum(1 for t in self.tracked_signals.values() if t.status == "EXECUTED")
            logger.info(
                f"🎯 Daily target reached (mode: stop_new). "
                f"Blocking new entries, {open_count} positions continue."
            )
            return

        open_positions = [
            (sig_id, tracked)
            for sig_id, tracked in self.tracked_signals.items()
            if tracked.status == "EXECUTED"
        ]

        if not open_positions:
            logger.info("🎯 Daily target reached. No open positions to close.")
            return

        logger.info(
            f"🎯 Daily target reached (mode: {mode}). Processing {len(open_positions)} positions..."
        )

        for signal_id, tracked in open_positions:
            current_price = self.current_prices.get(tracked.symbol)
            if not current_price:
                continue

            if tracked.side == "BUY":
                unrealized_pnl_pct = (
                    (current_price - tracked.executed_price) / tracked.executed_price
                ) * 100
            else:
                unrealized_pnl_pct = (
                    (tracked.executed_price - current_price) / tracked.executed_price
                ) * 100

            if mode == "close_all":
                logger.info(
                    f"🎯 Closing {tracked.side} {tracked.symbol} "
                    f"(P&L: {unrealized_pnl_pct:+.2f}%) - daily target close_all"
                )
                await self._close_position(signal_id, tracked, current_price, "DAILY_TARGET")

            elif mode == "close_profitable":
                if unrealized_pnl_pct > 0:
                    logger.info(
                        f"🎯 Closing PROFITABLE {tracked.side} {tracked.symbol} "
                        f"(P&L: +{unrealized_pnl_pct:.2f}%) - daily target"
                    )
                    await self._close_position(
                        signal_id, tracked, current_price, "DAILY_TARGET_PROFIT"
                    )
                else:
                    logger.info(
                        f"🎯 Keeping LOSING {tracked.side} {tracked.symbol} "
                        f"(P&L: {unrealized_pnl_pct:.2f}%) - waiting for SL/BE"
                    )

    async def _cancel_related_orders(self, tracked: TrackedSignal) -> int:
        """
        Cancel all orders related to a position (SL, TP, trailing).

        This should be called when a position is fully closed to clean up
        any remaining orders that are no longer needed.

        Args:
            tracked: The tracked signal with order IDs

        Returns:
            Number of orders successfully cancelled
        """
        if not self.connector:
            return 0

        cancelled = 0
        orders_to_cancel = []

        # Cancel SL via trading-stop API
        if tracked.sl_order_id:
            try:
                position_idx = 1 if tracked.side == "BUY" else 2
                sl_clear_kwargs = {
                    "symbol": tracked.symbol,
                    "position_idx": position_idx,
                    "stop_loss": 0,
                }
                if tracked.position_ticket:
                    sl_clear_kwargs["position_ticket"] = tracked.position_ticket
                result = await self.connector.set_trading_stop(**sl_clear_kwargs)
                if result.get("success"):
                    cancelled += 1
                    logger.debug(f"  Cleared trading-stop SL for {tracked.symbol}")
                    await self._update_order_status_in_db(
                        signal_id=tracked.signal_id,
                        order_type="STOP_LOSS",
                        status="CANCELLED",
                        status_reason="Position closed - cleanup",
                    )
                else:
                    error = result.get("error", "Unknown")
                    if "not exist" in str(error).lower() or "not found" in str(error).lower():
                        logger.debug(f"  SL already gone for {tracked.symbol}")
                    else:
                        logger.warning(f"  Failed to clear SL: {error}")
            except Exception as e:
                logger.warning(f"  Error clearing SL: {e}")

        # Collect TP and trailing order IDs
        for i, tp_id in enumerate(tracked.tp_order_ids or []):
            if tp_id:
                orders_to_cancel.append((f"TP{i+1}", tp_id))

        if tracked.trailing_order_id:
            orders_to_cancel.append(("TRAILING", tracked.trailing_order_id))

        if orders_to_cancel:
            logger.debug(
                f"Cancelling {len(orders_to_cancel)} TP/trailing orders for {tracked.symbol}"
            )

        for order_type, order_id in orders_to_cancel:
            try:
                result = await self.connector.cancel_order(
                    {"symbol": tracked.symbol, "order_id": order_id}
                )

                db_order_type = order_type
                if order_type.startswith("TP"):
                    db_order_type = f"TAKE_PROFIT_{order_type[2:]}"
                elif order_type == "TRAILING":
                    db_order_type = "TRAILING_STOP"

                if result.get("success"):
                    cancelled += 1
                    logger.debug(f"  Cancelled {order_type} order {order_id[:8]}...")
                    await self._update_order_status_in_db(
                        signal_id=tracked.signal_id,
                        order_type=db_order_type,
                        status="CANCELLED",
                        status_reason="Position closed - cleanup",
                    )
                else:
                    error = result.get("error", "Unknown")
                    if "not exist" in str(error).lower() or "not found" in str(error).lower():
                        logger.debug(f"  {order_type} order {order_id[:8]}... already gone")
                    else:
                        logger.warning(f"  Failed to cancel {order_type} order: {error}")

            except Exception as e:
                logger.warning(f"  Error cancelling {order_type} order: {e}")

        if cancelled > 0:
            logger.info(f"🧹 Cancelled {cancelled} related orders for {tracked.symbol}")

        return cancelled

    async def _close_position(
        self, signal_id: str, tracked: TrackedSignal, exit_price: float, exit_reason: str
    ) -> None:
        """Force close a position."""
        # Cancel related orders (SL/TP) since position is closing
        await self._cancel_related_orders(tracked)

        # Calculate final P&L
        if tracked.side == "BUY":
            pnl_percent = ((exit_price - tracked.executed_price) / tracked.executed_price) * 100
        else:
            pnl_percent = ((tracked.executed_price - exit_price) / tracked.executed_price) * 100

        # Note: leverage is NOT applied here because position_size_usdt is the notional value
        # (already leveraged). Applying leverage again would double-count it.
        pnl_usdt = tracked.position_size_usdt * (pnl_percent / 100)

        # Update tracked signal
        tracked.status = "CLOSED"
        tracked.closed_at = utcnow()
        tracked.exit_price = exit_price
        tracked.exit_reason = exit_reason
        tracked.pnl_percent = pnl_percent
        tracked.pnl = pnl_usdt

        # Update balance
        is_winner = pnl_usdt > 0
        self._update_balance(
            pnl_usdt=pnl_usdt,
            strategy_name=tracked.strategy_name,
            strategy_balance=tracked.strategy_balance,
            is_winner=is_winner,
        )

        # Update stats
        self.stats["signals_closed"] += 1
        if is_winner:
            self.stats["winners"] += 1
        else:
            self.stats["losers"] += 1
        self.stats["total_pnl_usdt"] += pnl_usdt

        # Save balance to database after trade close
        self._save_balance_to_db(
            event_type="TRADE_CLOSE", trade_symbol=tracked.symbol, trade_pnl=pnl_usdt
        )

        # Remove from BE manager if exists
        if self.break_even_manager and signal_id in self.break_even_manager.positions:
            del self.break_even_manager.positions[signal_id]

        logger.info(
            f"{P.CLOSE} {tracked.side} {tracked.symbol} {pnl_percent:+.1f}% ${pnl_usdt:+.1f} | {exit_reason}"
        )

        # Notify via callback
        if self.on_signal_closed:
            try:
                await self.on_signal_closed(
                    signal_id,
                    exit_reason,
                    tracked.realized_pnl_usdt + pnl_usdt,
                    pnl_percent,
                    {"pnl_percent": pnl_percent, "pnl_usdt": pnl_usdt, "exit_price": exit_price},
                )
            except Exception as e:
                logger.error(f"Error in on_signal_closed callback: {e}")

    async def close_position_by_id(
        self, signal_id: str, exit_price: float, exit_reason: str
    ) -> bool:
        """
        Public method to close a position by signal ID.
        Used by Grid-Hedge and other external modules.

        Args:
            signal_id: The signal/level ID to close
            exit_price: Exit price
            exit_reason: Reason for closing (TAKE_PROFIT, STOP_LOSS, REBALANCE, etc.)

        Returns:
            True if position was closed, False if not found
        """
        if signal_id not in self.tracked_signals:
            logger.warning(f"Cannot close position {signal_id}: not found in tracked signals")
            return False

        tracked = self.tracked_signals[signal_id]

        if tracked.status == "CLOSED":
            logger.debug(f"Position {signal_id} already closed")
            return False

        # Acquire per-symbol lock to prevent race conditions
        symbol_lock = self._get_symbol_lock(tracked.symbol)
        async with symbol_lock:
            # Re-check after acquiring lock (double-check pattern)
            if tracked.status == "CLOSED":
                return False
            await self._close_position(signal_id, tracked, exit_price, exit_reason)
        return True

    # ==================== Signal Processing ====================

    async def process_signal(
        self, signal: Any, market_data: Optional[Dict[str, Any]] = None
    ) -> Optional[TrackedSignal]:
        """
        Process a new signal through the full lifecycle.

        Args:
            signal: Signal from strategy
            market_data: Current market data for validation

        Returns:
            TrackedSignal if signal was accepted, None otherwise
        """
        self.stats["signals_received"] += 1

        # Check if daily profit target is reached - reject new signals
        if self.daily_target_reached:
            logger.debug("Signal rejected: daily profit target already reached")
            self.stats["signals_rejected"] += 1
            return None

        try:
            # Extract signal data
            signal_id = (
                getattr(signal, "id", None) or f"SIG_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            )
            symbol = signal.symbol
            side = signal.side
            entry_price = signal.entry_price
            stop_loss = signal.stop_loss
            take_profit = (
                signal.take_profit if isinstance(signal.take_profit, list) else [signal.take_profit]
            )
            confidence = signal.confidence
            strategy_name = signal.strategy_name

            # Get per-strategy balance from signal metadata (if set)
            metadata = getattr(signal, "metadata", {}) or {}
            strategy_balance = metadata.get("account_balance", 0.0)

            # Acquire per-symbol lock to prevent race conditions
            symbol_lock = self._get_symbol_lock(symbol)
            async with symbol_lock:
                return await self._process_signal_locked(
                    signal,
                    signal_id,
                    symbol,
                    side,
                    entry_price,
                    stop_loss,
                    take_profit,
                    confidence,
                    strategy_name,
                    strategy_balance,
                    market_data,
                    metadata,
                )

        except Exception as e:
            logger.error(f"Error processing signal: {e}")
            self.stats["signals_rejected"] += 1
            return None

    async def _process_signal_locked(
        self,
        signal: Any,
        signal_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: List[float],
        confidence: float,
        strategy_name: str,
        strategy_balance: float,
        market_data: Optional[Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> Optional["TrackedSignal"]:
        """
        Process signal with lock held. This method should only be called
        from process_signal() with the symbol lock acquired.
        """
        try:
            # 1. Check if we already have an open position for this symbol (in tracked_signals)
            # Fan/grid strategies use batch metadata to allow multiple same-side positions
            is_fan_batch = bool(
                metadata and (metadata.get("fan_batch_id") or metadata.get("batch_id"))
            )

            if self.config.hedge_mode:
                # Hedge mode: allow long + short, and allow different strategies
                # to hold same-side positions on the same symbol independently.
                # Block only duplicates from the SAME strategy (fan/batch excepted).
                existing_same = [
                    s
                    for s in self.tracked_signals.values()
                    if s.symbol == symbol
                    and s.status == "EXECUTED"
                    and s.side == side
                    and s.strategy_name == strategy_name
                ]
                if existing_same and not is_fan_batch:
                    logger.debug(
                        f"Signal rejected: {strategy_name} already has {side} position for {symbol} (hedge mode)"
                    )
                    self.stats["signals_rejected"] += 1
                    return None
                elif existing_same and is_fan_batch:
                    # Fan/batch: enforce max_positions limit from risk config
                    max_pos = self.config.max_positions or 20
                    total_open = sum(
                        1 for s in self.tracked_signals.values() if s.status == "EXECUTED"
                    )
                    if total_open >= max_pos:
                        logger.warning(
                            f"Signal rejected: max positions limit reached ({total_open}/{max_pos})"
                        )
                        self.stats["signals_rejected"] += 1
                        return None
                    logger.debug(
                        f"Fan/batch signal allowed: {len(existing_same)} existing {side} for {symbol} ({total_open}/{max_pos} total)"
                    )

                if self._exceeds_strategy_position_cap(
                    symbol, strategy_name, metadata, is_fan_batch
                ):
                    self.stats["signals_rejected"] += 1
                    return None
            else:
                # Normal mode: one position per (symbol, strategy).
                # Different strategies may still run in parallel on the same symbol.
                existing_same = [
                    s
                    for s in self.tracked_signals.values()
                    if s.symbol == symbol
                    and s.status == "EXECUTED"
                    and s.strategy_name == strategy_name
                ]
                if existing_same and not is_fan_batch:
                    logger.debug(
                        f"Signal rejected: {strategy_name} already has {len(existing_same)} open position(s) for {symbol}"
                    )
                    self.stats["signals_rejected"] += 1
                    return None
                elif existing_same and is_fan_batch:
                    max_pos = self.config.max_positions or 20
                    total_open = sum(
                        1 for s in self.tracked_signals.values() if s.status == "EXECUTED"
                    )
                    if total_open >= max_pos:
                        logger.warning(
                            f"Signal rejected: max positions limit reached ({total_open}/{max_pos})"
                        )
                        self.stats["signals_rejected"] += 1
                        return None

            # 1b. CRITICAL: Also check exchange for existing positions (prevents duplicates after restart)
            # This catches positions that exist on exchange but weren't recovered to tracked_signals
            # Skip for fan/batch strategies — they intentionally open multiple positions.
            # Skip if we already track a position on this symbol/side from any strategy —
            # the exchange position is our own and new strategies may legitimately join it.
            if (
                self.connector and not is_fan_batch
            ):  # Only check if we have a real connector (not paper trading)
                if self.config.hedge_mode:
                    # Hedge mode: check same side only
                    already_tracked = any(
                        s.symbol == symbol and s.status == "EXECUTED" and s.side == side
                        for s in self.tracked_signals.values()
                    )
                    if not already_tracked and await self._has_exchange_position(symbol, side):
                        logger.warning(
                            f"Signal rejected: {side} {symbol} - position already exists on exchange "
                            f"(not in tracked_signals, possibly from previous session)"
                        )
                        self.stats["signals_rejected"] += 1
                        return None
                else:
                    # Normal mode: check both sides
                    already_tracked = any(
                        s.symbol == symbol and s.status == "EXECUTED"
                        for s in self.tracked_signals.values()
                    )
                    if not already_tracked and (
                        await self._has_exchange_position(symbol, "BUY")
                        or await self._has_exchange_position(symbol, "SELL")
                    ):
                        logger.warning(
                            f"Signal rejected: {symbol} - position already exists on exchange "
                            f"(not in tracked_signals, possibly from previous session)"
                        )
                        self.stats["signals_rejected"] += 1
                        return None

            # 1c. Directional cooldown - prevent mass correlated entries
            if self.config.directional_cooldown_enabled:
                # Count open positions in the same direction
                same_direction_count = sum(
                    1
                    for s in self.tracked_signals.values()
                    if s.status == "EXECUTED" and s.side == side
                )

                # If at/above threshold, check if cooldown has expired
                if same_direction_count >= self.config.directional_cooldown_threshold:
                    cooldown_until = self._directional_cooldown_until.get(side)
                    now = utcnow()

                    if cooldown_until is None:
                        # First time hitting threshold — start cooldown
                        self._directional_cooldown_until[side] = now + timedelta(
                            minutes=self.config.directional_cooldown_minutes
                        )
                        logger.info(
                            f"{S.BLOCK} {side} cooldown started: {same_direction_count} positions open, "
                            f"waiting {self.config.directional_cooldown_minutes}min before new {side}"
                        )
                        self.stats["signals_rejected"] += 1
                        return None
                    elif now < cooldown_until:
                        remaining = (cooldown_until - now).total_seconds() / 60
                        logger.debug(
                            f"{S.BLOCK} {side} {symbol} rejected: cooldown active, "
                            f"{remaining:.1f}min remaining ({same_direction_count} {side} positions)"
                        )
                        self.stats["signals_rejected"] += 1
                        return None
                    else:
                        # Cooldown expired — allow and reset
                        logger.info(
                            f"{S.BLOCK} {side} cooldown expired, allowing new {side} position"
                        )
                        self._directional_cooldown_until.pop(side, None)
                else:
                    # Below threshold — clear any stale cooldown
                    self._directional_cooldown_until.pop(side, None)

            # Track if AI adjusted entry price (for limit orders)
            ai_adjusted_entry = None

            if self.ai_validator:
                # Get signal type as string (BUY/SELL)
                sig_type = signal.side if hasattr(signal, "side") else signal.type.value

                # Fetch candles for AI validation (20 candles)
                timeframe = getattr(signal, "timeframe", "15m") or "15m"
                candles = await self._fetch_candles(symbol, timeframe, limit=20)
                candles_data = []
                if candles:
                    for c in candles:
                        candles_data.append(
                            {
                                "open": float(c.open),
                                "high": float(c.high),
                                "low": float(c.low),
                                "close": float(c.close),
                                "volume": float(c.volume),
                            }
                        )

                ai_request = AIRequest(
                    signal_type=sig_type,
                    symbol=signal.symbol,
                    entry_price=float(entry_price),
                    stop_loss=float(stop_loss),
                    take_profit=[float(tp) for tp in take_profit] if take_profit else [],
                    confidence=confidence,
                    indicators=getattr(signal, "indicators", {}) or {},
                    candles=candles_data,
                    metadata={**(getattr(signal, "metadata", {}) or {}), "timeframe": timeframe},
                )
                validation = await self.ai_validator.validate(ai_request)

                if not validation.is_valid:
                    reason = validation.errors[0] if validation.errors else "AI validation failed"
                    logger.info(f"Signal rejected by AI: {reason}")
                    self.stats["signals_rejected"] += 1
                    await self._save_signal_to_db(
                        signal_id,
                        signal,
                        status="REJECTED",
                        metadata={"reason": f"AI rejected: {reason}"},
                    )
                    return None

                if validation.ai_response:
                    if validation.ai_response.adjusted_entry:
                        ai_adjusted_entry = validation.ai_response.adjusted_entry
                        entry_price = ai_adjusted_entry
                        logger.info(f"[AI] Adjusted entry: {entry_price}")
                    if validation.ai_response.adjusted_stop_loss:
                        stop_loss = validation.ai_response.adjusted_stop_loss
                        logger.info(f"[AI] Adjusted SL: {stop_loss}")
                    if validation.ai_response.adjusted_take_profit:
                        take_profit = validation.ai_response.adjusted_take_profit
                        logger.info(f"[AI] Adjusted TP: {take_profit}")

            # 2. Basic validation
            if confidence < self.config.min_confidence:
                logger.debug(
                    f"Signal rejected: confidence {confidence:.2f} < {self.config.min_confidence}"
                )
                self.stats["signals_rejected"] += 1
                await self._save_signal_to_db(
                    signal_id, signal, status="REJECTED", metadata={"reason": "Low confidence"}
                )
                return None

            # 3. Calculate risk/reward
            # Validate required prices before calculation
            if entry_price is None or stop_loss is None:
                logger.warning(
                    f"Signal rejected: missing entry_price ({entry_price}) or stop_loss ({stop_loss})"
                )
                self.stats["signals_rejected"] += 1
                await self._save_signal_to_db(
                    signal_id,
                    signal,
                    status="REJECTED",
                    metadata={"reason": "Missing entry_price or stop_loss"},
                )
                return None

            # Batch close strategies (grid/fan) have no individual TP — skip TP/RR checks
            has_batch_close = (
                metadata.get("fan_batch_id") or metadata.get("batch_id") if metadata else False
            )

            if not has_batch_close:
                if not take_profit or take_profit[0] is None:
                    logger.warning("Signal rejected: missing take_profit")
                    self.stats["signals_rejected"] += 1
                    await self._save_signal_to_db(
                        signal_id,
                        signal,
                        status="REJECTED",
                        metadata={"reason": "Missing take_profit"},
                    )
                    return None

            if side == "BUY":
                risk = entry_price - stop_loss
                reward = take_profit[0] - entry_price if take_profit else 0
            else:  # SELL
                risk = stop_loss - entry_price
                reward = entry_price - take_profit[0] if take_profit else 0

            risk_reward = reward / risk if risk > 0 else 0

            if not has_batch_close and risk_reward < self.config.min_risk_reward:
                logger.debug(
                    f"Signal rejected: R:R {risk_reward:.2f} < {self.config.min_risk_reward}"
                )
                self.stats["signals_rejected"] += 1
                await self._save_signal_to_db(
                    signal_id,
                    signal,
                    status="REJECTED",
                    metadata={"reason": f"Low R:R ratio: {risk_reward:.2f}"},
                )
                return None

            self.stats["signals_validated"] += 1

            # 3. Create tracked signal
            tracked = TrackedSignal(
                signal=signal,
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                created_at=utcnow(),
                ai_adjusted_entry=ai_adjusted_entry,
                strategy_name=strategy_name,
                strategy_balance=strategy_balance,
                # DCA fields from signal metadata (set by strategy)
                dca_enabled=metadata.get("dca_enabled", False),
                dca_level=0,
                dca_max_levels=metadata.get("dca_max_levels", 4),
                dca_threshold=metadata.get("dca_threshold", 0.8),
                dca_size_multiplier=metadata.get("dca_add_size_pct", 0.5)
                + 1.0,  # Convert add % to multiplier
                dca_recalc_tp=metadata.get("dca_recalc_sl_tp", True),
                dca_sl_tighten_factor=metadata.get("dca_sl_tighten_factor", 0.8),
                dca_use_trend_filter=metadata.get("dca_use_trend_filter", False),
                dca_rsi_filter=metadata.get("dca_rsi_filter", False),
                dca_rsi_extreme_threshold=metadata.get("dca_rsi_extreme_threshold", 25.0),
                dca_require_retracement=metadata.get("dca_require_retracement", False),
                original_entry_price=entry_price,
                original_stop_loss=stop_loss,
                entry_rsi=metadata.get("entry_rsi", 50.0),
            )

            # Initialize strategy balance tracking if this is a new strategy
            if strategy_name and strategy_balance > 0:
                self._init_strategy_balance(strategy_name, strategy_balance)

            # 4. Execute order (paper or real)
            if self.config.simulate_execution:
                await self._simulate_execution(tracked)
            else:
                # Real execution via connector
                success = await self._real_execution(tracked)
                if not success:
                    logger.error(f"Failed to execute real order for {signal_id}")
                    return

            # 5. Add to tracking
            self.tracked_signals[signal_id] = tracked

            # 6. Record in signal history
            self.signal_history.add_signal(signal)

            # 7. Save to database
            await self._save_signal_to_db(
                signal_id, signal, status="EXECUTED", executed_price=tracked.executed_price
            )

            # 8. Register in Break-Even manager
            #
            # Breakeven parameters come from the signal metadata — the same way the
            # backtest reads them. Live used to register EVERY position and apply
            # the global config threshold, ignoring use_breakeven and the other
            # fields. As a result, a strategy that never asks for breakeven was
            # closed at +1% instead of running to its TP.
            be_meta = getattr(signal, "metadata", None) or {}
            if (
                self.break_even_manager
                and tracked.executed_price
                and be_meta.get("use_breakeven", True)
            ):
                position_side = "LONG" if side == "BUY" else "SHORT"
                signal_expiry = utcnow() + timedelta(seconds=self.config.signal_expiry_seconds)

                self.break_even_manager.register_position(
                    position_id=signal_id,
                    symbol=symbol,
                    side=position_side,
                    entry_price=tracked.executed_price,
                    quantity=1.0,  # Paper trading - nominal quantity
                    signal_id=signal_id,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    signal_expiry=signal_expiry,
                    activation_percent=be_meta.get("breakeven_activation_percent"),
                    lock_profit_ratio=be_meta.get("breakeven_lock_profit_ratio"),
                )

            # 9. Limit tracked signals
            self._cleanup_old_signals()

            # Calculate potential P&L for logging (only if order was filled)
            # Note: no leverage multiplier - position_size_usdt is the notional value
            pos_size = tracked.position_size_usdt
            entry = tracked.executed_price

            # Skip P&L logging for pending limit orders (entry not yet known)
            if entry is None:
                # Limit order still pending - logged in _execute_with_limit_order
                return

            # Filter out None values from take_profit
            valid_tps = [tp for tp in take_profit if tp is not None]

            if side == "BUY":
                sl_pnl = pos_size * ((stop_loss - entry) / entry) if stop_loss else 0
                tp_pnls = [pos_size * ((tp - entry) / entry) for tp in valid_tps]
            else:
                sl_pnl = pos_size * ((entry - stop_loss) / entry) if stop_loss else 0
                tp_pnls = [pos_size * ((entry - tp) / entry) for tp in valid_tps]

            # Format TP levels with potential profit
            tp_info = " | ".join(
                [f"TP{i+1}: ${tp_pnls[i]:+.2f}" for i in range(min(3, len(tp_pnls)))]
            )

            logger.info(f"{S.EXEC} {side} {symbol} @ {entry:.4f}", extra={"log_type": "signal"})
            # Use the risk % ACTUALLY applied: a strategy may override it via
            # metadata['risk_per_trade'] (see _calculate_position_size). The global
            # config.risk_per_trade used to be printed here, so a line like
            # "Risk: $240.00 (2.0%)" on a $10k balance looked like a broken balance_cap,
            # while it was really 3% of the correctly capped $8k base.
            _eff_risk = (metadata or {}).get("risk_per_trade") or self.config.risk_per_trade
            _cap = self.config.sizing_balance_cap
            _cap_note = (
                f", cap {_cap:g}%"
                if 0 < _cap < 100
                else (f", cap ${_cap:g}" if _cap >= 100 else "")
            )
            logger.info(
                f"   Position: ${pos_size:.2f} | Leverage: {self.config.leverage}x | "
                f"Risk: ${tracked.risk_amount_usdt:.2f} ({float(_eff_risk)*100:.1f}%{_cap_note})",
                extra={"log_type": "signal"},
            )
            logger.info(
                f"   SL: {stop_loss:.4f} (${sl_pnl:.2f}) | {tp_info}", extra={"log_type": "signal"}
            )
            logger.info(
                f"   R:R: {risk_reward:.2f} | Balance: ${self.stats['current_balance']:.2f}",
                extra={"log_type": "signal"},
            )

            # Notify via Telegram (if callback set)
            if self.on_signal_opened:
                try:
                    await self.on_signal_opened(tracked)
                except Exception as e:
                    logger.error(f"Error in on_signal_opened callback: {e}")

            return tracked

        except Exception as e:
            logger.error(f"Error processing signal: {e}", exc_info=True)
            self.stats["signals_rejected"] += 1
            return None

    async def _simulate_execution(self, tracked: TrackedSignal) -> None:
        """Simulate order execution with slippage and position sizing."""
        # Apply slippage
        slippage = tracked.entry_price * (self.config.slippage_percent / 100)

        if tracked.side == "BUY":
            tracked.executed_price = tracked.entry_price + slippage
        else:
            tracked.executed_price = tracked.entry_price - slippage

        # Get dynamic risk multiplier based on current market volatility
        risk_multiplier = await self._get_volatility_risk_multiplier()

        # Calculate position size based on risk (using per-strategy balance if available)
        (
            tracked.position_size_usdt,
            tracked.risk_amount_usdt,
            adjusted_sl,
        ) = self._calculate_position_size(
            entry_price=tracked.executed_price,
            stop_loss=tracked.stop_loss,
            side=tracked.side,
            strategy_name=tracked.strategy_name,
            strategy_balance=tracked.strategy_balance,
            risk_multiplier=risk_multiplier,
            signal_metadata=tracked.signal.metadata if tracked.signal else None,
        )

        # Update stop loss if it was adjusted (too tight)
        if adjusted_sl != tracked.stop_loss:
            logger.info(
                f"[SIM] {tracked.symbol} SL adjusted: {tracked.stop_loss:.6f} -> {adjusted_sl:.6f}"
            )
            tracked.stop_loss = adjusted_sl
            tracked.original_stop_loss = adjusted_sl  # Update original SL for DCA

        # Calculate and store initial quantity for DCA (account for contract_size)
        from src.core.trading_pairs import trading_pairs as tp_registry

        cs = tp_registry.get_contract_size(tracked.symbol)
        tracked.initial_quantity = tracked.position_size_usdt / (
            tp_registry.usd_price(tracked.symbol, tracked.executed_price) * cs
        )

        tracked.executed_at = utcnow()
        tracked.status = "EXECUTED"
        self.stats["signals_executed"] += 1

    async def _real_execution(self, tracked: TrackedSignal) -> bool:
        """
        Execute real order on exchange via connector.

        Supports two modes:
        1. Smart Limit Entry (if limit_order_manager enabled) - uses order book analysis
           for optimal limit price, falls back to market on timeout
        2. Direct Market Order (default) - immediate execution

        Args:
            tracked: Signal to execute

        Returns:
            True if order placed successfully, False otherwise
        """
        if not self.connector:
            logger.error("No connector available for real execution")
            return False

        try:
            # Get dynamic risk multiplier
            risk_multiplier = await self._get_volatility_risk_multiplier()

            # Calculate position size
            (
                tracked.position_size_usdt,
                tracked.risk_amount_usdt,
                adjusted_sl,
            ) = self._calculate_position_size(
                entry_price=tracked.entry_price,
                stop_loss=tracked.stop_loss,
                side=tracked.side,
                strategy_name=tracked.strategy_name,
                strategy_balance=tracked.strategy_balance,
                risk_multiplier=risk_multiplier,
                signal_metadata=tracked.signal.metadata if tracked.signal else None,
            )

            # Update stop loss if it was adjusted (too tight)
            if adjusted_sl != tracked.stop_loss:
                logger.info(
                    f"{tracked.symbol} SL adjusted: {tracked.stop_loss:.6f} -> {adjusted_sl:.6f}"
                )
                tracked.stop_loss = adjusted_sl

            # Calculate quantity from position size (account for contract_size: 100 for XAUUSD, 1 for crypto)
            from src.core.trading_pairs import trading_pairs

            contract_size = trading_pairs.get_contract_size(tracked.symbol)
            usd_px = trading_pairs.usd_price(
                tracked.symbol, tracked.entry_price
            )  # USDJPY: 1.0, not the price in yen
            raw_quantity = tracked.position_size_usdt / (usd_px * contract_size)

            # Round quantity based on symbol-specific step sizes
            quantity = self._round_quantity(tracked.symbol, raw_quantity, tracked.entry_price)

            # Update position_size_usdt to reflect actual quantity after rounding
            # (rounding to min_lot can significantly change quantity, e.g. 0.007 -> 0.01 = +40%)
            if quantity != raw_quantity:
                old_size = tracked.position_size_usdt
                tracked.position_size_usdt = quantity * usd_px * contract_size
                # Risk must move together with size. Only the notional used to be
                # recalculated while risk_amount_usdt stayed pre-rounding — and
                # overstated the real risk: on a demo account the computed $183.46
                # vs the actual $180.00 when rounding 0.0917 -> 0.09.
                # This number feeds telemetry and notifications, so it was wrong
                # exactly where people read it.
                if old_size > 0:
                    tracked.risk_amount_usdt *= tracked.position_size_usdt / old_size
                size_change_pct = (
                    ((tracked.position_size_usdt - old_size) / old_size * 100)
                    if old_size > 0
                    else 0
                )
                if abs(size_change_pct) > 5:
                    logger.warning(
                        f"⚠️ Position size adjusted after qty rounding for {tracked.symbol}: "
                        f"${old_size:.2f} -> ${tracked.position_size_usdt:.2f} ({size_change_pct:+.1f}%) "
                        f"[qty: {raw_quantity:.6f} -> {quantity}]"
                    )

            # Check against Bybit's maxMktOrderQty limit
            try:
                symbol_info = await self.connector.get_symbol_info(tracked.symbol)
                max_mkt_qty = symbol_info.get("max_market_quantity", 0)
                if max_mkt_qty > 0 and quantity > max_mkt_qty:
                    # Cap quantity and recalculate position size
                    old_qty = quantity
                    quantity = max_mkt_qty * 0.95  # Leave 5% buffer
                    quantity = self._round_quantity(tracked.symbol, quantity, tracked.entry_price)
                    tracked.position_size_usdt = quantity * usd_px * contract_size
                    logger.warning(
                        f"⚠️ Qty capped for {tracked.symbol}: {old_qty:.2f} -> {quantity:.2f} "
                        f"(maxMktOrderQty={max_mkt_qty})"
                    )
            except Exception as e:
                logger.debug(f"Could not check max market qty for {tracked.symbol}: {e}")

            logger.info(
                f"📊 Order details: {tracked.symbol} | raw_qty={raw_quantity:.6f} -> qty={quantity} | price={tracked.entry_price}"
            )

            # For Hedge Mode: positionIdx 1=Long (BUY), 2=Short (SELL)
            position_idx = 1 if tracked.side == "BUY" else 2

            # Per-signal limit entry opt-in (e.g. swing_fade) overrides global config
            signal_meta = (tracked.signal.metadata if tracked.signal else {}) or {}
            per_signal_use_limit = bool(signal_meta.get("use_limit_entry", False))

            # Try Smart Limit Entry if enabled globally OR opted-in per-signal
            if self.limit_order_manager and (
                self.config.limit_entry_enabled or per_signal_use_limit
            ):
                return await self._execute_with_limit_order(tracked, quantity, position_idx)

            # Fallback: Direct market order
            return await self._execute_market_order(tracked, quantity, position_idx)

        except Exception as e:
            logger.error(f"Error executing real order: {e}")
            return False

    async def _execute_with_limit_order(
        self, tracked: TrackedSignal, quantity: float, position_idx: int
    ) -> bool:
        """
        Execute entry using smart limit order with order book analysis.

        The limit order will be monitored by LimitOrderManager.
        On fill or timeout, callbacks will handle SL/TP placement.
        """
        try:
            signal_meta = (tracked.signal.metadata if tracked.signal else {}) or {}
            per_signal_use_limit = bool(signal_meta.get("use_limit_entry", False))
            per_signal_cancel_on_timeout = bool(signal_meta.get("cancel_on_limit_timeout", False))
            per_signal_timeout_min = signal_meta.get("limit_fill_timeout_min")

            # Prepare signal data for callbacks (passed through to LimitOrderManager)
            signal_data = {
                "signal_id": tracked.signal_id,
                "symbol": tracked.symbol,
                "side": tracked.side,
                "quantity": quantity,
                "position_idx": position_idx,
                "stop_loss": tracked.stop_loss,
                "take_profit": tracked.take_profit,
                "strategy_name": tracked.strategy_name,
                # Pass per-signal flags so LimitOrderManager can honor them
                "metadata": {
                    "use_limit_entry": per_signal_use_limit,
                    "cancel_on_limit_timeout": per_signal_cancel_on_timeout,
                },
            }

            # When per-signal limit entry is requested, use signal.entry_price as the
            # exact limit price (skip order-book analysis) and per-signal timeout if set.
            ai_price = tracked.ai_adjusted_entry
            timeout_s = self.config.limit_entry_timeout_seconds
            if per_signal_use_limit:
                ai_price = tracked.entry_price
                if per_signal_timeout_min:
                    timeout_s = int(per_signal_timeout_min) * 60

            # Enter via LimitOrderManager
            pending = await self.limit_order_manager.enter_position(
                symbol=tracked.symbol,
                side=tracked.side,
                quantity=quantity,
                signal_id=tracked.signal_id,
                signal_data=signal_data,
                current_price=tracked.entry_price,
                timeout_seconds=timeout_s,
                ai_adjusted_price=ai_price,
            )

            # Check if immediately filled (market fallback or instant limit fill)
            if pending.status == PendingOrderStatus.FILLED:
                # Order already filled, update tracked signal
                tracked.executed_price = pending.average_fill_price
                tracked.executed_at = utcnow()
                tracked.exchange_order_id = pending.exchange_order_id or pending.market_order_id
                tracked.position_ticket = getattr(pending, "position_ticket", None)  # MT5 ticket
                tracked.status = "EXECUTED"
                tracked.initial_quantity = quantity  # Store for DCA
                self.stats["signals_executed"] += 1

                # Resolve MT5 position ticket if not returned
                if not tracked.position_ticket and hasattr(self.connector, "get_positions"):
                    try:
                        positions = await self.connector.get_positions(tracked.symbol)
                        if positions:
                            used_tickets = {
                                t.position_ticket
                                for t in self.tracked_signals.values()
                                if t.position_ticket
                                and t.status == "EXECUTED"
                                and t.symbol == tracked.symbol
                            }
                            candidates = [
                                p
                                for p in positions
                                if p.get("side", "").upper() == tracked.side
                                and p.get("ticket") not in used_tickets
                            ]
                            if candidates:
                                best = max(candidates, key=lambda p: p.get("ticket", 0))
                                tracked.position_ticket = best.get("ticket")
                                logger.info(f"Resolved position ticket: {tracked.position_ticket}")
                    except Exception as e:
                        logger.debug(f"Could not resolve position ticket: {e}")

                order_type = "MARKET" if pending.market_fallback_used else "LIMIT"
                logger.info(
                    f"✅ {order_type} ORDER FILLED: {tracked.side} {tracked.symbol} | "
                    f"Qty: {quantity} | Price: {tracked.executed_price:.4f}"
                    + (f" | Ticket: {tracked.position_ticket}" if tracked.position_ticket else "")
                )

                # Save entry order to DB as FILLED
                await self._save_order_to_db(
                    signal_id=tracked.signal_id,
                    order_type="ENTRY",
                    symbol=tracked.symbol,
                    side=tracked.side,
                    order_category=order_type,
                    price=pending.limit_price if not pending.market_fallback_used else None,
                    quantity=quantity,
                    exchange_order_id=tracked.exchange_order_id,
                    status="FILLED",
                    position_idx=position_idx,
                    metadata={"filled_price": tracked.executed_price},
                )

                # Recalculate SL/TP from actual fill price
                self._recalculate_sl_tp_from_fill(tracked)

                # Set SL and TP orders
                await self._place_sl_tp_orders(tracked, quantity)
                return True

            elif pending.status == PendingOrderStatus.PENDING:
                # Limit order placed, waiting for fill
                # Mark signal as PENDING_ENTRY (new status for limit orders)
                tracked.status = "PENDING_ENTRY"
                tracked.exchange_order_id = pending.exchange_order_id

                logger.info(
                    f"⏳ LIMIT ORDER PENDING: {tracked.side} {tracked.symbol} | "
                    f"Qty: {quantity} | Limit: {pending.limit_price:.4f} | "
                    f"Timeout: {self.config.limit_entry_timeout_seconds}s"
                )

                # Save entry order to DB as PLACED (pending)
                await self._save_order_to_db(
                    signal_id=tracked.signal_id,
                    order_type="ENTRY",
                    symbol=tracked.symbol,
                    side=tracked.side,
                    order_category="LIMIT",
                    price=pending.limit_price,
                    quantity=quantity,
                    exchange_order_id=pending.exchange_order_id,
                    status="PLACED",
                    position_idx=position_idx,
                )

                return True  # Order placed successfully, will be handled by callbacks

            else:
                # Failed to place order
                logger.error(f"Failed to place limit order: {pending.status}")
                # Save rejected order to DB
                await self._save_order_to_db(
                    signal_id=tracked.signal_id,
                    order_type="ENTRY",
                    symbol=tracked.symbol,
                    side=tracked.side,
                    order_category="LIMIT",
                    quantity=quantity,
                    status="REJECTED",
                    metadata={"reason": str(pending.status)},
                )
                return await self._execute_market_order(tracked, quantity, position_idx)

        except Exception as e:
            logger.error(f"Error in limit order execution: {e}, falling back to market")
            return await self._execute_market_order(tracked, quantity, position_idx)

    async def _execute_market_order(
        self, tracked: TrackedSignal, quantity: float, position_idx: int
    ) -> bool:
        """Execute direct market order (original logic)."""
        order_params = {
            "symbol": tracked.symbol,
            "side": tracked.side,
            "type": "MARKET",
            "quantity": quantity,
            "reduce_only": False,
            "position_idx": position_idx,
            # Telemetry attribution only — connectors read these, MT5 never sees them.
            "strategy": tracked.strategy_name,
            "signal_id": tracked.signal_id,
        }

        # Include SL in the order itself (MT5: ensures SL is bound to THIS specific position)
        # This is critical for fan/grid strategies with multiple same-side positions
        if tracked.stop_loss and tracked.stop_loss > 0:
            order_params["stop_loss"] = tracked.stop_loss

        order_result = await self.connector.place_order(order_params)

        if order_result and (order_result.get("success") or order_result.get("order_id")):
            order_id = order_result.get("order_id") or order_result.get("orderId")

            executed_price = float(
                order_result.get("price") or order_result.get("avgPrice") or tracked.entry_price
            )
            if executed_price == 0:
                executed_price = tracked.entry_price

            tracked.executed_price = executed_price
            tracked.executed_at = utcnow()
            tracked.exchange_order_id = order_id
            tracked.position_ticket = order_result.get("position_ticket")  # MT5 position ticket
            tracked.status = "EXECUTED"
            tracked.initial_quantity = quantity  # Store for DCA
            self.stats["signals_executed"] += 1

            # Resolve MT5 position ticket if not returned by place_order
            if not tracked.position_ticket and hasattr(self.connector, "get_positions"):
                try:
                    positions = await self.connector.get_positions(tracked.symbol)
                    if positions:
                        # Collect tickets already assigned to other tracked signals
                        used_tickets = {
                            t.position_ticket
                            for t in self.tracked_signals.values()
                            if t.position_ticket
                            and t.status == "EXECUTED"
                            and t.symbol == tracked.symbol
                        }
                        # Find newest unassigned position matching our side
                        candidates = [
                            p
                            for p in positions
                            if p.get("side", "").upper() == tracked.side
                            and p.get("ticket") not in used_tickets
                        ]
                        if candidates:
                            # Pick the one with highest ticket (newest)
                            best = max(candidates, key=lambda p: p.get("ticket", 0))
                            tracked.position_ticket = best.get("ticket")
                            logger.info(
                                f"Resolved position ticket: {tracked.position_ticket} for {tracked.symbol}"
                            )
                except Exception as e:
                    logger.debug(f"Could not resolve position ticket: {e}")

            logger.info(
                f"✅ MARKET ORDER EXECUTED: {tracked.side} {tracked.symbol} | "
                f"Qty: {quantity} | Price: {executed_price:.4f} | "
                f"Order ID: {order_id}"
                + (f" | Ticket: {tracked.position_ticket}" if tracked.position_ticket else "")
            )

            # Save entry order to DB
            await self._save_order_to_db(
                signal_id=tracked.signal_id,
                order_type="ENTRY",
                symbol=tracked.symbol,
                side=tracked.side,
                order_category="MARKET",
                quantity=quantity,
                exchange_order_id=order_id,
                status="FILLED",
                position_idx=position_idx,
                metadata={"filled_price": executed_price},
            )

            # Recalculate SL/TP from actual fill price (not signal price)
            # This ensures correct distances regardless of entry slippage
            self._recalculate_sl_tp_from_fill(tracked)

            await self._place_sl_tp_orders(tracked, quantity)
            return True
        else:
            error_msg = (
                order_result.get("error", "Unknown error") if order_result else "No response"
            )
            logger.error(f"Market order failed: {error_msg} | Response: {order_result}")
            # Save failed entry order to DB
            await self._save_order_to_db(
                signal_id=tracked.signal_id,
                order_type="ENTRY",
                symbol=tracked.symbol,
                side=tracked.side,
                order_category="MARKET",
                quantity=quantity,
                status="REJECTED",
                metadata={"error": error_msg},
            )
            return False

    def _recalculate_sl_tp_from_fill(self, tracked: TrackedSignal) -> None:
        """
        Recalculate SL/TP from actual fill price instead of signal price.

        Problem: Strategy calculates SL/TP from candle close, but real entry
        may differ. This keeps the same % distance from actual entry.

        Example:
            Signal: entry=99, SL=97.5 (1.5% below)
            Fill: entry=100.2
            New SL: 100.2 * (1 - 0.015) = 98.7 (still 1.5% below)
        """
        if not tracked.executed_price or not tracked.entry_price:
            return

        signal_price = tracked.entry_price
        fill_price = tracked.executed_price

        # Skip if prices are the same (no adjustment needed)
        if abs(fill_price - signal_price) < signal_price * 0.0001:  # < 0.01% diff
            return

        # Recalculate SL
        if tracked.stop_loss:
            if tracked.side == "BUY":  # LONG
                sl_pct = (signal_price - tracked.stop_loss) / signal_price
                new_sl = fill_price * (1 - sl_pct)
            else:  # SHORT
                sl_pct = (tracked.stop_loss - signal_price) / signal_price
                new_sl = fill_price * (1 + sl_pct)

            logger.info(
                f"SL adjusted from fill: {tracked.stop_loss:.4f} → {new_sl:.4f} "
                f"({sl_pct*100:.2f}% from entry {fill_price:.4f})"
            )
            tracked.stop_loss = new_sl
            tracked.original_stop_loss = new_sl

        # Recalculate TPs
        if tracked.take_profit:
            new_tps = []
            for tp in tracked.take_profit:
                if tracked.side == "BUY":  # LONG
                    tp_pct = (tp - signal_price) / signal_price
                    new_tp = fill_price * (1 + tp_pct)
                else:  # SHORT
                    tp_pct = (signal_price - tp) / signal_price
                    new_tp = fill_price * (1 - tp_pct)
                new_tps.append(new_tp)

            logger.info(
                f"TP adjusted from fill: {tracked.take_profit} → {[round(tp, 4) for tp in new_tps]}"
            )
            tracked.take_profit = new_tps

        # Update entry_price to match fill for consistent P&L tracking
        tracked.entry_price = fill_price

    async def _place_sl_tp_orders(self, tracked: TrackedSignal, quantity: float) -> None:
        """
        Place stop loss and take profit orders on exchange after main order executed.

        These orders stay on exchange and execute automatically when price hits.
        Order IDs are saved in TrackedSignal for later modification/cancellation.
        """
        if not self.connector:
            return

        try:
            # Determine close side and position index
            sl_side = "SELL" if tracked.side == "BUY" else "BUY"
            position_idx = 1 if tracked.side == "BUY" else 2

            # Round quantity for the symbol
            sl_quantity = self._round_quantity(
                tracked.symbol, quantity, tracked.executed_price or tracked.entry_price
            )

            # Place Stop Loss via trading-stop API (market SL on position)
            # Simple and reliable: triggers at price → executes as market order
            sl_stop_price = tracked.stop_loss
            current_price = self.current_prices.get(tracked.symbol)
            if current_price:
                if sl_side == "SELL" and sl_stop_price >= current_price:
                    sl_stop_price = current_price * 0.998
                    logger.warning(
                        f"Initial SL {tracked.stop_loss:.4f} >= current {current_price:.4f}, "
                        f"adjusting to {sl_stop_price:.4f}"
                    )
                elif sl_side == "BUY" and sl_stop_price <= current_price:
                    sl_stop_price = current_price * 1.002
                    logger.warning(
                        f"Initial SL {tracked.stop_loss:.4f} <= current {current_price:.4f}, "
                        f"adjusting to {sl_stop_price:.4f}"
                    )

            # Pass position_ticket for MT5 multi-position support
            sl_kwargs = {
                "symbol": tracked.symbol,
                "position_idx": position_idx,
                "stop_loss": sl_stop_price,
            }
            if tracked.position_ticket:
                sl_kwargs["position_ticket"] = tracked.position_ticket

            # Retry SL placement up to 3 times (critical for position protection)
            sl_placed = False
            sl_error = "Unknown"
            for sl_attempt in range(3):
                sl_result = await self.connector.set_trading_stop(**sl_kwargs)

                if sl_result and sl_result.get("success"):
                    tracked.sl_order_id = "TRADING_STOP"
                    logger.info(
                        f"📍 SL SET: {tracked.symbol} | "
                        f"SL price: {sl_stop_price:.4f} (market) | "
                        f"via trading-stop API"
                        + (f" (attempt {sl_attempt + 1})" if sl_attempt > 0 else "")
                    )
                    await self._save_order_to_db(
                        signal_id=tracked.signal_id,
                        order_type="STOP_LOSS",
                        symbol=tracked.symbol,
                        side=sl_side,
                        order_category="TRADING_STOP",
                        trigger_price=sl_stop_price,
                        quantity=sl_quantity,
                        exchange_order_id="TRADING_STOP",
                        status="PLACED",
                        position_idx=position_idx,
                        reduce_only=True,
                    )
                    sl_placed = True
                    break
                else:
                    sl_error = sl_result.get("error", "Unknown") if sl_result else "No response"
                    logger.warning(
                        f"SL placement attempt {sl_attempt + 1}/3 failed for "
                        f"{tracked.symbol}: {sl_error}"
                    )
                    if sl_attempt < 2:
                        await asyncio.sleep(1)

            if not sl_placed:
                logger.error(
                    f"CRITICAL: Failed to set SL for {tracked.symbol} after 3 attempts: "
                    f"{sl_error}. Position has NO stop loss!"
                )
                await self._save_order_to_db(
                    signal_id=tracked.signal_id,
                    order_type="STOP_LOSS",
                    symbol=tracked.symbol,
                    side=sl_side,
                    order_category="TRADING_STOP",
                    trigger_price=tracked.stop_loss,
                    quantity=sl_quantity,
                    status="REJECTED",
                    metadata={"error": sl_error, "attempts": 3},
                )

            # Place Take Profit orders (partial close)
            if self.config.partial_close_enabled and tracked.take_profit:
                tracked.tp_order_ids = []  # Reset list

                # Pre-calculate and round all TP quantities to handle dust
                tp_data = []  # [(tp_price, tp_qty, close_pct), ...]
                tp_percents = self.config.partial_close_percents[:2]  # Only TP1 and TP2

                for i, (tp_price, close_pct) in enumerate(
                    zip(tracked.take_profit[:2], tp_percents)
                ):
                    tp_qty = quantity * (close_pct / 100)
                    tp_qty = self._round_quantity(
                        tracked.symbol, tp_qty, tracked.executed_price or tracked.entry_price
                    )
                    tp_data.append((tp_price, tp_qty, close_pct))

                # Calculate dust: difference between total quantity and sum of rounded TP quantities
                total_tp_qty = sum(tp[1] for tp in tp_data)
                dust = quantity - total_tp_qty

                # Add dust to the last (highest) TP level if significant
                if dust > 0 and tp_data:
                    last_idx = len(tp_data) - 1
                    tp_price, tp_qty, close_pct = tp_data[last_idx]
                    adjusted_qty = tp_qty + dust
                    # Round again to ensure valid quantity
                    adjusted_qty = self._round_quantity(
                        tracked.symbol, adjusted_qty, tracked.executed_price or tracked.entry_price
                    )
                    tp_data[last_idx] = (tp_price, adjusted_qty, close_pct)
                    logger.debug(
                        f"Dust correction for {tracked.symbol}: added {dust:.8f} to TP{last_idx+1}"
                    )

                first_tp_price = tp_data[0][0] if tp_data else None

                if first_tp_price and tracked.position_ticket:
                    # MT5: set TP via position modification (not limit order).
                    # Works for all strategies — acts as safety net if bot crashes.
                    # Bot can update/remove TP later via set_trading_stop(take_profit=0).
                    tp_kwargs = {
                        "symbol": tracked.symbol,
                        "position_idx": position_idx,
                        "take_profit": first_tp_price,
                        "stop_loss": sl_stop_price,
                        "position_ticket": tracked.position_ticket,
                    }
                    tp_result = await self.connector.set_trading_stop(**tp_kwargs)

                    if tp_result and tp_result.get("success"):
                        tracked.tp_order_ids.append("TRADING_STOP_TP")
                        logger.info(
                            f"🎯 TP1 SET: {tracked.symbol} | "
                            f"TP price: {first_tp_price:.4f} | "
                            f"via trading-stop API (position modify)"
                        )
                    else:
                        error = tp_result.get("error", "Unknown") if tp_result else "No response"
                        logger.error(f"Failed to set TP1 for {tracked.symbol}: {error}")
                        tracked.tp_order_ids.append(None)
                elif first_tp_price:
                    # Fallback: place as limit order (crypto exchanges)
                    for i, (tp_price, tp_qty, close_pct) in enumerate(tp_data):
                        if tp_qty <= 0:
                            tracked.tp_order_ids.append(None)
                            continue

                        tp_result = await self.connector.place_order(
                            {
                                "symbol": tracked.symbol,
                                "side": sl_side,
                                "type": "LIMIT",
                                "quantity": tp_qty,
                                "price": tp_price,
                                "reduce_only": True,
                                "position_idx": position_idx,
                                "time_in_force": "GTC",
                            }
                        )

                        if tp_result and tp_result.get("success"):
                            order_id = tp_result.get("order_id")
                            tracked.tp_order_ids.append(order_id)
                            logger.info(
                                f"🎯 TP{i+1} ORDER PLACED: {tracked.symbol} | "
                                f"Limit: {tp_price:.4f} (maker) | "
                                f"Qty: {tp_qty} ({close_pct:.0f}%) | Order ID: {order_id}"
                            )
                        else:
                            error = (
                                tp_result.get("error", "Unknown") if tp_result else "No response"
                            )
                            logger.error(
                                f"Failed to place TP{i+1} order for {tracked.symbol}: {error}"
                            )
                            tracked.tp_order_ids.append(None)
                        await self._save_order_to_db(
                            signal_id=tracked.signal_id,
                            order_type=f"TAKE_PROFIT_{i+1}",
                            symbol=tracked.symbol,
                            side=sl_side,
                            order_category="LIMIT",
                            price=tp_price,
                            quantity=tp_qty,
                            status="REJECTED",
                            metadata={"error": error, "close_percent": close_pct},
                        )

        except Exception as e:
            logger.error(f"Error placing SL/TP orders for {tracked.symbol}: {e}")

    async def _modify_sl_order(
        self, tracked: TrackedSignal, new_stop_price: float, new_quantity: Optional[float] = None
    ) -> bool:
        """
        Modify the stop loss order on exchange to new price.
        Used when BE activates or trailing stop moves.

        Args:
            tracked: TrackedSignal with sl_order_id
            new_stop_price: New stop loss price
            new_quantity: New quantity (optional, for partial close adjustments)

        Returns:
            True if successfully modified
        """
        if not self.connector or not tracked.sl_order_id:
            logger.warning(f"Cannot modify SL for {tracked.symbol}: no connector or order ID")
            return False

        try:
            # Determine position index
            position_idx = 1 if tracked.side == "BUY" else 2
            sl_side = "SELL" if tracked.side == "BUY" else "BUY"

            # Validate stop price against current price
            current_price = self.current_prices.get(tracked.symbol)
            if current_price:
                if sl_side == "SELL" and new_stop_price >= current_price:
                    adjusted_stop = current_price * 0.998
                    logger.warning(
                        f"SL price {new_stop_price:.4f} >= current {current_price:.4f} for {tracked.symbol}, "
                        f"adjusting to {adjusted_stop:.4f}"
                    )
                    new_stop_price = adjusted_stop
                elif sl_side == "BUY" and new_stop_price <= current_price:
                    adjusted_stop = current_price * 1.002
                    logger.warning(
                        f"SL price {new_stop_price:.4f} <= current {current_price:.4f} for {tracked.symbol}, "
                        f"adjusting to {adjusted_stop:.4f}"
                    )
                    new_stop_price = adjusted_stop

            # Update SL via trading-stop API (just set new price, no cancel needed)
            sl_update_kwargs = {
                "symbol": tracked.symbol,
                "position_idx": position_idx,
                "stop_loss": new_stop_price,
            }
            if tracked.position_ticket:
                sl_update_kwargs["position_ticket"] = tracked.position_ticket
            sl_result = await self.connector.set_trading_stop(**sl_update_kwargs)

            if sl_result and sl_result.get("success"):
                tracked.sl_order_id = "TRADING_STOP"
                logger.info(
                    f"{P.UPDATE} SL {tracked.symbol} | New SL: {new_stop_price:.4f} (market)"
                )
                return True
            else:
                error = sl_result.get("error", "Unknown") if sl_result else "No response"
                logger.error(f"Failed to update SL for {tracked.symbol}: {error}")
                tracked.sl_order_id = None
                return False

        except Exception as e:
            logger.error(f"Error modifying SL order for {tracked.symbol}: {e}")
            return False

    async def _place_trailing_stop_order(
        self, tracked: TrackedSignal, trailing_stop_price: float
    ) -> bool:
        """
        Place or update trailing stop order on exchange.

        Args:
            tracked: TrackedSignal
            trailing_stop_price: Current trailing stop price

        Returns:
            True if successfully placed/updated
        """
        if not self.connector:
            return False

        try:
            # Cancel existing trailing order if exists
            if tracked.trailing_order_id:
                cancel_result = await self.connector.cancel_order(
                    {"symbol": tracked.symbol, "order_id": tracked.trailing_order_id}
                )

                # Check if cancel failed because order was already filled
                if cancel_result and not cancel_result.get("success"):
                    error_msg = str(cancel_result.get("error", "")).lower()
                    # Order filled or doesn't exist - position might be closed
                    if "not exist" in error_msg or "filled" in error_msg or "too late" in error_msg:
                        logger.warning(
                            f"{P.TRAIL} {tracked.symbol}: Old order {tracked.trailing_order_id} "
                            f"already filled/cancelled, checking position..."
                        )
                        # Check if position still exists
                        positions = await self.connector.get_positions(tracked.symbol)
                        has_position = False
                        for pos in positions:
                            if float(pos.get("size", 0)) > 0:
                                pos_side = pos.get("side", "").upper()
                                if (tracked.side == "BUY" and pos_side == "BUY") or (
                                    tracked.side == "SELL" and pos_side == "SELL"
                                ):
                                    has_position = True
                                    break

                        if not has_position:
                            # Position was closed by exchange order
                            logger.info(
                                f"{P.TRAIL} {tracked.symbol}: Position closed by exchange, "
                                f"skipping new trailing order"
                            )
                            tracked.trailing_order_id = None
                            return False

                tracked.trailing_order_id = None  # Clear old ID

            # Check if position still exists before placing new order
            positions = await self.connector.get_positions(tracked.symbol)
            actual_qty = 0.0
            for pos in positions:
                pos_side = pos.get("side", "").upper()
                if (tracked.side == "BUY" and pos_side == "BUY") or (
                    tracked.side == "SELL" and pos_side == "SELL"
                ):
                    actual_qty = float(pos.get("size", 0))
                    break

            if actual_qty <= 0:
                logger.debug(
                    f"{P.TRAIL} {tracked.symbol}: No position found, skipping trailing order"
                )
                return False

            # Determine side and position index
            close_side = "SELL" if tracked.side == "BUY" else "BUY"
            position_idx = 1 if tracked.side == "BUY" else 2

            # Place trailing stop as STOP_LIMIT at current trailing price
            # Use qty=0 with closeOnTrigger to close entire remaining position (no dust)
            # Apply same trigger buffer logic as regular SL
            sl_buffer = self.config.sl_trigger_buffer_percent / 100
            if close_side == "SELL":  # LONG position
                trailing_trigger_price = trailing_stop_price * (1 + sl_buffer)
            else:  # SHORT position
                trailing_trigger_price = trailing_stop_price * (1 - sl_buffer)
            trailing_limit_price = trailing_stop_price  # Limit at exact price

            result = await self.connector.place_order(
                {
                    "symbol": tracked.symbol,
                    "side": close_side,
                    "type": "STOP_LIMIT",
                    "quantity": 0,  # Close entire position when triggered
                    "price": trailing_limit_price,
                    "stop_price": trailing_trigger_price,
                    "reduce_only": True,
                    "close_on_trigger": True,
                    "position_idx": position_idx,
                }
            )

            if result and result.get("success"):
                tracked.trailing_order_id = result.get("order_id")
                logger.info(
                    f"{P.TRAIL} {tracked.symbol} | Trigger: {trailing_trigger_price:.4f} → Limit: {trailing_limit_price:.4f} (order: {tracked.trailing_order_id})"
                )
                return True
            else:
                error = result.get("error", "Unknown") if result else "No response"
                logger.error(f"Failed to place trailing stop for {tracked.symbol}: {error}")
                return False

        except Exception as e:
            logger.error(f"Error placing trailing stop for {tracked.symbol}: {e}")
            return False

    async def _cancel_sl_tp_orders(self, tracked: TrackedSignal) -> None:
        """
        Cancel all SL/TP orders for a position.
        Called when position is closed or needs order cleanup.
        """
        if not self.connector:
            return

        try:
            # Cancel SL via trading-stop (set SL to 0)
            if tracked.sl_order_id:
                position_idx = 1 if tracked.side == "BUY" else 2
                sl_cancel_kwargs = {
                    "symbol": tracked.symbol,
                    "position_idx": position_idx,
                    "stop_loss": 0,
                }
                if tracked.position_ticket:
                    sl_cancel_kwargs["position_ticket"] = tracked.position_ticket
                await self.connector.set_trading_stop(**sl_cancel_kwargs)
                logger.debug(f"Cleared trading-stop SL for {tracked.symbol}")
                await self._update_order_status_in_db(
                    signal_id=tracked.signal_id,
                    order_type="STOP_LOSS",
                    status="CANCELLED",
                    status_reason="Position closed",
                )
                tracked.sl_order_id = None

            # Cancel TPs
            for i, tp_order_id in enumerate(tracked.tp_order_ids):
                if tp_order_id:
                    if tp_order_id == "TRADING_STOP_TP":
                        # TP was set via position modification — clear it
                        position_idx = 1 if tracked.side == "BUY" else 2
                        tp_clear_kwargs = {
                            "symbol": tracked.symbol,
                            "position_idx": position_idx,
                            "take_profit": 0,
                        }
                        if tracked.position_ticket:
                            tp_clear_kwargs["position_ticket"] = tracked.position_ticket
                        await self.connector.set_trading_stop(**tp_clear_kwargs)
                        logger.debug(f"Cleared trading-stop TP for {tracked.symbol}")
                    else:
                        await self.connector.cancel_order(
                            {"symbol": tracked.symbol, "order_id": tp_order_id}
                        )
                        logger.debug(f"Cancelled TP{i+1} order {tp_order_id}")
                    await self._update_order_status_in_db(
                        signal_id=tracked.signal_id,
                        order_type=f"TAKE_PROFIT_{i+1}",
                        status="CANCELLED",
                        status_reason="Position closed",
                    )
            tracked.tp_order_ids = []

            # Cancel trailing
            if tracked.trailing_order_id:
                await self.connector.cancel_order(
                    {"symbol": tracked.symbol, "order_id": tracked.trailing_order_id}
                )
                logger.debug(f"Cancelled trailing order {tracked.trailing_order_id}")
                # Update order status in DB
                await self._update_order_status_in_db(
                    signal_id=tracked.signal_id,
                    order_type="TRAILING_STOP",
                    status="CANCELLED",
                    status_reason="Position closed",
                )
                tracked.trailing_order_id = None

        except Exception as e:
            logger.error(f"Error cancelling orders for {tracked.symbol}: {e}")

    async def _sync_be_sl_to_exchange(
        self, signal_id: str, tracked: TrackedSignal, be_stop_price: float
    ) -> None:
        """
        Sync Break-Even stop loss to exchange.
        Called when BE becomes ACTIVE - moves SL to BE price on exchange.
        """
        try:
            success = await self._modify_sl_order(tracked, be_stop_price)
            if success:
                tracked.be_sl_synced = True
                logger.info(
                    f"🛡️ BE SL SYNCED TO EXCHANGE: {tracked.symbol} | "
                    f"SL moved to {be_stop_price:.4f}"
                )
            else:
                logger.warning(f"Failed to sync BE SL to exchange for {tracked.symbol}, will retry")
                tracked.be_sl_synced = False
        except Exception as e:
            logger.error(f"Error syncing BE SL to exchange for {tracked.symbol}: {e}")
            tracked.be_sl_synced = False

    async def _adjust_sl_quantity_after_partial(
        self, tracked: TrackedSignal, new_remaining_percent: float
    ) -> None:
        """
        Adjust SL after a partial TP close.

        With trading-stop SL, quantity is auto-managed by the exchange
        (covers remaining position). No adjustment needed unless
        we want to re-set the SL price.
        """
        if not self.connector or not tracked.sl_order_id:
            return

        # trading-stop SL auto-covers remaining position size
        # Just log the change
        logger.info(
            f"📊 SL auto-adjusted by exchange: {tracked.symbol} | "
            f"{new_remaining_percent:.0f}% remaining (trading-stop covers full position)"
        )

    async def _execute_close_order(self, tracked: "TrackedSignal", close_percent: float) -> bool:
        """
        Execute a real close order on the exchange.

        Args:
            tracked: Tracked signal with position info
            close_percent: Percentage of position to close (1-100)

        Returns:
            True if order was placed successfully
        """
        if not self.connector:
            logger.debug("No connector available for close order")
            return False

        try:
            # Determine close side (opposite of position side)
            close_side = "SELL" if tracked.side == "BUY" else "BUY"

            # For Hedge Mode: use same positionIdx as the position we're closing
            position_idx = 1 if tracked.side == "BUY" else 2

            # For 100% close: use qty=0 with closeOnTrigger to close entire position
            # This eliminates dust from rounding errors
            if close_percent >= 99.9:
                order_result = await self.connector.place_order(
                    {
                        "symbol": tracked.symbol,
                        "side": close_side,
                        "type": "MARKET",
                        "quantity": 0,  # Close entire position
                        "reduce_only": True,
                        "close_on_trigger": True,
                        "position_idx": position_idx,
                    }
                )
                close_quantity = "ALL"
            else:
                # Calculate quantity to close for partial exits (account for contract_size)
                from src.core.trading_pairs import trading_pairs as tp_reg

                entry_quantity = tracked.position_size_usdt / (
                    tp_reg.usd_price(tracked.symbol, tracked.executed_price)
                    * tp_reg.get_contract_size(tracked.symbol)
                )
                close_quantity = entry_quantity * (close_percent / 100.0)

                # Round to proper precision
                close_quantity = self._round_quantity(
                    tracked.symbol, close_quantity, tracked.executed_price
                )

                if close_quantity <= 0:
                    logger.warning(f"Close quantity is zero for {tracked.symbol}")
                    return False

                # Place market close order with reduceOnly
                order_result = await self.connector.place_order(
                    {
                        "symbol": tracked.symbol,
                        "side": close_side,
                        "type": "MARKET",
                        "quantity": close_quantity,
                        "reduce_only": True,
                        "position_idx": position_idx,
                    }
                )

            if order_result and (order_result.get("success") or order_result.get("order_id")):
                order_id = order_result.get("order_id") or order_result.get("orderId")
                logger.info(
                    f"✅ CLOSE ORDER EXECUTED: {close_side} {tracked.symbol} | "
                    f"Qty: {close_quantity:.6f} ({close_percent:.0f}%) | "
                    f"Order ID: {order_id}"
                )
                return True
            else:
                error_msg = (
                    order_result.get("error", "Unknown error") if order_result else "No response"
                )
                logger.error(f"Close order failed: {error_msg} | Symbol: {tracked.symbol}")
                return False

        except Exception as e:
            logger.error(f"Error executing close order for {tracked.symbol}: {e}")
            return False

    def _init_strategy_balance(self, strategy_name: str, initial_balance: float) -> None:
        """
        Initialize balance tracking for a strategy if not already done.

        Args:
            strategy_name: Name of the strategy
            initial_balance: Initial balance allocated to this strategy
        """
        if strategy_name not in self.strategy_balances:
            self.strategy_balances[strategy_name] = {
                "initial": initial_balance,
                "current": initial_balance,
                "pnl": 0.0,
                "max_balance": initial_balance,
                "max_drawdown": 0.0,
                "winners": 0,
                "losers": 0,
                "trades": 0,
            }
            logger.info(
                f"📊 Initialized balance for strategy '{strategy_name}': ${initial_balance:.2f}"
            )

    def _get_strategy_balance(self, strategy_name: str, strategy_balance: float) -> float:
        """
        Get current balance for a strategy.

        Args:
            strategy_name: Name of the strategy
            strategy_balance: Initial balance from signal (for initialization)

        Returns:
            Current balance for the strategy
        """
        # If strategy has a dedicated balance, use it
        if strategy_name and strategy_balance > 0:
            self._init_strategy_balance(strategy_name, strategy_balance)
            if self.config.use_compounding:
                balance = self.strategy_balances[strategy_name]["current"]
            else:
                balance = self.strategy_balances[strategy_name]["initial"]
        elif self.config.use_compounding:
            # Otherwise use global balance
            balance = self.stats["current_balance"]
        else:
            balance = self.config.initial_balance

        # Sizing balance cap (parity with backtest balance_cap, see config comment)
        return self._apply_sizing_cap(balance)

    def _apply_sizing_cap(self, balance: float) -> float:
        """Apply the sizing balance cap.

        Mirrors BacktestEngine._calculate_position_size and, backtest-side,
        PortfolioManager._sizing_base. Used both for position sizing and for
        the fan-batch thresholds, so the two share one base.
        """
        cap = self.config.sizing_balance_cap
        if cap <= 0:
            return balance
        return balance * (cap / 100.0) if cap < 100 else min(balance, cap)

    async def _get_volatility_risk_multiplier(self) -> float:
        """
        Get risk multiplier based on current BTC volatility/trend.

        Returns:
            Multiplier for risk_per_trade:
            - 0.5 for ranging/flat market (reduce risk)
            - 1.0 for trending market (normal risk)
            - 0.75 for high volatility (slightly reduce risk)
        """
        if not self.config.dynamic_risk_enabled:
            return 1.0

        if not self.data_provider:
            return 1.0  # No data provider, use default risk

        import time

        now = time.time()

        # Use cached value if still valid
        if self._btc_trend_cache and (now - self._btc_cache_time) < self._btc_cache_ttl:
            deviation = abs(self._btc_deviation_cache or 0)
            threshold = self.config.dynamic_risk_btc_threshold

            # Simplified logic:
            # - < threshold -> ranging (low risk)
            # - >= threshold -> trending (normal risk)
            # - > threshold * 3 -> high volatility (medium risk)
            if deviation < threshold:
                # Ranging/flat market
                multiplier = self.config.dynamic_risk_ranging_multiplier
            elif deviation > threshold * 3:
                # Very high volatility (> 4.5% from MA)
                multiplier = self.config.dynamic_risk_high_vol_multiplier
            else:
                # Normal trending
                multiplier = self.config.dynamic_risk_trending_multiplier

            logger.debug(
                f"Dynamic Risk (cached): BTC dev={deviation*100:.2f}%, threshold={threshold*100:.2f}%, multiplier={multiplier}"
            )
            return multiplier

        # Fetch fresh BTC data
        try:
            candles = await self.data_provider.get_candles(
                symbol="BTCUSDT", timeframe="4h", limit=25
            )

            if not candles or len(candles) < 20:
                return 1.0

            # Calculate EMA20
            closes = [c.close for c in candles]
            current_price = closes[-1]

            # Simple EMA calculation
            period = 20
            multiplier = 2 / (period + 1)
            ema = closes[0]
            for close in closes[1:]:
                ema = (close * multiplier) + (ema * (1 - multiplier))

            # Calculate deviation from EMA
            deviation = (current_price - ema) / ema

            # Update cache
            self._btc_deviation_cache = deviation
            self._btc_cache_time = now

            if abs(deviation) >= self.config.dynamic_risk_btc_threshold:
                self._btc_trend_cache = "TRENDING"
            else:
                self._btc_trend_cache = "RANGING"

            # Determine multiplier (simplified logic)
            abs_deviation = abs(deviation)
            threshold = self.config.dynamic_risk_btc_threshold

            if abs_deviation < threshold:
                # Ranging/flat market - reduce risk
                multiplier = self.config.dynamic_risk_ranging_multiplier
                logger.info(
                    f"Dynamic Risk: RANGING (BTC dev={abs_deviation*100:.2f}% < {threshold*100:.1f}%) -> risk x{multiplier}"
                )
            elif abs_deviation > threshold * 3:
                # Very high volatility
                multiplier = self.config.dynamic_risk_high_vol_multiplier
                logger.info(
                    f"Dynamic Risk: HIGH VOL (BTC dev={abs_deviation*100:.2f}% > {threshold*300:.1f}%) -> risk x{multiplier}"
                )
            else:
                # Normal trending
                multiplier = self.config.dynamic_risk_trending_multiplier
                logger.info(
                    f"Dynamic Risk: TRENDING (BTC dev={abs_deviation*100:.2f}%) -> risk x{multiplier}"
                )

            return multiplier

        except Exception as e:
            logger.warning(f"Failed to get BTC trend for dynamic risk: {e}")
            return 1.0

    def _calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        side: str,
        strategy_name: str = "",
        strategy_balance: float = 0.0,
        risk_multiplier: float = 1.0,
        signal_metadata: dict = None,
    ) -> tuple:
        """
        Calculate position size based on risk parameters.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            side: 'BUY' or 'SELL'
            strategy_name: Name of the strategy (for per-strategy balance)
            strategy_balance: Initial balance for the strategy (0 = use global)
            risk_multiplier: Dynamic risk multiplier (0.5 for ranging, 1.0 for trending)
            signal_metadata: Signal metadata with per-strategy overrides

        Returns:
            Tuple of (position_size_usdt, risk_amount_usdt, adjusted_stop_loss)
            adjusted_stop_loss: The corrected SL price if original was too tight, else original
        """
        meta = signal_metadata or {}

        # Get current balance (per-strategy or global)
        balance = self._get_strategy_balance(strategy_name, strategy_balance)

        # Per-strategy sizing method and risk override from signal metadata
        sizing_method = meta.get("position_sizing_method") or self.config.position_sizing_method
        risk_per_trade = meta.get("risk_per_trade") or self.config.risk_per_trade

        # Calculate stop loss distance in %
        if side == "BUY":
            sl_distance_percent = (entry_price - stop_loss) / entry_price
        else:
            sl_distance_percent = (stop_loss - entry_price) / entry_price

        # Avoid division by zero for invalid SL (wrong side)
        adjusted_stop_loss = stop_loss  # Default: no adjustment

        if sl_distance_percent <= 0:
            sl_distance_percent = 0.01  # Default 1%
            if side == "BUY":
                adjusted_stop_loss = entry_price * (1 - 0.01)
            else:
                adjusted_stop_loss = entry_price * (1 + 0.01)
            logger.warning(f"Invalid SL (wrong side), adjusted to {adjusted_stop_loss:.6f} (1%)")

        if sizing_method == "fixed_balance":
            # Fixed position sizing: position = balance (notional)
            position_size = balance
            risk_amount = position_size * sl_distance_percent
        elif sizing_method == "balance_ratio":
            # Simple linear scaling: $100 = 0.01 lot equivalent
            position_size = balance / 10.0
            risk_amount = position_size * sl_distance_percent
        else:
            # Risk-based sizing (fixed_fractional): position = risk / sl_distance
            adjusted_risk = float(risk_per_trade)
            risk_amount = balance * adjusted_risk
            position_size = risk_amount / sl_distance_percent

        # Apply limits
        min_size = self.config.min_position_size
        max_size = balance * self.config.max_position_percent * self.config.leverage

        position_size = max(min_size, min(position_size, max_size))

        # Strategy size multiplier — AFTER the clamp, as in the backtest
        # (backtest_engine._signal_to_position: hedge_size_pct / dca_size_pct /
        # initial_position_size are applied last, to the already clamped
        # size).
        #
        # The live path used not to read this key at all: only the backtest
        # consumed it. For a grid strategy with a 0.6 size multiplier that meant
        # every leg was ~67% larger live than in every run the strategy was
        # calibrated on — live risk and drawdown exceeded the backtested ones.
        # For a grid of many legs the gap compounds.
        size_mult = meta.get("initial_position_size")
        if size_mult is not None and 0 < float(size_mult) < 1.0:
            size_mult = float(size_mult)
            position_size = position_size * size_mult
            # risk must move together with size: it feeds telemetry,
            # notifications and the lot calculation, and a mismatch here lies silently
            risk_amount = risk_amount * size_mult
            # the exchange minimum still cannot be violated, unlike in the backtest
            position_size = max(min_size, position_size)

        strategy_info = f" [{strategy_name}]" if strategy_name else ""
        logger.debug(
            f"Position sizing{strategy_info}: balance=${balance:.2f}, "
            f"SL={sl_distance_percent*100:.2f}%, size=${position_size:.2f}, "
            f"risk_amt=${risk_amount:.2f}" + (f", size_mult={size_mult}" if size_mult else "")
        )

        return position_size, risk_amount, adjusted_stop_loss

    def _round_quantity(self, symbol: str, raw_quantity: float, price: float) -> float:
        """
        Round quantity to valid step size for the symbol.

        Uses config/trading_pairs.yaml for symbol-specific settings,
        with intelligent fallback based on price.
        """
        from src.core.trading_pairs import trading_pairs

        quantity = trading_pairs.round_quantity(symbol, raw_quantity, price)
        step = trading_pairs.get_quantity_step(symbol)
        logger.debug(f"Quantity: {raw_quantity:.8f} -> {quantity} (step={step})")
        return quantity

    def _update_balance(
        self,
        pnl_usdt: float,
        strategy_name: str = "",
        strategy_balance: float = 0.0,
        is_winner: bool = False,
    ) -> None:
        """
        Update virtual balance after closing a position.

        Args:
            pnl_usdt: P&L in USDT (positive = profit, negative = loss)
            strategy_name: Name of the strategy (for per-strategy tracking)
            strategy_balance: Initial balance of the strategy (0 = use global)
            is_winner: Whether this was a winning trade
        """
        # Update global balance
        self.stats["current_balance"] += pnl_usdt

        # Update max balance (peak)
        if self.stats["current_balance"] > self.stats["max_balance"]:
            self.stats["max_balance"] = self.stats["current_balance"]

        # Calculate drawdown from peak
        drawdown_usdt = self.stats["max_balance"] - self.stats["current_balance"]
        drawdown_percent = (
            (drawdown_usdt / self.stats["max_balance"] * 100)
            if self.stats["max_balance"] > 0
            else 0
        )

        # Update max drawdown
        if drawdown_usdt > self.stats["max_drawdown_usdt"]:
            self.stats["max_drawdown_usdt"] = drawdown_usdt
            self.stats["max_drawdown_percent"] = drawdown_percent

        # Update per-strategy balance if applicable
        if strategy_name and strategy_balance > 0 and strategy_name in self.strategy_balances:
            strat = self.strategy_balances[strategy_name]
            strat["current"] += pnl_usdt
            strat["pnl"] += pnl_usdt
            strat["trades"] += 1

            if is_winner:
                strat["winners"] += 1
            else:
                strat["losers"] += 1

            # Update strategy max balance
            if strat["current"] > strat["max_balance"]:
                strat["max_balance"] = strat["current"]

            # Update strategy max drawdown
            strat_drawdown = strat["max_balance"] - strat["current"]
            if strat_drawdown > strat["max_drawdown"]:
                strat["max_drawdown"] = strat_drawdown

    def _load_balance_from_db(self) -> None:
        """Load balance from database if available."""
        # If using exchange balance, don't load from DB - use initial_balance from config
        # (which was set from exchange balance in main.py for demo/testnet/production modes)
        if self.config.use_exchange_balance:
            logger.info(
                f"📊 Using balance from exchange: ${self.config.initial_balance:.2f} "
                f"(DB balance ignored)"
            )
            return

        if not self.database:
            logger.debug("No database configured, using initial balance from config")
            return

        try:
            latest = self.database.get_latest_balance()
            if latest and latest.get("balance"):
                loaded_balance = latest["balance"]
                loaded_max = latest.get("max_balance", loaded_balance)

                # Update stats with loaded values
                self.stats["current_balance"] = loaded_balance
                self.stats["max_balance"] = loaded_max
                self.stats["initial_balance"] = loaded_balance  # Start from loaded balance

                # Also update daily start balance
                self.daily_start_balance = loaded_balance

                logger.info(
                    f"📊 Balance loaded from DB: ${loaded_balance:.2f} "
                    f"(max: ${loaded_max:.2f}, last event: {latest.get('event_type', 'N/A')})"
                )
            else:
                logger.info(
                    f"No previous balance in DB, starting fresh with ${self.config.initial_balance:.2f}"
                )

        except Exception as e:
            logger.warning(f"Failed to load balance from DB: {e}, using config default")

    def _save_balance_to_db(
        self, event_type: str, trade_symbol: str = None, trade_pnl: float = None
    ) -> None:
        """
        Save current balance to database.

        Args:
            event_type: Type of event (SESSION_START, SESSION_END, TRADE_CLOSE, DAILY_RESET)
            trade_symbol: Symbol if this is a trade close
            trade_pnl: P&L if this is a trade close
        """
        if not self.database:
            return

        try:
            # Calculate P&L metrics
            initial = self.config.initial_balance
            current = self.stats["current_balance"]
            daily_pnl = current - self.daily_start_balance
            daily_pnl_pct = (
                (daily_pnl / self.daily_start_balance * 100) if self.daily_start_balance > 0 else 0
            )
            total_pnl = self.stats.get("total_pnl_usdt", 0)
            total_pnl_pct = (total_pnl / initial * 100) if initial > 0 else 0

            self.database.save_balance(
                balance=current,
                event_type=event_type,
                session_id=self.session_id,
                daily_start_balance=self.daily_start_balance,
                max_balance=self.stats["max_balance"],
                daily_pnl=daily_pnl,
                daily_pnl_percent=daily_pnl_pct,
                total_pnl=total_pnl,
                total_pnl_percent=total_pnl_pct,
                trade_symbol=trade_symbol,
                trade_pnl=trade_pnl,
                total_trades=self.stats["signals_closed"],
                winning_trades=self.stats["winners"],
                losing_trades=self.stats["losers"],
            )

        except Exception as e:
            logger.warning(f"Failed to save balance to DB: {e}")

    def update_price(
        self, symbol: str, price: float, high: Optional[float] = None, low: Optional[float] = None
    ) -> None:
        """
        Update current price for a symbol.

        Args:
            symbol: Trading symbol
            price: Current price (candle close)
            high: Candle high (for wick detection). Falls back to price if None.
            low: Candle low (for wick detection). Falls back to price if None.
        """
        self.current_prices[symbol] = price
        self.current_highs[symbol] = high if high is not None else price
        self.current_lows[symbol] = low if low is not None else price

    async def _check_fan_batch_close(self, trailing_enabled: bool = True) -> List[tuple]:
        """
        Check fan/grid batches for portfolio-level batch close.

        Args:
            trailing_enabled: When False, only check max_loss and profit_target
                (hard stops). Trailing is skipped to avoid cutting profits on
                high-frequency price noise. Set True only at candle close.

        Returns list of (signal_id, exit_price, reason) tuples for positions to close.
        Mirrors logic from backtesting/portfolio/portfolio_manager.py _check_fan_batches.
        """
        positions_to_close = []

        # Group executed positions by fan_batch_id
        fan_batches: Dict[str, List[tuple]] = {}
        for signal_id, tracked in self.tracked_signals.items():
            if tracked.status != "EXECUTED":
                continue
            if not tracked.signal:
                continue
            if hasattr(tracked.signal, "metadata"):
                metadata = tracked.signal.metadata
            elif isinstance(tracked.signal, dict):
                metadata = tracked.signal.get("metadata")
            else:
                metadata = None
            if not metadata:
                continue
            batch_id = metadata.get("fan_batch_id") or metadata.get("batch_id")
            if not batch_id:
                continue
            if batch_id not in fan_batches:
                fan_batches[batch_id] = []
            fan_batches[batch_id].append((signal_id, tracked))

        if not fan_batches:
            return positions_to_close

        # Get current equity (balance)
        equity = self.stats.get("current_balance", self.config.initial_balance)
        # Batch thresholds are measured on the same base that sized the legs
        # (_calculate_position_size -> _get_strategy_balance). Otherwise, with a
        # growing account and a fixed sizing base, the ratio
        # "position size / threshold denominator" drifts and the profit target
        # becomes unreachable. Measured in a 2026 backtest of a grid strategy:
        # profit-target exits 21% -> 0%, stop exits 37% -> 65%.
        equity = self._apply_sizing_cap(equity)
        if equity <= 0:
            return positions_to_close

        # Check each batch
        for batch_id, positions in fan_batches.items():
            if not positions:
                continue

            # Calculate total unrealized PnL in USDT for the batch
            total_pnl = 0.0
            all_have_price = True

            for sig_id, tracked in positions:
                current_price = self.current_prices.get(tracked.symbol)
                if current_price is None:
                    all_have_price = False
                    break

                # Get contract size for PnL calculation
                from src.core.trading_pairs import trading_pairs as _tp

                cs = _tp.get_contract_size(tracked.symbol)

                qty = (
                    tracked.initial_quantity
                    if tracked.initial_quantity > 0
                    else (
                        tracked.position_size_usdt
                        / (_tp.usd_price(tracked.symbol, tracked.executed_price) * cs)
                        if tracked.executed_price
                        else 0
                    )
                )

                if tracked.side == "BUY":
                    pnl = (current_price - tracked.executed_price) * qty * cs
                else:
                    pnl = (tracked.executed_price - current_price) * qty * cs

                total_pnl += pnl

            if not all_have_price:
                continue

            total_pnl_pct = (total_pnl / equity) * 100

            # Get thresholds from first position's metadata
            first_sig = positions[0][1].signal
            if first_sig and hasattr(first_sig, "metadata"):
                first_metadata = first_sig.metadata or {}
            elif isinstance(first_sig, dict):
                first_metadata = first_sig.get("metadata", {})
            else:
                first_metadata = {}
            profit_target_pct = first_metadata.get("batch_profit_target_pct", 0.3)
            max_loss_pct = first_metadata.get("batch_max_loss_pct", 5.0)
            trailing_callback_pct = first_metadata.get("batch_trailing_callback_pct", 0.15)

            # Get or init trailing state
            is_new_state = batch_id not in self._fan_batch_state
            if is_new_state:
                self._fan_batch_state[batch_id] = {
                    "trailing_active": False,
                    "peak_pnl": 0.0,
                    "sl_last_sync_time": 0.0,
                    "sl_pending": {},
                    "sl_synced_prices": {},
                    "tp_pending": {},
                    "tp_synced_prices": {},
                }
                logger.debug(
                    f"Fan batch {batch_id[:8]}: new state created, " f"{len(positions)} positions"
                )
            state = self._fan_batch_state[batch_id]

            # Safety: if state has stale trailing from a previous batch cycle,
            # reset it when the current batch just started (all positions are new)
            if not is_new_state and state["trailing_active"]:
                oldest_exec = min(
                    (trk.executed_at for _, trk in positions if trk.executed_at), default=None
                )
                if oldest_exec:
                    state_age = (utcnow() - oldest_exec).total_seconds()
                    if state_age < 10 and state["peak_pnl"] > total_pnl * 5:
                        # State has wildly higher peak than current PnL on a fresh batch
                        logger.warning(
                            f"Fan batch {batch_id[:8]}: resetting stale trailing state "
                            f"(peak=${state['peak_pnl']:.2f} vs current=${total_pnl:.2f}, "
                            f"batch age={state_age:.0f}s)"
                        )
                        state["trailing_active"] = False
                        state["peak_pnl"] = 0.0

            should_close = False
            close_reason = ""

            # 1. Emergency stop — max loss
            if total_pnl_pct <= -max_loss_pct:
                should_close = True
                close_reason = "FAN_BATCH_MAX_LOSS"
                logger.warning(
                    f"Fan batch {batch_id[:8]}: CLOSING — max loss hit "
                    f"({total_pnl_pct:.2f}% <= -{max_loss_pct}%), {len(positions)} positions, PnL=${total_pnl:.2f}"
                )

            # 2. Profit target
            elif total_pnl_pct >= profit_target_pct:
                should_close = True
                close_reason = "FAN_BATCH_PROFIT"
                logger.info(
                    f"Fan batch {batch_id[:8]}: CLOSING — profit target hit "
                    f"({total_pnl_pct:.2f}% >= {profit_target_pct}%), {len(positions)} positions, PnL=${total_pnl:.2f}"
                )

            # 3. Trailing: activate when batch PnL > 0, close on callback
            # Only checked at candle close (trailing_enabled=True) to avoid
            # cutting profits on high-frequency price noise between candles.
            # Peak PnL is always tracked for state accuracy.
            elif total_pnl > 0:
                if not state["trailing_active"]:
                    state["trailing_active"] = True
                    state["peak_pnl"] = total_pnl
                    logger.info(
                        f"Fan batch {batch_id[:8]}: trailing activated, "
                        f"peak=${total_pnl:.2f} ({total_pnl_pct:.2f}%), {len(positions)} positions"
                    )
                else:
                    if total_pnl > state["peak_pnl"]:
                        state["peak_pnl"] = total_pnl

                    if trailing_enabled:
                        callback_amount = equity * trailing_callback_pct / 100
                        if total_pnl <= state["peak_pnl"] - callback_amount:
                            peak_pct = (state["peak_pnl"] / equity) * 100
                            should_close = True
                            close_reason = "FAN_BATCH_TRAILING"
                            logger.info(
                                f"Fan batch {batch_id[:8]}: trailing close — "
                                f"peak=${state['peak_pnl']:.2f} ({peak_pct:.2f}%), "
                                f"now=${total_pnl:.2f} ({total_pnl_pct:.2f}%), "
                                f"callback=${callback_amount:.2f}"
                            )
            else:
                # PnL went negative — deactivate trailing
                if state["trailing_active"]:
                    state["trailing_active"] = False
                    state["peak_pnl"] = 0.0

            # 4. Batch protective SL/TP — tighten SL and set TP on exchange
            # Only recalculate at candle close (trailing_enabled) to match backtest
            # timing. Between candles, MT5 holds the SL from the last candle close.
            sl_sync_enabled = first_metadata.get("batch_sl_sync_enabled", True)
            if sl_sync_enabled and not should_close and trailing_enabled:
                from src.core.trading_pairs import trading_pairs as _tp2

                batch_cs = _tp2.get_contract_size(positions[0][1].symbol)
                pair_info = _tp2.get(positions[0][1].symbol)

                # Group positions by side (batch may have BUY + SELL)
                sides: Dict[str, list] = {}
                for sig_id, trk in positions:
                    sides.setdefault(trk.side, []).append((sig_id, trk))

                for side, side_positions in sides.items():
                    total_qty = 0.0
                    weighted_sum = 0.0
                    for sig_id, trk in side_positions:
                        qty = (
                            trk.initial_quantity
                            if trk.initial_quantity > 0
                            else (
                                trk.position_size_usdt
                                / (_tp2.usd_price(trk.symbol, trk.executed_price) * batch_cs)
                                if trk.executed_price and batch_cs > 0
                                else 0
                            )
                        )
                        total_qty += qty
                        weighted_sum += trk.executed_price * qty

                    if total_qty <= 0 or batch_cs <= 0:
                        continue

                    avg_entry = weighted_sum / total_qty

                    # -- Protective TP: price where this side hits profit target --
                    # Distribute target proportionally by side qty share
                    side_share = len(side_positions) / len(positions)
                    target_pnl = equity * profit_target_pct / 100 * side_share
                    tp_offset = target_pnl / (total_qty * batch_cs)
                    if side == "BUY":
                        uniform_tp = avg_entry + tp_offset
                    else:
                        uniform_tp = avg_entry - tp_offset
                    if pair_info:
                        uniform_tp = pair_info.round_price(uniform_tp)

                    for sig_id, trk in side_positions:
                        last_tp = state.get("tp_synced_prices", {}).get(sig_id)
                        if last_tp is None or abs(uniform_tp - last_tp) > 0.01:
                            state.setdefault("tp_pending", {})[sig_id] = uniform_tp

                    # -- Protective SL: mirror trailing stop level --
                    if state["trailing_active"] and total_pnl > 0:
                        callback_amt = equity * trailing_callback_pct / 100
                        protected_pnl = (state["peak_pnl"] - callback_amt) * side_share

                        if protected_pnl > 0:
                            sl_offset = protected_pnl / (total_qty * batch_cs)
                            if side == "BUY":
                                uniform_sl = avg_entry + sl_offset
                            else:
                                uniform_sl = avg_entry - sl_offset
                            if pair_info:
                                uniform_sl = pair_info.round_price(uniform_sl)

                            for sig_id, trk in side_positions:
                                # Use initial SL as floor (not current stop_loss
                                # which may include previous protective SL updates)
                                floor_sl = trk.original_stop_loss or trk.stop_loss
                                if side == "BUY":
                                    new_sl = max(uniform_sl, floor_sl)
                                else:
                                    new_sl = min(uniform_sl, floor_sl)
                                if pair_info:
                                    new_sl = pair_info.round_price(new_sl)

                                last_synced = state["sl_synced_prices"].get(sig_id)
                                if last_synced is None or abs(new_sl - last_synced) > 0.001:
                                    state["sl_pending"][sig_id] = new_sl
                    else:
                        # Trailing not active or batch not in profit:
                        # reset protective SL back to initial levels
                        for sig_id, trk in side_positions:
                            last_synced = state.get("sl_synced_prices", {}).get(sig_id)
                            if last_synced is not None:
                                initial_sl = trk.original_stop_loss or trk.stop_loss
                                if initial_sl and abs(initial_sl - last_synced) > 0.001:
                                    state.setdefault("sl_pending", {})[sig_id] = initial_sl
                                    state.get("sl_synced_prices", {}).pop(sig_id, None)

            # --- Broker-side batch loss backstop ---
            #
            # Why. The batch_max_loss_pct limit lives ONLY in this function: it is
            # checked above and leads to a close performed by the bot. At the broker
            # each leg only has a catastrophic stop at 5-7 ATR, and summed over the
            # grid (measured: up to 37 legs) that takes the account far beyond the
            # 3-4% the limit is meant for. If the bot is unavailable — Redis down,
            # terminal disconnected, VPS rebooted — the grid has no batch
            # protection at all.
            #
            # So every leg gets a uniform stop at the price where the grid would
            # lose max_loss * backstop_mult.
            #
            # WHY WIDER THAN THE LIVE THRESHOLD. batch_sl_sync_enabled was once
            # disabled precisely because the broker stop closed intra-candle, while
            # the bot and the backtest decide at the candle close. A multiplier > 1
            # keeps the backstop safely behind the bot's decision: with a live bot
            # it never fires and does not affect parity, with a dead one it catches
            # the grid at a well-defined level.
            #
            # Never loosens: if the computed level is farther than the leg's own
            # catastrophic stop, the catastrophic stop is kept.
            backstop_on = first_metadata.get("batch_loss_backstop_enabled", True)
            backstop_mult = float(first_metadata.get("batch_loss_backstop_mult", 1.5) or 1.5)
            if backstop_on and not should_close and max_loss_pct > 0:
                from src.core.trading_pairs import trading_pairs as _tp3

                bs_cs = _tp3.get_contract_size(positions[0][1].symbol)
                bs_pair = _tp3.get(positions[0][1].symbol)

                bs_sides: Dict[str, list] = {}
                for sig_id, trk in positions:
                    bs_sides.setdefault(trk.side, []).append((sig_id, trk))

                for side, side_positions in bs_sides.items():
                    total_qty = 0.0
                    weighted_sum = 0.0
                    for sig_id, trk in side_positions:
                        qty = (
                            trk.initial_quantity
                            if trk.initial_quantity > 0
                            else (
                                trk.position_size_usdt
                                / (_tp3.usd_price(trk.symbol, trk.executed_price) * bs_cs)
                                if trk.executed_price and bs_cs > 0
                                else 0
                            )
                        )
                        total_qty += qty
                        weighted_sum += (trk.executed_price or 0) * qty
                    if total_qty <= 0 or bs_cs <= 0:
                        continue

                    avg_entry = weighted_sum / total_qty
                    # the loss budget is split between sides in proportion to
                    # the number of legs, as already done for the protective TP
                    side_share = len(side_positions) / len(positions)
                    loss_budget = equity * max_loss_pct / 100.0 * backstop_mult * side_share
                    sl_offset = loss_budget / (total_qty * bs_cs)
                    uniform_sl = avg_entry - sl_offset if side == "BUY" else avg_entry + sl_offset
                    if bs_pair:
                        uniform_sl = bs_pair.round_price(uniform_sl)

                    for sig_id, trk in side_positions:
                        catastrophe = trk.original_stop_loss or trk.stop_loss
                        if not catastrophe:
                            continue
                        # only ever tighten the stop, never loosen it
                        new_sl = (
                            max(uniform_sl, catastrophe)
                            if side == "BUY"
                            else min(uniform_sl, catastrophe)
                        )
                        if abs(new_sl - catastrophe) < 1e-9:
                            continue  # backstop is weaker — leave it alone
                        last = state.get("sl_synced_prices", {}).get(sig_id)
                        if last is None or abs(new_sl - last) > 0.001:
                            state.setdefault("sl_pending", {})[sig_id] = new_sl

            if should_close:
                # Close all positions in this batch
                for sig_id, tracked in positions:
                    current_price = self.current_prices.get(tracked.symbol, tracked.executed_price)
                    positions_to_close.append((sig_id, current_price, close_reason))

                # Clean up trailing state
                if batch_id in self._fan_batch_state:
                    del self._fan_batch_state[batch_id]

        # Clean up state for batches that no longer exist
        active_batch_ids = set()
        for sig_id, tracked in self.tracked_signals.items():
            if tracked.status != "EXECUTED" or not tracked.signal:
                continue
            if hasattr(tracked.signal, "metadata"):
                m = tracked.signal.metadata
            elif isinstance(tracked.signal, dict):
                m = tracked.signal.get("metadata")
            else:
                m = None
            if m:
                bid = m.get("fan_batch_id") or m.get("batch_id")
                if bid:
                    active_batch_ids.add(bid)
        stale = [bid for bid in self._fan_batch_state if bid not in active_batch_ids]
        for bid in stale:
            old_state = self._fan_batch_state[bid]
            if old_state.get("trailing_active") or old_state.get("peak_pnl", 0) > 0:
                logger.debug(
                    f"Fan batch {bid[:8]}: cleaning stale state "
                    f"(trailing={old_state.get('trailing_active')}, "
                    f"peak=${old_state.get('peak_pnl', 0):.2f})"
                )
            del self._fan_batch_state[bid]

        return positions_to_close

    async def _monitoring_loop(self) -> None:
        """Monitor open positions and check for SL/TP hits."""
        logger.info("Position monitoring loop started")
        import time

        while self.is_running:
            try:
                await self._check_positions()

                # Periodically sync with exchange to detect external closes
                if self.config.position_sync_enabled and self.connector:
                    current_time = time.time()
                    if (
                        current_time - self._last_position_sync
                        >= self.config.position_sync_interval
                    ):
                        await self._sync_positions_with_exchange()
                        self._last_position_sync = current_time

                # Periodically sync balance with exchange
                if self.config.balance_sync_enabled and self.connector:
                    current_time = time.time()
                    if current_time - self._last_balance_sync >= self.config.balance_sync_interval:
                        await self._sync_balance_with_exchange()
                        self._last_balance_sync = current_time

                # Process pending trailing stop updates (with debounce)
                if self.connector:
                    await self._process_pending_trailing_updates()
                    await self._sync_batch_protective_sl()

                await asyncio.sleep(self.config.monitoring_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitoring loop error: {e}")
                await asyncio.sleep(1)

        logger.info("Position monitoring loop stopped")

    @staticmethod
    def _signal_meta(sig) -> dict:
        """Signal metadata: a Signal for new positions, a dict for ones recovered after a restart, None."""
        if isinstance(sig, dict):
            meta = sig.get("metadata")
        else:
            meta = getattr(sig, "metadata", None)
        return meta if isinstance(meta, dict) else {}

    async def _check_positions(self) -> None:
        """Check all tracked signals for SL/TP/BE hits."""
        current_time = utcnow()
        signals_to_close = []
        signals_to_partial_close = []  # For partial TP exits
        be_syncs_pending = []  # BE syncs to execute after position checks

        # Check for daily reset (new day)
        self._check_daily_reset()

        # Check if daily target reached
        if self._check_daily_target():
            # Target just reached - handle positions
            await self._handle_daily_target_reached()

        # ── Fan/Grid Batch Close ──────────────────────────────────────────
        # Group executed positions by fan_batch_id and check batch-level exit.
        # Trailing is only checked at candle close (passed via is_candle_close
        # param). Between candles, only max_loss/profit_target are checked.
        trailing_on = getattr(self, "_is_candle_close_check", False)
        fan_batch_close_ids = await self._check_fan_batch_close(trailing_enabled=trailing_on)
        # Track which signal_ids came from batch close so we can alert on partial failures.
        # Batch close is designed to close the whole batch atomically (lock profits or
        # cut losses as a group) — if some close and others don't, the batch is broken.
        batch_close_signal_ids = {sid_tuple[0] for sid_tuple in fan_batch_close_ids}
        for sid in fan_batch_close_ids:
            signals_to_close.append(sid)  # (signal_id, exit_price, reason)

        for signal_id, tracked in list(self.tracked_signals.items()):
            if tracked.status != "EXECUTED":
                continue

            # Skip if already marked for batch close
            if any(sid == signal_id for sid, _, _ in signals_to_close):
                continue

            symbol = tracked.symbol
            current_price = self.current_prices.get(symbol)

            if current_price is None:
                continue

            # ── Time exit (parity with backtest portfolio_manager) ──────────
            # Opt-in via signal metadata 'time_exit_minutes': close by market
            # after N minutes from fill, regardless of SL/TP.
            # tracked.signal is a Signal for new positions and a dict for ones recovered after a
            # restart (position_recovery.py stores raw_signal as a dict). Calling .metadata on a
            # dict used to crash the whole monitoring loop on every iteration.
            time_exit = self._signal_meta(tracked.signal).get("time_exit_minutes")
            if time_exit and tracked.executed_at:
                age_min = (current_time - tracked.executed_at).total_seconds() / 60
                if age_min >= float(time_exit):
                    signals_to_close.append((signal_id, current_price, "time_exit"))
                    continue

            # Get candle high/low for wick detection (matches backtest behavior)
            candle_high = self.current_highs.get(symbol, current_price)
            candle_low = self.current_lows.get(symbol, current_price)

            # Update Break-Even manager with current price and candle high/low
            be_triggered = False
            if self.break_even_manager:
                be_status = self.break_even_manager.update_price(
                    signal_id, current_price, candle_high=candle_high, candle_low=candle_low
                )

                # Track max profit from BE manager
                if be_status and be_status.max_profit_percent > tracked.max_profit_percent:
                    tracked.max_profit_percent = be_status.max_profit_percent

                # When BE becomes ACTIVE, queue SL sync to exchange (executed after loop)
                if (
                    be_status
                    and be_status.state == BreakEvenState.ACTIVE
                    and not tracked.be_sl_synced
                ):
                    if self.connector:
                        be_syncs_pending.append((signal_id, tracked, be_status.stop_price))

                # Close position if BE was triggered (even if re-entry is pending)
                if be_status and be_status.state in (
                    BreakEvenState.TRIGGERED,
                    BreakEvenState.REENTRY_PENDING,
                ):
                    # BE was triggered - use current market price (not BE stop level).
                    # The exchange closes at market when SL triggers, so the actual
                    # fill price is near current_price, not the SL target.
                    # Note: re-entry will be tracked as separate position
                    signals_to_close.append((signal_id, current_price, "BREAK_EVEN"))
                    be_triggered = True

            if be_triggered:
                continue

            # Check expiry (skip in hedge mode if disabled)
            if not (self.config.hedge_mode and self.config.hedge_disable_expiry):
                age_seconds = (current_time - tracked.created_at).total_seconds()
                if age_seconds > self.config.signal_expiry_seconds:
                    signals_to_close.append((signal_id, current_price, "EXPIRED"))
                    continue

            # Check and apply DCA (before SL check - may improve average entry)
            if self.config.dca_enabled and tracked.dca_enabled:
                await self._check_and_apply_dca(signal_id, tracked, current_price)

            # Check stop loss (only if BE not active or not triggered)
            be_status = (
                self.break_even_manager.get_status(signal_id) if self.break_even_manager else None
            )
            be_is_active = be_status and be_status.state == BreakEvenState.ACTIVE

            # Skip internal SL monitoring if SL order exists on exchange
            # (exchange will handle the stop, no need for internal check)
            has_exchange_sl = tracked.sl_order_id is not None

            if tracked.side == "BUY":
                # If BE is active, use BE stop price instead of original SL
                stop_price = be_status.stop_price if be_is_active else tracked.stop_loss

                # SL check using candle_low (catches wicks, matches backtest)
                if candle_low <= stop_price and not has_exchange_sl:
                    exit_reason = "BREAK_EVEN" if be_is_active else "STOP_LOSS"
                    signals_to_close.append((signal_id, stop_price, exit_reason))
                    continue

                # Check take profits using candle_high (catches wicks)
                if self.config.partial_close_enabled:
                    tp_index = tracked.current_tp_index

                    if tp_index < 2:
                        # TP1 and TP2 (indices 0, 1): fixed take profits
                        if tp_index < len(tracked.take_profit):
                            tp = tracked.take_profit[tp_index]
                            if candle_high >= tp:
                                signals_to_partial_close.append((signal_id, tp, tp_index))

                    elif (
                        tp_index == 2
                        and self.config.trailing_tp_enabled
                        and tracked.remaining_percent > 0
                    ):
                        # After TP2: trailing take profit for remaining 40%
                        if self._check_trailing_take_profit(
                            signal_id, tracked, current_price, candle_high, candle_low
                        ):
                            # Trailing stop hit - close remaining position
                            exit_price = tracked.trailing_tp_stop or current_price
                            signals_to_close.append((signal_id, exit_price, "TRAILING_TAKE_PROFIT"))

                    elif tp_index >= 2 and not self.config.trailing_tp_enabled:
                        # Trailing disabled: use fixed TP3 (legacy behavior)
                        if tp_index < len(tracked.take_profit):
                            tp = tracked.take_profit[tp_index]
                            if candle_high >= tp:
                                signals_to_partial_close.append((signal_id, tp, tp_index))
                else:
                    # Full close mode (legacy): close all at first TP
                    for i, tp in enumerate(tracked.take_profit):
                        if candle_high >= tp:
                            signals_to_close.append((signal_id, tp, f"TAKE_PROFIT_{i+1}"))
                            break

            else:  # SELL
                # If BE is active, use BE stop price instead of original SL
                stop_price = be_status.stop_price if be_is_active else tracked.stop_loss

                # SL check using candle_high (catches wicks, matches backtest)
                if candle_high >= stop_price and not has_exchange_sl:
                    exit_reason = "BREAK_EVEN" if be_is_active else "STOP_LOSS"
                    signals_to_close.append((signal_id, stop_price, exit_reason))
                    continue

                # Check take profits using candle_low (catches wicks)
                if self.config.partial_close_enabled:
                    tp_index = tracked.current_tp_index

                    if tp_index < 2:
                        # TP1 and TP2 (indices 0, 1): fixed take profits
                        if tp_index < len(tracked.take_profit):
                            tp = tracked.take_profit[tp_index]
                            if candle_low <= tp:
                                signals_to_partial_close.append((signal_id, tp, tp_index))

                    elif (
                        tp_index == 2
                        and self.config.trailing_tp_enabled
                        and tracked.remaining_percent > 0
                    ):
                        # After TP2: trailing take profit for remaining 40%
                        if self._check_trailing_take_profit(
                            signal_id, tracked, current_price, candle_high, candle_low
                        ):
                            # Trailing stop hit - close remaining position
                            exit_price = tracked.trailing_tp_stop or current_price
                            signals_to_close.append((signal_id, exit_price, "TRAILING_TAKE_PROFIT"))

                    elif tp_index >= 2 and not self.config.trailing_tp_enabled:
                        # Trailing disabled: use fixed TP3 (legacy behavior)
                        if tp_index < len(tracked.take_profit):
                            tp = tracked.take_profit[tp_index]
                            if candle_low <= tp:
                                signals_to_partial_close.append((signal_id, tp, tp_index))
                else:
                    # Full close mode (legacy): close all at first TP
                    for i, tp in enumerate(tracked.take_profit):
                        if candle_low <= tp:
                            signals_to_close.append((signal_id, tp, f"TAKE_PROFIT_{i+1}"))
                            break

        # Sync BE stop losses to exchange (parallel, non-blocking for position checks)
        if be_syncs_pending:
            await asyncio.gather(
                *(
                    self._sync_be_sl_to_exchange(sid, trk, price)
                    for sid, trk, price in be_syncs_pending
                ),
                return_exceptions=True,
            )

        # Check pending re-entries
        if self.break_even_manager:
            await self._check_reentries()
            self.break_even_manager.cleanup_expired_reentries()

        # Process partial closes first (TP hits with partial close enabled)
        for signal_id, exit_price, tp_index in signals_to_partial_close:
            tracked = self.tracked_signals.get(signal_id)
            if tracked:
                symbol_lock = self._get_symbol_lock(tracked.symbol)
                async with symbol_lock:
                    # Re-check status after acquiring lock
                    if tracked.status == "EXECUTED":
                        await self._partial_close_signal(signal_id, exit_price, tp_index)

        # Close signals (full close: SL, BE, EXPIRED, or TP with partial disabled).
        # Each close is wrapped in try/except so one failure does not prevent
        # the rest of the batch from closing. Batch-close failures are tracked
        # separately and trigger a critical Telegram alert (a partially-closed
        # fan batch leaves positions trading outside their group-level risk rules).
        # Note: silent failures (connector returns {"success": False} without raise)
        # are already logged inside _close_signal/_close_remaining_position but
        # not caught here — that would require changing their return signatures.
        batch_close_failures: Dict[str, List[tuple]] = {}

        for signal_id, exit_price, exit_reason in signals_to_close:
            tracked = self.tracked_signals.get(signal_id)
            if not tracked:
                continue

            try:
                symbol_lock = self._get_symbol_lock(tracked.symbol)
                async with symbol_lock:
                    # Re-check status after acquiring lock
                    if tracked.status == "CLOSED":
                        continue
                    if tracked.remaining_percent < 100.0:
                        # Position was partially closed, close remainder with accumulated P&L
                        await self._close_remaining_position(signal_id, exit_price, exit_reason)
                    else:
                        await self._close_signal(signal_id, exit_price, exit_reason)
            except Exception as close_err:
                logger.error(
                    f"Failed to close signal {signal_id} ({exit_reason}): {close_err}",
                    exc_info=True,
                )
                # Track batch-close failures for post-loop alert.
                # The next monitor cycle will re-check batch criteria and retry
                # automatically, so we only surface this to the user and continue.
                if signal_id in batch_close_signal_ids:
                    batch_id = self._extract_batch_id(tracked) or "unknown"
                    batch_close_failures.setdefault(batch_id, []).append(
                        (signal_id, str(close_err))
                    )

        if batch_close_failures:
            await self._alert_batch_close_failures(batch_close_failures)

    @staticmethod
    def _extract_batch_id(tracked) -> Optional[str]:
        """Return fan_batch_id / batch_id from a tracked signal's metadata, or None."""
        if not tracked or not getattr(tracked, "signal", None):
            return None
        signal = tracked.signal
        if hasattr(signal, "metadata"):
            metadata = signal.metadata or {}
        elif isinstance(signal, dict):
            metadata = signal.get("metadata") or {}
        else:
            return None
        return metadata.get("fan_batch_id") or metadata.get("batch_id")

    async def _alert_batch_close_failures(
        self,
        failures_by_batch: Dict[str, List[tuple]],
    ) -> None:
        """Send critical Telegram alert when batch-close partially failed.

        Batch close is meant to exit an entire fan/grid group in one go
        (lock peak profit or cut loss). When only part of the batch closes,
        the survivors keep trading without batch-level risk rules — user
        needs to know immediately so they can decide whether to close manually.
        """
        if not getattr(self, "alert_manager", None):
            return

        try:
            from src.monitoring.alerts.alert_manager import TelegramAlert

            total_failures = sum(len(f) for f in failures_by_batch.values())
            lines = [
                f"{total_failures} position(s) failed to close during batch exit.",
                "",
            ]
            for batch_id, failures in failures_by_batch.items():
                short_id = batch_id[:8] if batch_id != "unknown" else "unknown"
                lines.append(f"• Batch {short_id}: {len(failures)} failure(s)")
                for sig_id, err_msg in failures[:3]:
                    err_short = err_msg[:80] + ("…" if len(err_msg) > 80 else "")
                    lines.append(f"    - {sig_id[:8]}: {err_short}")
                if len(failures) > 3:
                    lines.append(f"    …and {len(failures) - 3} more")

            lines.append("")
            lines.append(
                "Survivors are trading outside batch-level risk rules. "
                "Bot will retry on next monitor cycle; check manually if critical."
            )

            await self.alert_manager.send_alert(
                TelegramAlert(
                    type="system",
                    priority="critical",
                    title="⚠️ Batch Close Partial Failure",
                    message="\n".join(lines),
                    data={
                        "failed_count": str(total_failures),
                        "batches_affected": str(len(failures_by_batch)),
                    },
                )
            )
        except Exception as alert_err:
            logger.error(f"Failed to send batch-close failure alert: {alert_err}")

    async def _close_remaining_position(
        self, signal_id: str, exit_price: float, exit_reason: str
    ) -> None:
        """
        Close the remaining portion of a partially closed position.
        Used when SL/BE/TrailingTP hits after partial TPs were taken.
        """
        tracked = self.tracked_signals.get(signal_id)
        if not tracked or tracked.status == "CLOSED":
            return  # Already closed or not found

        # Cancel any remaining SL/TP orders on exchange
        exchange_close_failed = False
        if self.connector:
            await self._cancel_sl_tp_orders(tracked)

            # Check if position still exists before sending market order
            # This prevents duplicate close orders when exchange already closed via SL/TP
            if tracked.remaining_percent > 0 and tracked.position_size_usdt > 0:
                try:
                    # MT5: close by specific position ticket
                    if tracked.position_ticket and hasattr(
                        self.connector, "close_position_by_ticket"
                    ):
                        order_result = await self.connector.close_position_by_ticket(
                            position_ticket=tracked.position_ticket,
                            comment=f"Bot: {exit_reason}",
                            strategy=tracked.strategy_name,
                            signal_id=tracked.signal_id,
                        )
                        if order_result.get("success"):
                            fill_price = order_result.get("price")
                            if fill_price and fill_price > 0:
                                exit_price = float(fill_price)
                            logger.info(
                                f"📤 Remainder closed by ticket {tracked.position_ticket}: "
                                f"{tracked.symbol} fill@{exit_price:.4f} (reason: {exit_reason})"
                            )
                        else:
                            logger.error(
                                f"FAILED to close ticket {tracked.position_ticket}: "
                                f"{order_result.get('error')} — position still open!"
                            )
                            exchange_close_failed = True
                    else:
                        # Bybit / generic: verify and close by symbol+side
                        positions = await self.connector.get_positions(tracked.symbol)
                        has_position = False
                        for pos in positions:
                            if float(pos.get("size", 0)) > 0:
                                pos_side = pos.get("side", "").upper()
                                if (tracked.side == "BUY" and pos_side == "BUY") or (
                                    tracked.side == "SELL" and pos_side == "SELL"
                                ):
                                    has_position = True
                                    break

                        if not has_position:
                            logger.info(
                                f"📊 Position {tracked.symbol} already closed by exchange, skipping market order"
                            )
                        else:
                            close_side = "SELL" if tracked.side == "BUY" else "BUY"
                            position_idx = (
                                2
                                if (self.config.hedge_mode and tracked.side == "SELL")
                                else 1
                                if self.config.hedge_mode
                                else 0
                            )

                            order_result = await self.connector.place_order(
                                {
                                    "symbol": tracked.symbol,
                                    "side": close_side,
                                    "type": "MARKET",
                                    "quantity": 0,
                                    "reduce_only": True,
                                    "close_on_trigger": True,
                                    "position_idx": position_idx,
                                }
                            )

                            if order_result.get("success"):
                                logger.info(
                                    f"📤 Market close order sent: {close_side} ALL {tracked.symbol} "
                                    f"(reason: {exit_reason})"
                                )
                            else:
                                logger.error(
                                    f"FAILED to close {tracked.symbol}: "
                                    f"{order_result.get('error')} — position still open!"
                                )
                                exchange_close_failed = True
                except Exception as e:
                    logger.error(
                        f"Error sending close order for {tracked.symbol}: {e} "
                        f"— position may still be open!"
                    )
                    exchange_close_failed = True

        # If exchange close failed, do NOT finalize — let position sync retry later
        if exchange_close_failed:
            logger.error(
                f"Aborting remainder close for {tracked.symbol} ({signal_id[:8]}): "
                f"exchange order failed, keeping status EXECUTED for retry"
            )
            return

        # Calculate P&L for remaining portion
        # Note: leverage is NOT applied here because position_size_usdt already represents
        # the risk-adjusted notional position (P&L is simply notional × price_change)
        if tracked.side == "BUY":
            pnl_percent = ((exit_price - tracked.executed_price) / tracked.executed_price) * 100
        else:
            pnl_percent = ((tracked.executed_price - exit_price) / tracked.executed_price) * 100

        # Weighted P&L for remaining portion
        remaining_percent = tracked.remaining_percent
        weighted_pnl = pnl_percent * (remaining_percent / 100.0)

        # Calculate P&L in USDT for remaining portion
        remaining_position_usdt = tracked.position_size_usdt * (remaining_percent / 100.0)
        pnl_usdt = remaining_position_usdt * (pnl_percent / 100.0)

        # Add to realized P&L
        tracked.realized_pnl += weighted_pnl
        tracked.realized_pnl_usdt += pnl_usdt

        # Update balance for remainder (per-strategy if applicable)
        is_winner = pnl_usdt > 0
        self._update_balance(
            pnl_usdt,
            strategy_name=tracked.strategy_name,
            strategy_balance=tracked.strategy_balance,
            is_winner=is_winner,
        )

        # Save balance to database after remainder close
        self._save_balance_to_db(
            event_type="TRADE_CLOSE", trade_symbol=tracked.symbol, trade_pnl=pnl_usdt
        )

        # Record this close
        partial_close_info = {
            "tp_level": "REMAINDER",
            "exit_price": exit_price,
            "close_percent": remaining_percent,
            "pnl_percent": pnl_percent,
            "weighted_pnl": weighted_pnl,
            "pnl_usdt": pnl_usdt,
            "timestamp": utcnow().isoformat(),
            "remaining_percent": 0,
            "reason": exit_reason,
        }
        tracked.partial_closes.append(partial_close_info)
        tracked.remaining_percent = 0

        # Log the remainder close
        logger.info(
            f"📊 REMAINDER CLOSE: {tracked.side} {tracked.symbol} | "
            f"{exit_reason} @ {exit_price:.4f} | "
            f"Closed: {remaining_percent:.0f}% | P&L: {pnl_percent:+.2f}% (${pnl_usdt:+.2f})",
            extra={"log_type": "signal"},
        )

        # Notify via Telegram about remainder (if callback set)
        if self.on_partial_close:
            try:
                await self.on_partial_close(
                    signal_id,
                    "REMAINDER",
                    partial_close_info,
                    0,  # remaining percent is now 0
                    None,
                )
            except Exception as e:
                logger.error(f"Error in on_partial_close callback (remainder): {e}")

        # Finalize the position
        await self._finalize_partial_closed_signal(tracked, exit_price, exit_reason)

    async def close_signal(self, signal_id: str, exit_reason: str = "MANUAL_CLOSE") -> bool:
        """
        Public method to close a signal manually (from Telegram buttons, etc.)

        Args:
            signal_id: Signal ID to close
            exit_reason: Reason for closing

        Returns:
            True if signal was closed successfully
        """
        tracked = self.tracked_signals.get(signal_id)
        if not tracked or tracked.status == "CLOSED":
            return False

        # Acquire per-symbol lock to prevent race conditions
        symbol_lock = self._get_symbol_lock(tracked.symbol)
        async with symbol_lock:
            # Re-check after acquiring lock
            if tracked.status == "CLOSED":
                return False

            # Get current price
            current_price = self.current_prices.get(tracked.symbol)
            if current_price is None:
                logger.warning(
                    f"Cannot close signal {signal_id}: no current price for {tracked.symbol}"
                )
                return False

            # Close the signal
            await self._close_signal(signal_id, current_price, exit_reason)
            logger.info(f"Signal {signal_id} closed manually: {exit_reason}")
            return True

    async def _close_signal(
        self,
        signal_id: str,
        exit_price: float,
        exit_reason: str,
        send_market_order: bool = True,
        exchange_pnl: Optional[float] = None,
    ) -> None:
        """Close a tracked signal and record outcome.

        Args:
            signal_id: The signal ID to close
            exit_price: The exit price
            exit_reason: Reason for closing (STOP_LOSS, TAKE_PROFIT_1, etc.)
            send_market_order: Whether to send market order to close position
            exchange_pnl: If provided, use this PnL from exchange instead of calculating
        """
        tracked = self.tracked_signals.get(signal_id)
        if not tracked or tracked.status == "CLOSED":
            return  # Already closed or not found

        # Mark the triggered order as FILLED before cancelling others
        if exit_reason == "STOP_LOSS":
            await self._update_order_status_in_db(
                signal_id=signal_id,
                order_type="STOP_LOSS",
                status="FILLED",
                filled_price=exit_price,
                status_reason="Stop loss triggered",
            )
            # Clear SL order ID so it won't be cancelled
            tracked.sl_order_id = None
        elif exit_reason.startswith("TAKE_PROFIT"):
            # Extract TP number from exit_reason (e.g., 'TAKE_PROFIT_1' -> 1)
            try:
                tp_num = int(exit_reason.split("_")[-1])
                await self._update_order_status_in_db(
                    signal_id=signal_id,
                    order_type=f"TAKE_PROFIT_{tp_num}",
                    status="FILLED",
                    filled_price=exit_price,
                    status_reason=f"Take profit {tp_num} triggered",
                )
                # Clear TP order ID so it won't be cancelled
                if tracked.tp_order_ids and tp_num <= len(tracked.tp_order_ids):
                    tracked.tp_order_ids[tp_num - 1] = None
            except (ValueError, IndexError):
                pass
        elif exit_reason == "TRAILING_STOP":
            await self._update_order_status_in_db(
                signal_id=signal_id,
                order_type="TRAILING_STOP",
                status="FILLED",
                filled_price=exit_price,
                status_reason="Trailing stop triggered",
            )
            tracked.trailing_order_id = None

        # Cancel any remaining SL/TP orders on exchange
        exchange_close_failed = False
        if self.connector:
            await self._cancel_sl_tp_orders(tracked)

            # Send market order to close position on exchange
            # Skip if position was closed by exchange SL/TP (detected via fill notification)
            if send_market_order and tracked.position_size_usdt > 0 and tracked.executed_price > 0:
                try:
                    order_result = None

                    # MT5: close by specific position ticket (supports multi-position fan)
                    if tracked.position_ticket and hasattr(
                        self.connector, "close_position_by_ticket"
                    ):
                        order_result = await self.connector.close_position_by_ticket(
                            position_ticket=tracked.position_ticket,
                            comment=f"Bot: {exit_reason}",
                            strategy=tracked.strategy_name,
                            signal_id=tracked.signal_id,
                        )
                        if order_result.get("success"):
                            # Use actual fill price from exchange if available
                            fill_price = order_result.get("price")
                            if fill_price and fill_price > 0:
                                exit_price = float(fill_price)
                                logger.info(
                                    f"📤 Position closed by ticket {tracked.position_ticket}: "
                                    f"{tracked.symbol} fill@{exit_price:.4f} (reason: {exit_reason})"
                                )
                            else:
                                logger.info(
                                    f"📤 Position closed by ticket {tracked.position_ticket}: "
                                    f"{tracked.symbol} (reason: {exit_reason})"
                                )
                        else:
                            logger.error(
                                f"FAILED to close ticket {tracked.position_ticket}: "
                                f"{order_result.get('error')} — position still open!"
                            )
                            exchange_close_failed = True
                    else:
                        # Bybit / generic: close by symbol+side
                        close_side = "SELL" if tracked.side == "BUY" else "BUY"
                        position_idx = (
                            2
                            if (self.config.hedge_mode and tracked.side == "SELL")
                            else 1
                            if self.config.hedge_mode
                            else 0
                        )

                        order_result = await self.connector.place_order(
                            {
                                "symbol": tracked.symbol,
                                "side": close_side,
                                "type": "MARKET",
                                "quantity": 0,  # Close entire position - no dust
                                "reduce_only": True,
                                "close_on_trigger": True,
                                "position_idx": position_idx,
                            }
                        )

                        if order_result.get("success"):
                            logger.info(
                                f"📤 Market close order sent: {close_side} ALL {tracked.symbol} "
                                f"(reason: {exit_reason})"
                            )
                        else:
                            logger.error(
                                f"FAILED to close {tracked.symbol}: "
                                f"{order_result.get('error')} — position still open!"
                            )
                            exchange_close_failed = True
                except Exception as e:
                    logger.error(
                        f"Error sending close order for {tracked.symbol}: {e} "
                        f"— position may still be open!"
                    )
                    exchange_close_failed = True

        # If exchange close failed, do NOT mark as closed — let position sync retry later
        if exchange_close_failed:
            logger.error(
                f"Aborting close for {tracked.symbol} ({signal_id[:8]}): "
                f"exchange order failed, keeping status EXECUTED for retry"
            )
            return

        # Fetch real trade details from exchange (PnL, fees, funding)
        exchange_pnl_used = False
        if self.connector and hasattr(self.connector, "get_closed_trade_details"):
            try:
                # Small delay to allow exchange to process the close
                import asyncio

                await asyncio.sleep(0.5)

                entry_time = (
                    int(tracked.executed_at.timestamp() * 1000) if tracked.executed_at else None
                )
                trade_details = await self.connector.get_closed_trade_details(
                    symbol=tracked.symbol,
                    entry_order_id=tracked.exchange_order_id,
                    entry_time=entry_time,
                    entry_price=tracked.executed_price,
                )

                # Update tracked signal with exchange data
                if trade_details.get("closed_pnl") is not None:
                    tracked.exchange_pnl = trade_details["closed_pnl"]
                    exchange_pnl_used = True
                    if trade_details.get("exit_price"):
                        exit_price = trade_details["exit_price"]

                tracked.open_fee = trade_details.get("open_fee", 0.0)
                tracked.close_fee = trade_details.get("close_fee", 0.0)
                tracked.funding_fee = trade_details.get("funding_fee", 0.0)
                tracked.total_fees = trade_details.get("total_fees", 0.0)

                if trade_details.get("net_pnl") is not None:
                    tracked.net_pnl = trade_details["net_pnl"]

            except Exception as e:
                logger.warning(f"Could not fetch trade details from exchange: {e}")

        # Calculate P&L
        # Note: leverage is NOT applied here because position_size_usdt already represents
        # the risk-adjusted notional position (P&L is simply notional × price_change)
        if tracked.side == "BUY":
            pnl_percent = ((exit_price - tracked.executed_price) / tracked.executed_price) * 100
        else:
            pnl_percent = ((tracked.executed_price - exit_price) / tracked.executed_price) * 100

        # Priority for P&L source:
        # 1. exchange_pnl parameter (passed from _handle_external_close for orphans)
        # 2. tracked.exchange_pnl (from get_closed_trade_details)
        # 3. Calculated from price change (fallback)
        if exchange_pnl is not None:
            # Use P&L passed directly (for orphan positions)
            pnl_usdt = exchange_pnl
            if tracked.position_size_usdt > 0:
                pnl_percent = (pnl_usdt / tracked.position_size_usdt) * 100
        elif exchange_pnl_used and tracked.exchange_pnl is not None:
            pnl_usdt = tracked.exchange_pnl
            # Recalculate percent based on actual exchange PnL
            if tracked.position_size_usdt > 0:
                pnl_percent = (pnl_usdt / tracked.position_size_usdt) * 100
        else:
            # Calculate P&L in USDT (fallback)
            pnl_usdt = tracked.position_size_usdt * (pnl_percent / 100)

        # Update tracked signal
        tracked.status = "CLOSED"
        tracked.closed_at = utcnow()
        tracked.exit_price = exit_price
        tracked.exit_reason = exit_reason
        tracked.pnl_percent = pnl_percent
        tracked.pnl = pnl_usdt

        # Update statistics
        self.stats["signals_closed"] += 1
        self.stats["total_pnl"] += pnl_percent
        self.stats["total_pnl_usdt"] += pnl_usdt

        is_winner = pnl_percent > 0

        # Check if this is an orphan position (recovered from previous session)
        is_orphan = signal_id.startswith("ORPHAN_")

        # Update virtual balance (per-strategy if applicable)
        # For orphan positions: sync balance from exchange instead of adding P&L
        # (orphan P&L was already included in unrealized PnL at session start)
        if is_orphan:
            # Sync balance from exchange for accurate tracking
            try:
                if self.connector and hasattr(self.connector, "get_account_info"):
                    import asyncio

                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Schedule balance sync
                        asyncio.create_task(self._sync_balance_after_orphan_close())
                    logger.debug("Orphan position closed - will sync balance from exchange")
            except Exception as e:
                logger.debug(f"Could not schedule balance sync for orphan: {e}")
        else:
            self._update_balance(
                pnl_usdt,
                strategy_name=tracked.strategy_name,
                strategy_balance=tracked.strategy_balance,
                is_winner=is_winner,
            )

        if is_winner:
            self.stats["winners"] += 1
        else:
            self.stats["losers"] += 1

        # Update best/worst trade tracking
        if self.stats["best_trade"] is None or pnl_usdt > self.stats["best_trade"].get(
            "pnl", float("-inf")
        ):
            self.stats["best_trade"] = {"symbol": tracked.symbol, "pnl": pnl_usdt}
        if self.stats["worst_trade"] is None or pnl_usdt < self.stats["worst_trade"].get(
            "pnl", float("inf")
        ):
            self.stats["worst_trade"] = {"symbol": tracked.symbol, "pnl": pnl_usdt}

        # Save balance to database after trade close
        self._save_balance_to_db(
            event_type="TRADE_CLOSE", trade_symbol=tracked.symbol, trade_pnl=pnl_usdt
        )

        # Calculate holding time
        holding_time = (
            (tracked.closed_at - tracked.executed_at).total_seconds() if tracked.executed_at else 0
        )

        # Check if this is a re-entry signal (virtual signal, not in DB)
        is_reentry = "_reentry_" in signal_id

        # Update database (skip for re-entry signals - they don't exist in DB)
        if self.database and not is_reentry:
            try:
                self.database.update_signal_outcome(
                    signal_id=signal_id,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl=pnl_percent,  # Using percent as "pnl" for simplicity
                    pnl_percent=pnl_percent,
                    fees=tracked.total_fees,  # Real fees from exchange
                    max_profit_percent=tracked.max_profit_percent
                    if tracked.max_profit_percent > 0
                    else None,
                    open_fee=tracked.open_fee,
                    close_fee=tracked.close_fee,
                    funding_fee=tracked.funding_fee,
                    exchange_pnl=tracked.exchange_pnl,
                    net_pnl=tracked.net_pnl,
                )
            except Exception as e:
                logger.error(f"Error updating signal outcome in DB: {e}")

        # Update signal history (skip for re-entry signals)
        if not is_reentry:
            try:
                outcome = SignalOutcome(
                    signal_id=signal_id,
                    executed=True,
                    execution_timestamp=int(tracked.executed_at.timestamp() * 1000)
                    if tracked.executed_at
                    else None,
                    exit_timestamp=int(tracked.closed_at.timestamp() * 1000),
                    entry_price_actual=tracked.executed_price,
                    exit_price_actual=exit_price,
                    slippage_percent=self.config.slippage_percent,
                    pnl=pnl_percent,
                    pnl_percent=pnl_percent,
                    realized=True,
                    exit_reason=exit_reason,
                    metadata={"holding_time_seconds": holding_time},
                )
                self.signal_history.add_outcome(outcome)
            except Exception as e:
                logger.error(f"Error adding outcome to history: {e}")

        # Remove from Break-Even manager
        if self.break_even_manager:
            self.break_even_manager.remove_position(signal_id)

        # Notify strategy of trade close (for batch tracking, daily PnL limits)
        if self.strategy_manager and tracked.strategy_name:
            try:
                strategy = self.strategy_manager.get_strategy(tracked.strategy_name)
                if strategy and hasattr(strategy, "notify_trade_closed"):
                    # Extract metadata from signal (handles both Signal object and dict)
                    sig_metadata = None
                    if tracked.signal:
                        if hasattr(tracked.signal, "metadata"):
                            sig_metadata = tracked.signal.metadata
                        elif isinstance(tracked.signal, dict):
                            sig_metadata = tracked.signal.get("metadata", {})

                    strategy.notify_trade_closed(
                        symbol=tracked.symbol,
                        pnl_pct=pnl_percent,
                        exit_reason=exit_reason,
                        metadata=sig_metadata,
                        net_pnl=pnl_usdt,
                        exit_time=tracked.closed_at,
                    )
            except Exception as e:
                logger.warning(f"Failed to notify strategy of trade close: {e}")

        # Clean up batch state for closed position
        try:
            sig_metadata = None
            if tracked.signal:
                if hasattr(tracked.signal, "metadata"):
                    sig_metadata = tracked.signal.metadata
                elif isinstance(tracked.signal, dict):
                    sig_metadata = tracked.signal.get("metadata", {})
            if sig_metadata:
                batch_id = sig_metadata.get("fan_batch_id")
                if batch_id and batch_id in self._fan_batch_state:
                    state = self._fan_batch_state[batch_id]
                    state.get("sl_pending", {}).pop(signal_id, None)
                    state.get("tp_pending", {}).pop(signal_id, None)
                    state.get("sl_synced_prices", {}).pop(signal_id, None)
                    state.get("tp_synced_prices", {}).pop(signal_id, None)
        except Exception as e:
            logger.debug(f"Batch state cleanup error: {e}")

        # Log result with fees breakdown
        result_emoji = "✅" if pnl_percent > 0 else "❌"
        be_indicator = " [BE]" if exit_reason == "BREAK_EVEN" else ""
        balance_info = f" | Balance: ${self.stats['current_balance']:.2f}"

        # Build fees info string
        fees_info = ""
        if tracked.total_fees > 0:
            fees_info = f" | Fees: ${tracked.total_fees:.4f}"
            if tracked.funding_fee != 0:
                fees_info += f" (F: {tracked.funding_fee:+.4f})"

        # Show source of PnL
        pnl_source = " [EX]" if exchange_pnl_used else ""

        logger.info(
            f"{result_emoji} SIGNAL CLOSED{be_indicator}{pnl_source}: {tracked.side} {tracked.symbol} | "
            f"Entry: {tracked.executed_price:.4f} → Exit: {exit_price:.4f} | "
            f"P&L: {pnl_percent:+.2f}% (${pnl_usdt:+.2f}){fees_info} | Reason: {exit_reason}{balance_info} | "
            f"Hold: {holding_time:.0f}s",
            extra={"log_type": "signal"},
        )

        # Notify via Telegram (if callback set)
        if self.on_signal_closed:
            try:
                # Full close - create single partial info
                final_partial = {
                    "tp_level": "FULL",
                    "close_percent": 100,
                    "pnl_percent": pnl_percent,
                    "pnl_usdt": pnl_usdt,
                }
                await self.on_signal_closed(
                    signal_id, exit_reason, pnl_usdt, pnl_percent, final_partial
                )
            except Exception as e:
                logger.error(f"Error in on_signal_closed callback: {e}")

        # Remove from active tracking (keep in tracked_signals for history)
        # We'll clean up old ones periodically

    def _calculate_progressive_stop(self, tracked: TrackedSignal, tp_index: int) -> Optional[float]:
        """
        Calculate new stop loss based on which TP level was hit.

        Progressive trailing:
        - TP1 hit → SL moves to entry + lock_profit (BE with profit)
        - TP2 hit → SL moves to TP1 level
        - TP3 hit → SL moves to TP2 level

        Args:
            tracked: Tracked signal
            tp_index: Which TP was hit (0=TP1, 1=TP2, 2=TP3)

        Returns:
            New stop loss price or None
        """
        entry = tracked.executed_price
        take_profits = tracked.take_profit

        if tp_index == 0:
            # TP1 hit → lock 50% of TP1 distance (matches backtest)
            if take_profits and len(take_profits) > 0:
                tp1 = take_profits[0]
                tp1_distance = abs(tp1 - entry)
                lock_distance = tp1_distance * 0.5  # 50% of TP1 distance

                if tracked.side == "BUY":
                    new_sl = entry + lock_distance
                else:
                    new_sl = entry - lock_distance
            else:
                # Fallback to fixed percent
                lock_profit = self.config.break_even_lock_profit_percent / 100
                buffer = 0.0001
                if tracked.side == "BUY":
                    new_sl = entry * (1 + lock_profit + buffer)
                else:
                    new_sl = entry * (1 - lock_profit - buffer)

            return new_sl

        elif tp_index >= 1 and len(take_profits) > tp_index - 1:
            # TP2+ hit → move stop to previous TP level
            prev_tp = take_profits[tp_index - 1]

            # Add small buffer below/above TP
            buffer_pct = 0.001  # 0.1% buffer

            if tracked.side == "BUY":
                # For LONG: stop slightly below previous TP
                new_sl = prev_tp * (1 - buffer_pct)
            else:
                # For SHORT: stop slightly above previous TP
                new_sl = prev_tp * (1 + buffer_pct)

            return new_sl

        return None

    def _check_trailing_take_profit(
        self,
        signal_id: str,
        tracked: TrackedSignal,
        current_price: float,
        candle_high: Optional[float] = None,
        candle_low: Optional[float] = None,
    ) -> bool:
        """
        Check and update trailing take profit for remaining position.

        Called only when current_tp_index >= 2 (after TP2 hit).
        Trailing TP follows the price at a distance.

        NOTE: This method only updates the trailing stop price and queues exchange sync.
        The actual position close is handled by the exchange order (STOP_LIMIT) and
        detected via _sync_positions_with_exchange when the order fills.
        We do NOT do internal "hit" detection to avoid race conditions.

        Args:
            signal_id: Signal ID
            tracked: Tracked signal
            current_price: Current market price
            candle_high: Candle high for wick detection (LONG max tracking)
            candle_low: Candle low for wick detection (SHORT min tracking)

        Returns:
            Always False - close is detected via exchange position sync
        """

        # Activate trailing if not active yet
        if not tracked.trailing_tp_active:
            tracked.trailing_tp_active = True
            # Use candle high/low for initial max price (catches wicks)
            if tracked.side == "BUY":
                tracked.trailing_tp_max_price = (
                    candle_high if candle_high is not None else current_price
                )
            else:
                tracked.trailing_tp_max_price = (
                    candle_low if candle_low is not None else current_price
                )

            # Calculate initial trailing stop
            distance = self.config.trailing_tp_distance_percent / 100
            min_profit = self.config.trailing_tp_min_profit_percent / 100

            if tracked.side == "BUY":
                initial_stop = current_price * (1 - distance)
                be_price = tracked.executed_price * (1 + min_profit)
                initial_stop = max(initial_stop, be_price)
            else:
                initial_stop = current_price * (1 + distance)
                be_price = tracked.executed_price * (1 - min_profit)
                initial_stop = min(initial_stop, be_price)

            tracked.trailing_tp_stop = initial_stop
            tracked.trailing_tp_pending_price = initial_stop  # Queue for sync

            logger.info(
                f"{P.TRAIL} TP START {tracked.side} {tracked.symbol} @ {current_price:.4f} stop={initial_stop:.4f}",
                extra={"log_type": "signal"},
            )

            return False

        distance = self.config.trailing_tp_distance_percent / 100
        min_profit = self.config.trailing_tp_min_profit_percent / 100

        if tracked.side == "BUY":
            # Update max price using candle high (catches wicks)
            check_high = candle_high if candle_high is not None else current_price
            if check_high > tracked.trailing_tp_max_price:
                tracked.trailing_tp_max_price = check_high

            # Calculate trailing stop (distance below max)
            new_stop = tracked.trailing_tp_max_price * (1 - distance)

            # Ensure trailing stop >= BE price (guaranteed profit)
            be_price = tracked.executed_price * (1 + min_profit)
            new_stop = max(new_stop, be_price)

            # Update stop only if higher (trailing up)
            old_stop = tracked.trailing_tp_stop
            if old_stop is None or new_stop > old_stop:
                tracked.trailing_tp_stop = new_stop

                # Log significant moves (every 0.5%+)
                if old_stop is not None:
                    change_pct = abs((new_stop - old_stop) / old_stop * 100)
                    if change_pct >= 0.02:
                        logger.debug(
                            f"{P.TRAIL} UPD {tracked.symbol} max={tracked.trailing_tp_max_price:.4f} "
                            f"stop={old_stop:.4f}→{new_stop:.4f}",
                            extra={"log_type": "signal"},
                        )
                        # Queue for exchange sync (with debounce)
                        tracked.trailing_tp_pending_price = new_stop

        else:  # SELL/SHORT
            # Update min price using candle low (catches wicks)
            check_low = candle_low if candle_low is not None else current_price
            if tracked.trailing_tp_max_price is None or check_low < tracked.trailing_tp_max_price:
                tracked.trailing_tp_max_price = check_low

            # Calculate trailing stop (distance above min)
            new_stop = tracked.trailing_tp_max_price * (1 + distance)

            # Ensure trailing stop <= BE price (guaranteed profit for short)
            be_price = tracked.executed_price * (1 - min_profit)
            new_stop = min(new_stop, be_price)

            # Update stop only if lower (trailing down)
            old_stop = tracked.trailing_tp_stop
            if old_stop is None or new_stop < old_stop:
                tracked.trailing_tp_stop = new_stop

                # Log significant moves
                if old_stop is not None:
                    change_pct = abs((new_stop - old_stop) / old_stop * 100)
                    if change_pct >= 0.02:
                        logger.debug(
                            f"{P.TRAIL} UPD {tracked.symbol} min={tracked.trailing_tp_max_price:.4f} "
                            f"stop={old_stop:.4f}→{new_stop:.4f}",
                            extra={"log_type": "signal"},
                        )
                        # Queue for exchange sync (with debounce)
                        tracked.trailing_tp_pending_price = new_stop

        # Never return True - let exchange order handle the close
        # Position close will be detected by _sync_positions_with_exchange
        return False

    async def _process_pending_trailing_updates(self) -> None:
        """
        Process pending trailing stop order updates with debounce.

        This method is called from the monitoring loop to sync trailing stop
        orders to the exchange without spamming requests.
        """
        import time

        # Debounce interval (minimum seconds between syncs per symbol)
        TRAILING_SYNC_INTERVAL = 0.5

        current_time = time.time()

        for signal_id, tracked in list(self.tracked_signals.items()):
            if tracked.status != "EXECUTED":
                continue

            # Check if there's a pending trailing stop update
            if not tracked.trailing_tp_pending_price:
                continue

            # Check debounce - don't sync too frequently
            time_since_last_sync = current_time - tracked.trailing_tp_last_sync
            if time_since_last_sync < TRAILING_SYNC_INTERVAL:
                continue

            # Process the pending update
            pending_price = tracked.trailing_tp_pending_price

            try:
                success = await self._place_trailing_stop_order(tracked, pending_price)
                if success:
                    tracked.trailing_tp_last_sync = current_time
                    tracked.trailing_tp_pending_price = None  # Clear only after confirmed sync
                else:
                    logger.debug(f"Trailing stop sync failed for {tracked.symbol}, will retry")
            except Exception as e:
                logger.error(f"Error syncing trailing stop for {tracked.symbol}: {e}")

    async def _sync_batch_protective_sl(self) -> None:
        """
        Sync batch-level protective SL and TP to exchange with debounce.

        When a fan batch is in profit, this tightens the exchange SL for each
        position so that if the bot crashes, the exchange catches some profit.
        Also sets a protective TP at the batch profit target level.
        Runs every batch_sl_sync_interval seconds (default 30s).
        """
        import time

        if not self.connector:
            return

        current_time = time.time()

        for batch_id, state in list(self._fan_batch_state.items()):
            sl_pending = state.get("sl_pending", {})
            tp_pending = state.get("tp_pending", {})
            if not sl_pending and not tp_pending:
                continue

            # Get sync interval from any pending position's metadata
            any_sig_id = next(iter(sl_pending), next(iter(tp_pending), None))
            if not any_sig_id:
                continue
            tracked = self.tracked_signals.get(any_sig_id)
            if not tracked:
                state["sl_pending"] = {}
                state["tp_pending"] = {}
                continue

            metadata = None
            if tracked.signal:
                if hasattr(tracked.signal, "metadata"):
                    metadata = tracked.signal.metadata
                elif isinstance(tracked.signal, dict):
                    metadata = tracked.signal.get("metadata", {})
            sync_interval = (metadata or {}).get("batch_sl_sync_interval", 30.0)

            # Debounce check
            last_sync = state.get("sl_last_sync_time", 0.0)
            if current_time - last_sync < sync_interval:
                continue

            # Merge all signal IDs that need updates (SL and/or TP)
            all_sig_ids = set(sl_pending.keys()) | set(tp_pending.keys())

            synced_count = 0
            failed_sl = {}
            failed_tp = {}

            for sig_id in all_sig_ids:
                trk = self.tracked_signals.get(sig_id)
                if not trk or trk.status != "EXECUTED":
                    continue

                position_idx = 1 if trk.side == "BUY" else 2
                current_price = self.current_prices.get(trk.symbol)

                new_sl = sl_pending.get(sig_id)
                new_tp = tp_pending.get(sig_id)

                # Validate SL: must not be beyond current price
                if new_sl and current_price:
                    if trk.side == "BUY" and new_sl >= current_price:
                        new_sl = current_price * 0.998
                    elif trk.side == "SELL" and new_sl <= current_price:
                        new_sl = current_price * 1.002

                # Validate TP: must be beyond current price
                if new_tp and current_price:
                    if trk.side == "BUY" and new_tp <= current_price:
                        new_tp = None  # Skip TP, price already past target
                    elif trk.side == "SELL" and new_tp >= current_price:
                        new_tp = None

                try:
                    kwargs = {
                        "symbol": trk.symbol,
                        "position_idx": position_idx,
                    }
                    if new_sl:
                        kwargs["stop_loss"] = new_sl
                    if new_tp:
                        kwargs["take_profit"] = new_tp
                    if trk.position_ticket:
                        kwargs["position_ticket"] = trk.position_ticket

                    # Single API call updates both SL and TP
                    result = await self.connector.set_trading_stop(**kwargs)

                    if result and result.get("success"):
                        if new_sl:
                            trk.stop_loss = new_sl
                            trk.sl_order_id = "TRADING_STOP"
                            state["sl_synced_prices"][sig_id] = new_sl
                        if new_tp:
                            if "tp_synced_prices" not in state:
                                state["tp_synced_prices"] = {}
                            state["tp_synced_prices"][sig_id] = new_tp
                        synced_count += 1
                    else:
                        error = result.get("error", "Unknown") if result else "No response"
                        logger.warning(
                            f"Batch SL/TP sync failed for {trk.symbol} "
                            f"ticket={trk.position_ticket}: {error}"
                        )
                        if new_sl:
                            failed_sl[sig_id] = new_sl
                        if new_tp:
                            failed_tp[sig_id] = new_tp
                except Exception as e:
                    logger.error(f"Error syncing batch SL/TP for {trk.symbol}: {e}")
                    if new_sl:
                        failed_sl[sig_id] = new_sl
                    if new_tp:
                        failed_tp[sig_id] = new_tp

            # Update state
            state["sl_pending"] = failed_sl
            state["tp_pending"] = failed_tp
            state["sl_last_sync_time"] = current_time

            if synced_count > 0:
                failed_total = len(failed_sl) + len(failed_tp)
                logger.info(
                    f"Batch {batch_id[:8]}: protective SL/TP synced "
                    f"for {synced_count} positions"
                    f"{f', {failed_total} failed' if failed_total else ''}"
                )

    # =========================================================================
    # DCA (Dollar Cost Averaging) - Add to losing positions
    # =========================================================================

    async def _check_and_apply_dca(
        self, signal_id: str, tracked: TrackedSignal, current_price: float
    ) -> bool:
        """
        Check if DCA conditions are met and apply DCA if so.

        DCA adds to a losing position at predetermined price levels to lower
        the average entry price, making it easier to exit at breakeven or profit.

        Args:
            signal_id: Signal ID
            tracked: Tracked signal with DCA settings
            current_price: Current market price

        Returns:
            True if DCA was applied, False otherwise
        """
        # Check if already at max DCA level
        if tracked.dca_level >= tracked.dca_max_levels:
            return False

        # Calculate current loss percentage
        if tracked.side == "BUY":
            loss_pct = (tracked.entry_price - current_price) / tracked.entry_price * 100
        else:  # SELL
            loss_pct = (current_price - tracked.entry_price) / tracked.entry_price * 100

        # Check if loss is positive (position is in loss)
        if loss_pct <= 0:
            return False

        # Calculate required loss for next DCA level
        next_dca_level = tracked.dca_level + 1
        required_loss_pct = tracked.dca_threshold * next_dca_level

        if loss_pct < required_loss_pct:
            # Not at next DCA level yet, but track extreme for retracement mode
            if tracked.dca_require_retracement:
                if tracked.side == "BUY":
                    if (
                        tracked.dca_extreme_price is None
                        or current_price < tracked.dca_extreme_price
                    ):
                        tracked.dca_extreme_price = current_price
                else:  # SELL
                    if (
                        tracked.dca_extreme_price is None
                        or current_price > tracked.dca_extreme_price
                    ):
                        tracked.dca_extreme_price = current_price
            return False

        # Check for retracement-based DCA
        if tracked.dca_require_retracement:
            extreme_price = tracked.dca_extreme_price
            touched_levels = set(tracked.dca_touched_levels)

            # First time at this level - mark as touched and set extreme
            if extreme_price is None:
                tracked.dca_extreme_price = current_price
                touched_levels.add(next_dca_level)
                tracked.dca_touched_levels = list(touched_levels)
                logger.debug(
                    f"[DCA] Level {next_dca_level} touched for {tracked.symbol}, waiting for retracement"
                )
                return False

            # Check if next level was previously touched
            if next_dca_level not in touched_levels:
                touched_levels.add(next_dca_level)
                tracked.dca_touched_levels = list(touched_levels)
                # Update extreme price
                if tracked.side == "BUY":
                    if current_price < extreme_price:
                        tracked.dca_extreme_price = current_price
                else:
                    if current_price > extreme_price:
                        tracked.dca_extreme_price = current_price
                logger.debug(
                    f"[DCA] Level {next_dca_level} touched for {tracked.symbol}, waiting for retracement"
                )
                return False

            # Check for retracement (price moved back toward entry)
            if tracked.side == "BUY":
                if current_price <= extreme_price:
                    tracked.dca_extreme_price = current_price
                    return False
            else:  # SELL
                if current_price >= extreme_price:
                    tracked.dca_extreme_price = current_price
                    return False

            logger.info(
                f"[DCA] Retracement confirmed for {tracked.symbol}: "
                f"extreme={extreme_price:.4f}, current={current_price:.4f}"
            )

        # DCA trend filter: skip if strong trend against position
        if tracked.dca_use_trend_filter:
            trend_threshold_mult = 2.0
            if loss_pct > tracked.dca_threshold * trend_threshold_mult:
                logger.debug(
                    f"[DCA] Skipped for {tracked.symbol}: strong trend "
                    f"(loss {loss_pct:.2f}% > {tracked.dca_threshold * trend_threshold_mult:.2f}%)"
                )
                return False

        # DCA RSI filter: only DCA if entry RSI was very extreme
        if tracked.dca_rsi_filter:
            entry_rsi = tracked.entry_rsi
            rsi_threshold = tracked.dca_rsi_extreme_threshold

            if tracked.side == "BUY" and entry_rsi > rsi_threshold:
                logger.debug(
                    f"[DCA] Skipped for {tracked.symbol} BUY: entry RSI {entry_rsi:.1f} > {rsi_threshold}"
                )
                return False
            elif tracked.side == "SELL" and entry_rsi < (100 - rsi_threshold):
                logger.debug(
                    f"[DCA] Skipped for {tracked.symbol} SELL: entry RSI {entry_rsi:.1f} < {100 - rsi_threshold}"
                )
                return False

        # Check if we have enough balance for DCA
        if tracked.initial_quantity <= 0:
            logger.warning(f"[DCA] Skipped for {tracked.symbol}: no initial_quantity set")
            return False

        additional_qty = tracked.initial_quantity * (tracked.dca_size_multiplier - 1.0)
        additional_notional = additional_qty * current_price
        additional_margin = additional_notional / self.config.leverage

        min_available = self.current_balance * self.config.dca_min_balance_percent
        if additional_margin > (self.current_balance - min_available):
            logger.debug(
                f"[DCA] Skipped for {tracked.symbol}: insufficient margin "
                f"(need ${additional_margin:.2f}, have ${self.current_balance:.2f})"
            )
            return False

        # Apply DCA
        return await self._execute_dca(signal_id, tracked, current_price, additional_qty)

    async def _execute_dca(
        self, signal_id: str, tracked: TrackedSignal, current_price: float, additional_qty: float
    ) -> bool:
        """
        Execute a DCA order to add to the position.

        Args:
            signal_id: Signal ID
            tracked: Tracked signal
            current_price: Current market price
            additional_qty: Quantity to add

        Returns:
            True if DCA was successful, False otherwise
        """
        symbol = tracked.symbol
        side = tracked.side
        old_entry = tracked.entry_price
        old_qty = tracked.initial_quantity

        # Calculate current total quantity (accounting for partial closes)
        current_qty_pct = tracked.remaining_percent / 100.0
        current_qty = old_qty * current_qty_pct

        try:
            if self.connector:
                # Place market order for DCA
                order_params = {
                    "symbol": symbol,
                    "side": side,  # Same side as original position
                    "type": "Market",
                    "qty": additional_qty,
                    "position_idx": 1 if side == "BUY" else 2,  # Hedge mode
                }

                logger.info(
                    f"[DCA] Placing order for {symbol}: {side} {additional_qty:.4f} @ market"
                )

                order_result = await self.connector.place_order(order_params)

                if not order_result or order_result.get("retCode") != 0:
                    error_msg = (
                        order_result.get("retMsg", "Unknown error")
                        if order_result
                        else "No response"
                    )
                    logger.error(f"[DCA] Order failed for {symbol}: {error_msg}")
                    return False

                # Get actual fill price
                fill_price = float(order_result.get("result", {}).get("avgPrice", current_price))

            else:
                # Paper trading - use current price
                fill_price = current_price

            # Calculate new average entry price
            old_notional = current_qty * old_entry
            new_notional = additional_qty * fill_price
            total_qty = current_qty + additional_qty
            new_avg_entry = (old_notional + new_notional) / total_qty

            # Update tracked signal
            old_level = tracked.dca_level
            tracked.dca_level = old_level + 1
            tracked.entry_price = new_avg_entry
            tracked.initial_quantity = total_qty / current_qty_pct  # Scale back for 100%

            # Update position size tracking
            additional_margin = (additional_qty * fill_price) / self.config.leverage
            tracked.position_size_usdt += additional_qty * fill_price
            self.current_balance -= additional_margin

            # Update take profits based on new entry
            if tracked.dca_recalc_tp and tracked.take_profit:
                tp_percents = []
                for tp in tracked.take_profit:
                    if tracked.side == "BUY":
                        tp_pct = (tp - old_entry) / old_entry * 100
                    else:
                        tp_pct = (old_entry - tp) / old_entry * 100
                    tp_percents.append(tp_pct)

                if tracked.side == "BUY":
                    tracked.take_profit = [new_avg_entry * (1 + tp / 100) for tp in tp_percents]
                else:
                    tracked.take_profit = [new_avg_entry * (1 - tp / 100) for tp in tp_percents]

            # Update stop loss with tightening factor
            if tracked.dca_sl_tighten_factor and tracked.dca_sl_tighten_factor < 1.0:
                original_sl = tracked.original_stop_loss or tracked.stop_loss
                if tracked.side == "BUY":
                    sl_distance = old_entry - original_sl
                    new_sl = new_avg_entry - (sl_distance * tracked.dca_sl_tighten_factor)
                else:
                    sl_distance = original_sl - old_entry
                    new_sl = new_avg_entry + (sl_distance * tracked.dca_sl_tighten_factor)

                old_sl = tracked.stop_loss
                tracked.stop_loss = new_sl

                # Update SL order on exchange
                if self.connector and tracked.sl_order_id:
                    await self._update_sl_order(tracked, new_sl)

                logger.info(f"[DCA] SL updated for {symbol}: {old_sl:.4f} -> {new_sl:.4f}")

            # Update break-even manager with new entry
            if self.break_even_manager:
                self.break_even_manager.update_entry_price(signal_id, new_avg_entry)
                tracked.be_sl_synced = False  # Need to re-sync BE

            logger.info(
                f"{P.DCA} {side} {symbol}: level {old_level} -> {tracked.dca_level}, "
                f"qty {current_qty:.4f} -> {total_qty:.4f}, "
                f"entry {old_entry:.4f} -> {new_avg_entry:.4f}, "
                f"loss {((old_entry - current_price) / old_entry * 100) if side == 'BUY' else ((current_price - old_entry) / old_entry * 100):.2f}% -> "
                f"{((new_avg_entry - current_price) / new_avg_entry * 100) if side == 'BUY' else ((current_price - new_avg_entry) / new_avg_entry * 100):.2f}%"
            )

            return True

        except Exception as e:
            logger.error(f"[DCA] Error executing DCA for {symbol}: {e}")
            return False

    async def _update_sl_order(self, tracked: TrackedSignal, new_sl: float) -> bool:
        """Update stop loss on exchange via trading-stop API (DCA version)."""
        if not self.connector or not tracked.sl_order_id:
            return False

        try:
            position_idx = 1 if tracked.side == "BUY" else 2

            sl_dca_kwargs = {
                "symbol": tracked.symbol,
                "position_idx": position_idx,
                "stop_loss": new_sl,
            }
            if tracked.position_ticket:
                sl_dca_kwargs["position_ticket"] = tracked.position_ticket
            sl_result = await self.connector.set_trading_stop(**sl_dca_kwargs)

            if sl_result and sl_result.get("success"):
                tracked.sl_order_id = "TRADING_STOP"
                logger.info(
                    f"[DCA] SL updated: {tracked.symbol} | " f"New SL: {new_sl:.4f} (market)"
                )
                return True
            else:
                error = sl_result.get("error", "Unknown") if sl_result else "No response"
                logger.error(f"Failed to update SL: {error}")
                return False

        except Exception as e:
            logger.error(f"Error updating SL order for {tracked.symbol}: {e}")
            return False

    async def _partial_close_signal(self, signal_id: str, exit_price: float, tp_index: int) -> None:
        """
        Partially close a position at take profit level.

        Args:
            signal_id: Signal ID
            exit_price: Current price
            tp_index: Which TP level was hit (0, 1, 2)
        """
        tracked = self.tracked_signals.get(signal_id)
        if not tracked:
            return

        # Get close percent for this TP level
        close_percents = self.config.partial_close_percents
        if tp_index >= len(close_percents):
            # No more partial closes, do full close
            await self._close_signal(signal_id, exit_price, f"TAKE_PROFIT_{tp_index + 1}")
            return

        close_percent = close_percents[tp_index]

        # Mark TP order as FILLED in database
        tp_level = tp_index + 1  # tp_index is 0-based, TP levels are 1-based
        await self._update_order_status_in_db(
            signal_id=signal_id,
            order_type=f"TAKE_PROFIT_{tp_level}",
            status="FILLED",
            filled_price=exit_price,
            status_reason=f"Take profit {tp_level} hit",
        )
        # Clear the executed TP order ID
        if tracked.tp_order_ids and tp_index < len(tracked.tp_order_ids):
            tracked.tp_order_ids[tp_index] = None

        # Note: Exchange executes the TP order automatically
        # After TP execution, we need to:
        # 1. Update the SL order quantity (remaining position)
        # 2. Remove the executed TP order from tracking
        if self.connector and tracked.sl_order_id:
            # Reduce SL quantity to match remaining position after this partial close
            new_remaining = tracked.remaining_percent - close_percent
            if new_remaining > 0:
                asyncio.create_task(self._adjust_sl_quantity_after_partial(tracked, new_remaining))

        # Calculate how much of the original position we're closing
        # e.g., if remaining is 100% and close_percent is 40%, we close 40%
        # if remaining is 60% and close_percent is 40%, we close 40% (which is 2/3 of remaining)
        position_percent_to_close = close_percent

        # Calculate P&L for this partial close
        # Note: leverage is NOT applied here because position_size_usdt already represents
        # the risk-adjusted notional position (P&L is simply notional × price_change)
        if tracked.side == "BUY":
            pnl_percent = ((exit_price - tracked.executed_price) / tracked.executed_price) * 100
        else:
            pnl_percent = ((tracked.executed_price - exit_price) / tracked.executed_price) * 100

        # Weighted P&L for this partial close
        weighted_pnl = pnl_percent * (position_percent_to_close / 100.0)

        # Calculate P&L in USDT for this partial close
        partial_position_usdt = tracked.position_size_usdt * (position_percent_to_close / 100.0)
        pnl_usdt = partial_position_usdt * (pnl_percent / 100.0)

        # Update tracked signal
        tracked.realized_pnl += weighted_pnl
        tracked.realized_pnl_usdt += pnl_usdt
        tracked.remaining_percent -= position_percent_to_close
        tracked.current_tp_index = tp_index + 1

        # Update balance immediately for partial close (per-strategy if applicable)
        # Note: partial closes at TP are always winners
        self._update_balance(
            pnl_usdt,
            strategy_name=tracked.strategy_name,
            strategy_balance=tracked.strategy_balance,
            is_winner=True,
        )

        # Save balance to database after partial close
        self._save_balance_to_db(
            event_type="TRADE_CLOSE", trade_symbol=tracked.symbol, trade_pnl=pnl_usdt
        )

        # Record partial close
        partial_close_info = {
            "tp_level": tp_index + 1,
            "exit_price": exit_price,
            "close_percent": position_percent_to_close,
            "pnl_percent": pnl_percent,
            "weighted_pnl": weighted_pnl,
            "pnl_usdt": pnl_usdt,
            "timestamp": utcnow().isoformat(),
            "remaining_percent": tracked.remaining_percent,
        }
        tracked.partial_closes.append(partial_close_info)

        # Log partial close
        logger.info(
            f"📊 PARTIAL CLOSE: {tracked.side} {tracked.symbol} | "
            f"TP{tp_index + 1} @ {exit_price:.4f} | "
            f"Closed: {position_percent_to_close:.0f}% | P&L: {pnl_percent:+.2f}% (${pnl_usdt:+.2f}) | "
            f"Remaining: {tracked.remaining_percent:.0f}%",
            extra={"log_type": "signal"},
        )

        # Notify via Telegram (if callback set)
        if self.on_partial_close:
            try:
                # Get new stop loss (progressive trailing)
                new_sl = self._calculate_progressive_stop(tracked, tp_index)
                await self.on_partial_close(
                    signal_id, tp_index + 1, partial_close_info, tracked.remaining_percent, new_sl
                )
            except Exception as e:
                logger.error(f"Error in on_partial_close callback: {e}")

        # If position fully closed, finalize
        if tracked.remaining_percent <= 0:
            await self._finalize_partial_closed_signal(
                tracked, exit_price, f"TAKE_PROFIT_{tp_index + 1}"
            )
        else:
            # Progressive stop loss trailing based on TP level hit
            new_sl = self._calculate_progressive_stop(tracked, tp_index)
            if new_sl:
                old_sl = tracked.stop_loss
                tracked.stop_loss = new_sl
                logger.info(
                    f"🛡️ STOP TRAILED: {tracked.symbol} | TP{tp_index + 1} hit | "
                    f"SL: {old_sl:.4f} → {new_sl:.4f}",
                    extra={"log_type": "signal"},
                )

    async def _finalize_partial_closed_signal(
        self, tracked: TrackedSignal, final_exit_price: float, exit_reason: str
    ) -> None:
        """Finalize a signal that was closed through partial exits."""
        # Total P&L is the accumulated realized P&L
        pnl_usdt = tracked.realized_pnl_usdt

        # Calculate real ROI (P&L USDT / position size)
        roi_percent = (
            (pnl_usdt / tracked.position_size_usdt * 100) if tracked.position_size_usdt > 0 else 0.0
        )

        # Update tracked signal
        tracked.status = "CLOSED"
        tracked.closed_at = utcnow()
        tracked.exit_price = final_exit_price
        tracked.exit_reason = exit_reason
        tracked.pnl_percent = roi_percent  # Store real ROI, not weighted average
        tracked.pnl = pnl_usdt

        # Update statistics (balance already updated by partial closes)
        self.stats["signals_closed"] += 1
        self.stats["total_pnl"] += roi_percent  # Use real ROI for stats
        self.stats["total_pnl_usdt"] += pnl_usdt

        if pnl_usdt > 0:
            self.stats["winners"] += 1
        else:
            self.stats["losers"] += 1

        # Update best/worst trade tracking
        if self.stats["best_trade"] is None or pnl_usdt > self.stats["best_trade"].get(
            "pnl", float("-inf")
        ):
            self.stats["best_trade"] = {"symbol": tracked.symbol, "pnl": pnl_usdt}
        if self.stats["worst_trade"] is None or pnl_usdt < self.stats["worst_trade"].get(
            "pnl", float("inf")
        ):
            self.stats["worst_trade"] = {"symbol": tracked.symbol, "pnl": pnl_usdt}

        # Update database with exchange data
        if self.database:
            try:
                self.database.update_signal_outcome(
                    signal_id=tracked.signal_id,
                    exit_price=final_exit_price,
                    exit_reason=exit_reason,
                    pnl=roi_percent,  # Store real ROI
                    pnl_percent=roi_percent,
                    fees=tracked.total_fees,  # Real fees from exchange
                    max_profit_percent=tracked.max_profit_percent
                    if tracked.max_profit_percent > 0
                    else None,
                    open_fee=tracked.open_fee,
                    close_fee=tracked.close_fee,
                    funding_fee=tracked.funding_fee,
                    exchange_pnl=tracked.exchange_pnl,
                    net_pnl=tracked.net_pnl,
                )
            except Exception as e:
                logger.error(f"Error updating signal outcome in DB: {e}")

        # Remove from Break-Even manager
        if self.break_even_manager:
            self.break_even_manager.remove_position(tracked.signal_id)

        # Build breakdown of partial closes for logging
        parts_breakdown = []
        for pc in tracked.partial_closes:
            tp_level = pc.get("tp_level", "?")
            pc_pnl_percent = pc.get("pnl_percent", 0)
            pc_pnl_usdt = pc.get("pnl_usdt", 0)
            close_pct = pc.get("close_percent", 0)
            if tp_level == "REMAINDER":
                parts_breakdown.append(
                    f"Remainder({close_pct:.0f}%): {pc_pnl_percent:+.2f}% ${pc_pnl_usdt:+.2f}"
                )
            else:
                parts_breakdown.append(
                    f"TP{tp_level}({close_pct:.0f}%): {pc_pnl_percent:+.2f}% ${pc_pnl_usdt:+.2f}"
                )

        # Log result with breakdown
        result_emoji = "✅" if pnl_usdt > 0 else "❌"
        logger.info(
            f"{result_emoji} SIGNAL CLOSED: {tracked.side} {tracked.symbol} | "
            f"Entry: {tracked.executed_price:.4f} | Position: ${tracked.position_size_usdt:.2f}",
            extra={"log_type": "signal"},
        )
        logger.info(f"   📊 Breakdown: {' | '.join(parts_breakdown)}", extra={"log_type": "signal"})
        logger.info(
            f"   💰 Total: ${pnl_usdt:+.2f} | ROI: {roi_percent:+.2f}% | "
            f"Reason: {exit_reason} | Balance: ${self.stats['current_balance']:.2f}",
            extra={"log_type": "signal"},
        )

        # Notify via Telegram (if callback set)
        if self.on_signal_closed:
            try:
                # Get final partial close info (if any remainder was closed)
                final_partial = tracked.partial_closes[-1] if tracked.partial_closes else None
                await self.on_signal_closed(
                    tracked.signal_id, exit_reason, pnl_usdt, roi_percent, final_partial
                )
            except Exception as e:
                logger.error(f"Error in on_signal_closed callback: {e}")

    async def _save_signal_to_db(
        self,
        signal_id: str,
        signal: Any,
        status: str,
        metadata: Optional[Dict] = None,
        executed_price: Optional[float] = None,
    ) -> None:
        """
        Save signal to database with WAL fallback.

        Signal is first written to WAL for durability, then synced to DB.
        If DB is unavailable, signal is persisted in WAL and retried later.
        """
        try:
            # Get strength as string (handle enum or string)
            strength = getattr(signal, "strength", "MODERATE")
            if hasattr(strength, "value"):
                strength = strength.value  # Convert enum to string

            # Extract entry reasons from metadata
            entry_reasons = signal.metadata.get("reasons", [])
            if not entry_reasons:
                entry_reasons = signal.metadata.get("entry_reasons", [])

            # Prepare signal data (use ISO format for timestamp for JSON serialization)
            signal_data = {
                "signal_id": signal_id,
                "symbol": signal.symbol,
                "timestamp": utcnow().isoformat(),
                "signal_type": signal.side,
                "strength": strength,
                "confidence": signal.confidence,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit
                if isinstance(signal.take_profit, list)
                else [signal.take_profit],
                "risk_reward_ratio": getattr(signal, "risk_reward_ratio", None),
                "position_size_percent": getattr(signal, "position_size_percent", None),
                "strategy_name": signal.strategy_name,
                "timeframe": getattr(signal, "timeframe", "1m"),
                "indicators_snapshot": signal.metadata.get("indicators", {}),
                "metadata": metadata or signal.metadata,
                "entry_reasons": entry_reasons,  # Why signal was generated
                "status": status,
                "exchange": getattr(signal, "exchange", "bybit"),
                "source": "signal_lifecycle_manager",
            }

            # Write to WAL first (ensures durability even if DB fails)
            wal_entry_id = await self.wal.write(WALEntryType.SIGNAL_CREATE, signal_data)

            # Try direct DB save
            db_success = False
            if self.database:
                try:
                    # Convert timestamp back to datetime for DB
                    db_data = signal_data.copy()
                    db_data["timestamp"] = datetime.fromisoformat(db_data["timestamp"])
                    db_success = self.database.save_signal(db_data)
                except Exception as db_error:
                    logger.warning(f"DB save failed (WAL will retry): {db_error}")

            # Mark WAL entry as synced if DB save succeeded
            if db_success:
                await self.wal.mark_synced(wal_entry_id)
            else:
                logger.debug(f"Signal {signal_id} written to WAL, pending DB sync")

            # If executed, update execution details
            if status == "EXECUTED" and executed_price:
                # Get real order_id and size from tracked signal if available
                real_order_id = None
                executed_size = 0.0
                if signal_id in self.tracked_signals:
                    tracked = self.tracked_signals[signal_id]
                    real_order_id = tracked.exchange_order_id
                    # Calculate size from position_size_usdt / executed_price (account for contract_size)
                    if executed_price > 0 and tracked.position_size_usdt > 0:
                        from src.core.trading_pairs import trading_pairs as _tp

                        executed_size = tracked.position_size_usdt / (
                            _tp.usd_price(tracked.symbol, executed_price)
                            * _tp.get_contract_size(tracked.symbol)
                        )

                # Write execution update to WAL
                exec_data = {
                    "signal_id": signal_id,
                    "executed_price": executed_price,
                    "executed_size": executed_size,
                    "order_id": real_order_id or f"PAPER_{signal_id}",
                }
                exec_wal_id = await self.wal.write(WALEntryType.SIGNAL_EXECUTE, exec_data)

                # Try direct DB update
                if self.database:
                    try:
                        if self.database.update_signal_execution(**exec_data):
                            await self.wal.mark_synced(exec_wal_id)
                    except Exception as db_error:
                        logger.warning(f"DB execution update failed (WAL will retry): {db_error}")

        except Exception as e:
            logger.error(f"Error saving signal to DB/WAL: {e}")

    async def _save_order_to_db(
        self,
        signal_id: str,
        order_type: str,  # ENTRY, STOP_LOSS, TAKE_PROFIT_1, TAKE_PROFIT_2, etc.
        symbol: str,
        side: str,
        order_category: str = None,  # MARKET, LIMIT, STOP_MARKET
        price: float = None,
        trigger_price: float = None,
        quantity: float = None,
        exchange_order_id: str = None,
        status: str = "PENDING",
        position_idx: int = None,
        reduce_only: bool = False,
        metadata: dict = None,
    ) -> None:
        """Save order to database with WAL fallback."""
        try:
            order_data = {
                "signal_id": signal_id,
                "order_type": order_type,
                "symbol": symbol,
                "side": side,
                "order_category": order_category,
                "price": price,
                "trigger_price": trigger_price,
                "quantity": quantity,
                "exchange_order_id": exchange_order_id,
                "status": status,
                "position_idx": position_idx,
                "reduce_only": reduce_only,
                "metadata": metadata,
            }

            # Write to WAL first
            wal_entry_id = await self.wal.write(WALEntryType.ORDER_CREATE, order_data)

            # Try direct DB save
            if self.database:
                try:
                    if self.database.save_order(order_data):
                        await self.wal.mark_synced(wal_entry_id)
                except Exception as db_error:
                    logger.warning(f"DB order save failed (WAL will retry): {db_error}")

        except Exception as e:
            logger.error(f"Error saving order to DB/WAL: {e}")

    async def _update_order_status_in_db(
        self,
        signal_id: str,
        order_type: str,
        status: str,
        exchange_order_id: str = None,
        filled_price: float = None,
        filled_quantity: float = None,
        commission: float = None,
        status_reason: str = None,
    ) -> None:
        """Update order status in database with WAL fallback."""
        try:
            order_data = {
                "signal_id": signal_id,
                "order_type": order_type,
                "status": status,
                "exchange_order_id": exchange_order_id,
                "filled_price": filled_price,
                "filled_quantity": filled_quantity,
                "commission": commission,
                "status_reason": status_reason,
            }

            # Write to WAL first
            wal_entry_id = await self.wal.write(WALEntryType.ORDER_UPDATE, order_data)

            # Try direct DB update
            if self.database:
                try:
                    if self.database.update_order_status(**order_data):
                        await self.wal.mark_synced(wal_entry_id)
                    elif not self.database.order_exists(signal_id, order_type):
                        # Nothing to update (see sync_order_update) — close the
                        # record right away, otherwise it burns 10 retries for nothing.
                        await self.wal.mark_synced(wal_entry_id)
                except Exception as db_error:
                    logger.warning(f"DB order update failed (WAL will retry): {db_error}")

        except Exception as e:
            logger.error(f"Error updating order status in DB/WAL: {e}")

    def _cleanup_old_signals(self) -> None:
        """Remove old closed signals from tracking."""
        if len(self.tracked_signals) <= self.config.max_tracked_signals:
            return

        # Sort by creation time and remove oldest closed signals
        closed_signals = [
            (sig_id, sig) for sig_id, sig in self.tracked_signals.items() if sig.status == "CLOSED"
        ]
        closed_signals.sort(key=lambda x: x[1].created_at)

        # Remove oldest
        to_remove = len(self.tracked_signals) - self.config.max_tracked_signals
        for sig_id, _ in closed_signals[:to_remove]:
            del self.tracked_signals[sig_id]

    def get_stats(self) -> Dict[str, Any]:
        """Get lifecycle statistics."""
        win_rate = 0.0
        if self.stats["signals_closed"] > 0:
            win_rate = self.stats["winners"] / self.stats["signals_closed"]

        avg_pnl = 0.0
        if self.stats["signals_closed"] > 0:
            avg_pnl = self.stats["total_pnl"] / self.stats["signals_closed"]

        return {
            **self.stats,
            "win_rate": win_rate,
            "avg_pnl_percent": avg_pnl,
            "active_signals": len(
                [s for s in self.tracked_signals.values() if s.status == "EXECUTED"]
            ),
            "total_tracked": len(self.tracked_signals),
            "strategy_balances": self.strategy_balances,
        }

    def get_strategy_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Get per-strategy statistics.

        Returns:
            Dict with strategy name as key and stats dict as value
        """
        result = {}
        for strategy_name, stats in self.strategy_balances.items():
            win_rate = 0.0
            if stats["trades"] > 0:
                win_rate = stats["winners"] / stats["trades"]

            pnl_percent = 0.0
            if stats["initial"] > 0:
                pnl_percent = (stats["pnl"] / stats["initial"]) * 100

            result[strategy_name] = {
                **stats,
                "win_rate": win_rate,
                "pnl_percent": pnl_percent,
                "roi": pnl_percent,
            }
        return result

    def log_strategy_performance(self) -> None:
        """Log performance summary for each strategy."""
        if not self.strategy_balances:
            return

        logger.info("=" * 60)
        logger.info("📊 PER-STRATEGY PERFORMANCE")
        logger.info("=" * 60)

        for strategy_name, stats in self.strategy_balances.items():
            win_rate = (stats["winners"] / stats["trades"] * 100) if stats["trades"] > 0 else 0
            pnl_percent = (stats["pnl"] / stats["initial"] * 100) if stats["initial"] > 0 else 0

            logger.info(
                f"  {strategy_name}: ${stats['current']:.2f} "
                f"(PnL: ${stats['pnl']:+.2f} / {pnl_percent:+.1f}%) | "
                f"Trades: {stats['trades']} | Win Rate: {win_rate:.0f}%"
            )

        logger.info("=" * 60)

    def get_open_positions(self) -> List[TrackedSignal]:
        """Get all currently open (executed) signals."""
        return [s for s in self.tracked_signals.values() if s.status == "EXECUTED"]

    async def close_all_positions(self, reason: str = "EMERGENCY_STOP") -> Dict[str, Any]:
        """Force-close all open positions through the broker.

        Added because the emergency stop did NOT close positions: at this point
        TradingController.emergency_stop had a stub that added a "Requested
        position closure" line to the report — and Telegram reported "EMERGENCY
        STOP EXECUTED". In a real emergency that is the worst possible outcome:
        confirmation received, positions still open.

        close_position_by_id cannot be used: that path only does bookkeeping and
        sends no order to the broker — positions would be marked closed inside the
        bot while remaining open on the account. The real close goes through
        _close_remaining_position: remove SL/TP + market exit by ticket.

        Each position is closed independently: a failure on one must not block
        the others. Each result is verified by the actual status, not by the
        absence of an exception.
        """
        open_ids = [sid for sid, s in self.tracked_signals.items() if s.status == "EXECUTED"]
        if not open_ids:
            return {"requested": 0, "closed": [], "failed": [], "reason": reason}

        logger.critical(f"🚨 Force-closing {len(open_ids)} positions: {reason}")
        closed: List[str] = []
        failed: List[Dict[str, str]] = []

        for sid in open_ids:
            tracked = self.tracked_signals.get(sid)
            if tracked is None or tracked.status == "CLOSED":
                continue
            price = (
                self.current_prices.get(tracked.symbol)
                or tracked.executed_price
                or tracked.entry_price
                or 0.0
            )
            try:
                await self._close_remaining_position(sid, float(price), reason)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Closing {sid} failed: {exc}", exc_info=True)
                failed.append({"signal_id": sid, "symbol": tracked.symbol, "error": str(exc)})
                continue

            after = self.tracked_signals.get(sid)
            if after is not None and after.status == "CLOSED":
                closed.append(f"{tracked.symbol} {tracked.side}")
            else:
                failed.append(
                    {
                        "signal_id": sid,
                        "symbol": tracked.symbol,
                        "error": "position remained open",
                    }
                )

        if failed:
            logger.critical(
                f"🚨 NOT CLOSED {len(failed)} of {len(open_ids)}: "
                + ", ".join(f"{f['symbol']} ({f['error']})" for f in failed)
            )
        return {"requested": len(open_ids), "closed": closed, "failed": failed, "reason": reason}

    def get_closed_positions(self, limit: int = 50) -> List[TrackedSignal]:
        """Get recently closed signals."""
        closed = [s for s in self.tracked_signals.values() if s.status == "CLOSED"]
        closed.sort(key=lambda x: x.closed_at or datetime.min, reverse=True)
        return closed[:limit]

    # ==================== Break-Even Callbacks ====================

    def _on_break_even_activated(self, status: BreakEvenStatus) -> None:
        """Callback when break-even stop is activated."""
        self.stats["break_even_activated"] += 1
        logger.info(
            f"🛡️ BE ACTIVATED: {status.side} {status.symbol} | "
            f"Entry: {status.entry_price:.4f} | BE Stop: {status.stop_price:.4f}",
            extra={"log_type": "signal"},
        )

        # Update actual stop loss on exchange
        if self.connector and status.stop_price:
            asyncio.create_task(self._sync_be_stop_to_exchange(status))

    def _on_break_even_triggered(self, status: BreakEvenStatus) -> None:
        """Callback when break-even stop is triggered."""
        self.stats["break_even_triggered"] += 1
        logger.info(
            f"🔔 BE TRIGGERED: {status.side} {status.symbol} | "
            f"Max profit was: {status.max_profit_percent:.2f}%",
            extra={"log_type": "signal"},
        )

    def _on_reentry_created(self, reentry: ReentryOrder) -> None:
        """Callback when re-entry order is created."""
        self.stats["reentries_created"] += 1
        logger.info(
            f"{S.NEW} RE-ENTRY {reentry.side} {reentry.symbol} @ {reentry.price:.4f} "
            f"exp={reentry.expires_at.strftime('%H:%M')} #{reentry.reentry_number}",
            extra={"log_type": "signal"},
        )

    async def _sync_be_stop_to_exchange(self, status: BreakEvenStatus) -> None:
        """
        Sync break-even stop to exchange by updating the position's stop loss.

        This is the CRITICAL method that actually updates the SL order on Bybit
        when break-even is activated.
        """
        try:
            from src.core.trading_pairs import trading_pairs

            # Get tracked signal
            tracked = self.tracked_signals.get(status.position_id)
            if not tracked:
                logger.warning(f"Cannot sync BE: signal {status.position_id} not found")
                return

            # Determine position_idx for hedge mode
            # LONG position = 1, SHORT position = 2
            position_idx = 1 if status.side == "LONG" else 2

            # Round price to correct precision
            pair_info = trading_pairs.get(status.symbol)
            if pair_info:
                be_stop_price = pair_info.round_price(status.stop_price)
            else:
                # Fallback: round to 4 decimals
                be_stop_price = round(status.stop_price, 4)

            # Use _modify_sl_order for consistent limit order behavior (with trigger buffer)
            # This replaces the old set_trading_stop which was market order
            tracked.stop_loss = be_stop_price  # Update tracked SL

            result = await self._modify_sl_order(tracked, be_stop_price)

            if result:
                # Mark BE as synced in tracked signal
                tracked.be_sl_synced = True
                logger.info(
                    f"✅ BE SYNCED to exchange: {status.symbol} {status.side} | "
                    f"SL updated to {be_stop_price:.6f} (limit order)",
                    extra={"log_type": "trade"},
                )
                return

            # Fallback to set_trading_stop (market) if limit order fails
            max_retries = 3
            fallback_result = None

            for attempt in range(max_retries):
                be_fallback_kwargs = {
                    "symbol": status.symbol,
                    "position_idx": position_idx,
                    "stop_loss": be_stop_price,
                }
                if tracked.position_ticket:
                    be_fallback_kwargs["position_ticket"] = tracked.position_ticket
                fallback_result = await self.connector.set_trading_stop(**be_fallback_kwargs)

                if fallback_result and fallback_result.get("success"):
                    # Mark BE as synced in tracked signal
                    tracked.be_sl_synced = True
                    logger.info(
                        f"✅ BE SYNCED to exchange (fallback): {status.symbol} {status.side} | "
                        f"SL updated to {be_stop_price:.6f} (market order)",
                        extra={"log_type": "trade"},
                    )
                    return

                # Check if error is retryable
                error = (
                    fallback_result.get("error", "Unknown error")
                    if fallback_result
                    else "No response"
                )
                error_code = fallback_result.get("error_code") if fallback_result else "NO_RESPONSE"

                # Don't retry on certain errors
                if error_code not in ("NO_RESPONSE", 10002, 10006):  # Rate limit, network issues
                    break

                if attempt < max_retries - 1:
                    delay = 1.0 * (attempt + 1)
                    logger.warning(
                        f"⚠️ BE SYNC retry {attempt + 1}/{max_retries}: {status.symbol} | "
                        f"Error: {error} | Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)

            # All retries failed - reset flag so monitoring loop retries
            tracked.be_sl_synced = False
            error = (
                fallback_result.get("error", "Unknown error") if fallback_result else "No response"
            )
            logger.error(
                f"❌ BE SYNC FAILED: {status.symbol} | Error: {error}", extra={"log_type": "error"}
            )

        except Exception as e:
            logger.error(f"Error syncing BE stop to exchange: {e}", exc_info=True)
            # Reset flag so monitoring loop retries
            tracked = self.tracked_signals.get(status.position_id)
            if tracked:
                tracked.be_sl_synced = False

    # ==================== Limit Order Callbacks ====================

    async def _on_limit_order_filled(self, pending: PendingLimitOrder) -> None:
        """Callback when limit entry order is filled."""
        signal_id = pending.signal_id
        tracked = self.tracked_signals.get(signal_id)

        if not tracked:
            # This can happen when market fallback is used - the signal is processed
            # synchronously in _execute_with_limit_order, not through this callback.
            # This is expected behavior, not an error.
            logger.debug(f"Limit order callback: signal {signal_id} already processed or not found")
            return

        # Update tracked signal with fill info
        tracked.executed_price = pending.average_fill_price
        tracked.executed_at = utcnow()
        tracked.exchange_order_id = pending.exchange_order_id or pending.market_order_id
        tracked.position_ticket = getattr(pending, "position_ticket", None)  # MT5 ticket
        tracked.status = "EXECUTED"
        self.stats["signals_executed"] += 1

        # Resolve MT5 position ticket if not returned
        if (
            not tracked.position_ticket
            and self.connector
            and hasattr(self.connector, "get_positions")
        ):
            try:
                positions = await self.connector.get_positions(tracked.symbol)
                if positions:
                    used_tickets = {
                        t.position_ticket
                        for t in self.tracked_signals.values()
                        if t.position_ticket
                        and t.status == "EXECUTED"
                        and t.symbol == tracked.symbol
                    }
                    candidates = [
                        p
                        for p in positions
                        if p.get("side", "").upper() == tracked.side
                        and p.get("ticket") not in used_tickets
                    ]
                    if candidates:
                        best = max(candidates, key=lambda p: p.get("ticket", 0))
                        tracked.position_ticket = best.get("ticket")
                        logger.info(f"Resolved position ticket: {tracked.position_ticket}")
            except Exception as e:
                logger.debug(f"Could not resolve position ticket: {e}")

        fill_time = pending.filled_at - pending.created_at if pending.filled_at else 0
        order_type = "MARKET" if pending.market_fallback_used else "LIMIT"

        logger.info(
            f"✅ {order_type} FILLED: {tracked.side} {tracked.symbol} | "
            f"Price: {tracked.executed_price:.4f} | Fill time: {fill_time:.1f}s"
            + (f" | Ticket: {tracked.position_ticket}" if tracked.position_ticket else "")
        )

        # Update entry order status in DB to FILLED
        await self._update_order_status_in_db(
            signal_id=signal_id,
            order_type="ENTRY",
            status="FILLED",
            exchange_order_id=tracked.exchange_order_id,
            filled_price=tracked.executed_price,
            status_reason=f"{order_type} order filled",
        )

        # Recalculate SL/TP from actual fill price
        self._recalculate_sl_tp_from_fill(tracked)

        # Place SL and TP orders
        signal_data = pending.signal_data
        quantity = signal_data.get("quantity", 0)
        if quantity > 0:
            await self._place_sl_tp_orders(tracked, quantity)

        # Notify Telegram if callback is set
        if self.on_signal_opened:
            try:
                await self.on_signal_opened(tracked)
            except Exception as e:
                logger.error(f"Error in on_signal_opened callback: {e}")

    async def _on_limit_order_timeout(self, pending: PendingLimitOrder) -> None:
        """Callback when limit entry order times out."""
        signal_id = pending.signal_id
        tracked = self.tracked_signals.get(signal_id)

        if not tracked:
            logger.warning(f"Limit order timeout but signal {signal_id} not found")
            return

        signal_meta = (pending.signal_data or {}).get("metadata", {}) or {}
        no_fallback = bool(signal_meta.get("cancel_on_limit_timeout"))

        logger.warning(
            f"⏰ LIMIT ORDER TIMEOUT: {pending.side} {pending.symbol} | "
            f"Waited: {pending.timeout_at - pending.created_at:.0f}s | "
            f"Filled: {pending.fill_percent:.1f}% | "
            f"{'CANCELLED (no fallback)' if no_fallback else 'will market-fallback'}"
        )

        await self._update_order_status_in_db(
            signal_id=signal_id,
            order_type="ENTRY",
            status="CANCELLED" if no_fallback else "EXPIRED",
            status_reason=(
                f"Limit cancelled per-signal flag, {pending.fill_percent:.1f}% filled"
                if no_fallback
                else f"Limit order timeout, {pending.fill_percent:.1f}% filled"
            ),
        )

        if no_fallback:
            # No market fallback — mark tracked signal terminated, drop it so
            # the same daily slot isn't held indefinitely in PENDING_ENTRY.
            tracked.status = "CANCELLED"
            tracked.closed_at = datetime.now()
            tracked.exit_reason = "LIMIT_TIMEOUT_NO_FALLBACK"

    async def _check_reentries(self) -> None:
        """Check if any pending re-entry orders should be filled (simulation)."""
        if not self.break_even_manager:
            return

        pending_reentries = self.break_even_manager.get_pending_reentries()

        for reentry in pending_reentries:
            current_price = self.current_prices.get(reentry.symbol)
            if current_price is None:
                continue

            # Check if limit order would be filled
            should_fill = False

            if reentry.side == "BUY":
                # Buy limit fills when price drops to or below limit price
                if current_price <= reentry.price:
                    should_fill = True
            else:  # SELL
                # Sell limit fills when price rises to or above limit price
                if current_price >= reentry.price:
                    should_fill = True

            if should_fill:
                await self._execute_reentry(reentry, current_price)

    async def _execute_reentry(self, reentry: ReentryOrder, fill_price: float) -> None:
        """Execute a re-entry order."""
        # Register new position in BE manager
        new_be_status = self.break_even_manager.handle_reentry_filled(
            reentry.position_id, fill_price
        )

        if not new_be_status:
            return

        self.stats["reentries_filled"] += 1

        # Create new tracked signal for the re-entry
        side = "BUY" if reentry.side == "BUY" else "SELL"
        new_signal_id = reentry.position_id

        tracked = TrackedSignal(
            signal=None,  # No original signal object for re-entry
            signal_id=new_signal_id,
            symbol=reentry.symbol,
            side=side,
            entry_price=fill_price,
            stop_loss=reentry.stop_loss,
            take_profit=reentry.take_profit,
            created_at=utcnow(),
            status="EXECUTED",
            executed_at=utcnow(),
            executed_price=fill_price,
        )

        self.tracked_signals[new_signal_id] = tracked
        self.stats["signals_executed"] += 1

        logger.info(
            f"✅ RE-ENTRY FILLED: {side} {reentry.symbol} @ {fill_price:.4f} | "
            f"SL: {reentry.stop_loss:.4f} | TP: {reentry.take_profit[0]:.4f} | "
            f"Re-entry #{reentry.reentry_number}",
            extra={"log_type": "trade"},
        )

    def get_break_even_stats(self) -> Dict[str, Any]:
        """Get break-even specific statistics."""
        if not self.break_even_manager:
            return {"enabled": False}

        active_be = self.break_even_manager.get_all_active()
        pending_reentries = self.break_even_manager.get_pending_reentries()

        return {
            "enabled": True,
            "positions_with_be": len(active_be),
            "pending_reentries": len(pending_reentries),
            "be_activated": self.stats["break_even_activated"],
            "be_triggered": self.stats["break_even_triggered"],
            "reentries_created": self.stats["reentries_created"],
            "reentries_filled": self.stats["reentries_filled"],
        }

    async def _sync_positions_with_exchange(self) -> None:
        """
        Synchronize tracked signals with actual exchange positions.

        Detects when positions are closed externally (SL/TP fills, manual close)
        and updates bot state accordingly.

        Supports multi-position strategies (e.g., grid fan with multiple positions
        on the same symbol+side) by using ticket-based lookup as primary method
        and (symbol, side) with volume matching as fallback.
        """
        if not self.connector:
            return

        try:
            # Get all open positions from exchange
            exchange_positions = await self.connector.get_positions()

            # Build two maps for position lookup:
            # 1. By ticket (primary, exact match for MT5 multi-position)
            # 2. By (symbol, side) -> list of positions (fallback)
            exchange_by_ticket: Dict[int, Dict] = {}
            exchange_by_symbol_side: Dict[tuple, list] = {}

            for pos in exchange_positions:
                if pos.get("size", 0) > 0:
                    symbol = pos["symbol"]
                    side = pos["side"].upper()
                    if side in ("BUY", "LONG"):
                        side = "BUY"
                    else:
                        side = "SELL"

                    # Index by ticket if available
                    ticket = pos.get("ticket")
                    if ticket:
                        exchange_by_ticket[int(ticket)] = pos

                    # Index by (symbol, side) as list
                    key = (symbol, side)
                    if key not in exchange_by_symbol_side:
                        exchange_by_symbol_side[key] = []
                    exchange_by_symbol_side[key].append(pos)

            # Track which exchange positions are matched (by ticket)
            matched_tickets: set = set()

            # Check each tracked signal
            signals_closed_externally = []

            for signal_id, tracked in list(self.tracked_signals.items()):
                if tracked.status != "EXECUTED":
                    continue

                position_found = False
                matched_pos = None

                # Method 1: Lookup by position ticket (primary for MT5)
                if tracked.position_ticket:
                    ticket = int(tracked.position_ticket)
                    if ticket in exchange_by_ticket:
                        matched_pos = exchange_by_ticket[ticket]
                        matched_tickets.add(ticket)
                        position_found = True

                # Method 2: Fallback — match by (symbol, side) + volume
                if not position_found:
                    key = (tracked.symbol, tracked.side)
                    candidates = exchange_by_symbol_side.get(key, [])

                    # Filter out already-matched positions
                    unmatched = [p for p in candidates if p.get("ticket") not in matched_tickets]

                    if unmatched:
                        if len(unmatched) == 1:
                            # Only one candidate — use it
                            matched_pos = unmatched[0]
                        else:
                            # Multiple candidates — try to match by volume
                            if tracked.executed_price and tracked.executed_price > 0:
                                from src.core.trading_pairs import trading_pairs as _tps

                                expected_size = tracked.position_size_usdt / (
                                    _tps.usd_price(tracked.symbol, tracked.executed_price)
                                    * _tps.get_contract_size(tracked.symbol)
                                )
                                expected_size *= tracked.remaining_percent / 100.0

                                best_match = None
                                best_diff = float("inf")
                                for p in unmatched:
                                    p_size = float(p.get("size", 0))
                                    diff = abs(p_size - expected_size)
                                    if diff < best_diff:
                                        best_diff = diff
                                        best_match = p

                                if best_match:
                                    matched_pos = best_match
                            else:
                                # Can't compute expected size, take first unmatched
                                matched_pos = unmatched[0]

                        if matched_pos:
                            t = matched_pos.get("ticket")
                            if t:
                                matched_tickets.add(int(t))
                            position_found = True

                if not position_found:
                    # Position closed on exchange but still tracked by bot
                    signals_closed_externally.append((signal_id, tracked))
                elif matched_pos:
                    # Position exists — check for dust (mostly closed)
                    exchange_size = float(matched_pos.get("size", 0))

                    if tracked.executed_price and tracked.executed_price > 0:
                        from src.core.trading_pairs import trading_pairs as _tps

                        expected_size = tracked.position_size_usdt / (
                            _tps.usd_price(tracked.symbol, tracked.executed_price)
                            * _tps.get_contract_size(tracked.symbol)
                        )
                        expected_size *= tracked.remaining_percent / 100.0

                        if expected_size > 0 and exchange_size < expected_size * 0.10:
                            dust_value = exchange_size * tracked.executed_price
                            logger.warning(
                                f"⚠️ DUST DETECTED: {tracked.symbol} has {exchange_size:.6f} "
                                f"(${dust_value:.2f}) remaining vs expected "
                                f"{expected_size:.6f}. Treating as closed."
                            )
                            signals_closed_externally.append((signal_id, tracked))

            # Handle externally closed positions (with locks)
            for signal_id, tracked in signals_closed_externally:
                symbol_lock = self._get_symbol_lock(tracked.symbol)
                async with symbol_lock:
                    # Re-check status after acquiring lock
                    if tracked.status != "CLOSED":
                        await self._handle_external_close(signal_id, tracked)

        except Exception as e:
            logger.warning(f"⚠️ Position sync error (may miss closes): {e}")

    async def _handle_external_close(self, signal_id: str, tracked: "TrackedSignal") -> None:
        """
        Handle a position that was closed externally (on exchange but not by bot).

        This happens when:
        - SL order fills on exchange
        - TP order fills on exchange
        - User manually closes position
        """
        try:
            exit_price = self.current_prices.get(tracked.symbol)
            realized_pnl = 0.0
            exit_reason = "EXTERNAL_CLOSE"
            exchange_pnl_used = False

            # Check if this is an orphan position (recovered from previous session)
            is_orphan = signal_id.startswith("ORPHAN_")

            # Try to get actual closed PnL from exchange first
            if hasattr(self.connector, "get_closed_pnl"):
                try:
                    import time

                    # Get recent closed PnL (last 5 minutes, or longer for orphans)
                    end_time = int(time.time() * 1000)
                    # For orphans, search last 10 minutes to be safe
                    search_window = 10 * 60 * 1000 if is_orphan else 5 * 60 * 1000
                    start_time = end_time - search_window

                    # MT5: use position_ticket for exact match
                    pnl_kwargs = {
                        "symbol": tracked.symbol,
                        "start_time": start_time,
                        "limit": 10,
                    }
                    if tracked.position_ticket:
                        pnl_kwargs["position_ticket"] = tracked.position_ticket

                    closed_pnl_records = await self.connector.get_closed_pnl(**pnl_kwargs)

                    # Find matching record
                    for record in closed_pnl_records:
                        # MT5: if searched by position_ticket, the match is exact
                        if (
                            tracked.position_ticket
                            and record.get("ticket") == tracked.position_ticket
                        ):
                            realized_pnl = record["closed_pnl"]
                            exit_price = record["exit_price"]
                            exchange_pnl_used = True
                            logger.info(
                                f"📊 Using exchange PnL for {tracked.symbol} "
                                f"ticket={tracked.position_ticket}: "
                                f"exit@{exit_price:.4f}, PnL=${realized_pnl:.2f}"
                            )
                            break

                        # For orphan positions: use looser matching (5% tolerance) or just take most recent
                        # For regular positions: strict 1% tolerance
                        if is_orphan:
                            # For orphans, prefer matching by entry price but with larger tolerance
                            entry_diff = abs(record["entry_price"] - (tracked.executed_price or 0))
                            tolerance = 0.05  # 5% tolerance for orphans
                            if tracked.executed_price and tracked.executed_price > 0:
                                if entry_diff / tracked.executed_price < tolerance:
                                    realized_pnl = record["closed_pnl"]
                                    exit_price = record["exit_price"]
                                    exchange_pnl_used = True
                                    logger.info(
                                        f"📊 Using exchange PnL for orphan {tracked.symbol}: {realized_pnl:.2f} USDT"
                                    )
                                    break
                            else:
                                # No entry price to compare - just use the most recent record
                                realized_pnl = record["closed_pnl"]
                                exit_price = record["exit_price"]
                                exchange_pnl_used = True
                                logger.info(
                                    f"📊 Using exchange PnL for orphan {tracked.symbol} (no entry): {realized_pnl:.2f} USDT"
                                )
                                break
                        else:
                            # Regular position - strict matching
                            entry_diff = abs(record["entry_price"] - (tracked.executed_price or 0))
                            if (
                                tracked.executed_price
                                and entry_diff / tracked.executed_price < 0.01
                            ):  # Within 1%
                                realized_pnl = record["closed_pnl"]
                                exit_price = record["exit_price"]
                                exchange_pnl_used = True
                                logger.info(
                                    f"📊 Using exchange PnL for {tracked.symbol}: {realized_pnl:.2f} USDT"
                                )
                                break

                    # For orphans, if no match found, take the most recent record anyway
                    if is_orphan and not exchange_pnl_used and closed_pnl_records:
                        record = closed_pnl_records[0]  # Most recent
                        realized_pnl = record["closed_pnl"]
                        exit_price = record["exit_price"]
                        exchange_pnl_used = True
                        logger.info(
                            f"📊 Using most recent exchange PnL for orphan {tracked.symbol}: {realized_pnl:.2f} USDT"
                        )

                except Exception as e:
                    logger.debug(f"Could not get closed PnL from exchange: {e}")

            # Try to determine if it was SL or TP that filled
            # trading-stop SL has no order ID to check - detect via position SL field
            if tracked.sl_order_id and tracked.sl_order_id != "TRADING_STOP":
                try:
                    sl_status = await self.connector.get_order_status(
                        tracked.sl_order_id, tracked.symbol
                    )
                    if sl_status.get("status") in ("Filled", "FILLED"):
                        exit_reason = "STOP_LOSS"
                        if not exchange_pnl_used:
                            exit_price = float(
                                sl_status.get("avg_price")
                                or sl_status.get("price")
                                or exit_price
                                or 0
                            )
                except Exception as e:
                    logger.debug(f"Could not check SL order {tracked.sl_order_id} status: {e}")

            # Check TP orders
            if exit_reason == "EXTERNAL_CLOSE" and tracked.tp_order_ids:
                for i, tp_id in enumerate(tracked.tp_order_ids):
                    if not tp_id or tp_id.startswith("TRADING_STOP"):
                        continue
                    try:
                        tp_status = await self.connector.get_order_status(tp_id, tracked.symbol)
                        if tp_status.get("status") in ("Filled", "FILLED"):
                            exit_reason = f"TAKE_PROFIT_{i+1}"
                            if not exchange_pnl_used:
                                exit_price = float(
                                    tp_status.get("avg_price")
                                    or tp_status.get("price")
                                    or exit_price
                                    or 0
                                )
                            break
                    except Exception as e:
                        logger.debug(f"Could not check TP order {tp_id} status: {e}")

            # Fallback: detect TP close by exit price matching a TP level
            # (for MT5 positions where TP was set via position modify)
            if exit_reason == "EXTERNAL_CLOSE" and tracked.take_profit and exit_price:
                for i, tp_price in enumerate(tracked.take_profit):
                    if tp_price and tp_price > 0:
                        price_diff_pct = abs(exit_price - tp_price) / tp_price * 100
                        if price_diff_pct < 0.02:
                            exit_reason = f"TAKE_PROFIT_{i+1}"
                            logger.info(
                                f"TP{i+1} detected by exit price: "
                                f"exit={exit_price:.4f} ≈ TP={tp_price:.4f}"
                            )
                            break

            # Calculate PnL if not from exchange
            if not exchange_pnl_used and exit_price and tracked.executed_price:
                if tracked.side == "BUY":
                    raw_pnl_percent = (
                        (exit_price - tracked.executed_price) / tracked.executed_price
                    ) * 100
                else:
                    raw_pnl_percent = (
                        (tracked.executed_price - exit_price) / tracked.executed_price
                    ) * 100

                # Calculate realized PnL in USDT (position_size_usdt is notional, no leverage needed)
                realized_pnl = tracked.position_size_usdt * (raw_pnl_percent / 100)

                # For display: show leveraged percentage (how it looks on margin)
                pnl_percent = raw_pnl_percent * self.config.leverage
            elif exchange_pnl_used:
                # Calculate percent from exchange PnL
                if tracked.position_size_usdt > 0:
                    pnl_percent = (
                        (realized_pnl / tracked.position_size_usdt) * 100 * self.config.leverage
                    )
                else:
                    pnl_percent = 0
            else:
                pnl_percent = 0
                exit_price = exit_price or tracked.executed_price or 0

            # Log the external close (fees will be fetched and logged by _close_signal)
            logger.info(
                f"{P.EXTERN} {tracked.side} {tracked.symbol} @ {exit_price:.4f} "
                f"{pnl_percent:+.1f}% ${realized_pnl:+.1f} | {exit_reason}",
                extra={"log_type": "trade"},
            )

            # Close the signal properly (send_market_order=False since position already closed)
            # Pass exchange_pnl for orphan positions to ensure correct balance tracking
            await self._close_signal(
                signal_id,
                exit_price,
                exit_reason,
                send_market_order=False,
                exchange_pnl=realized_pnl if exchange_pnl_used else None,
            )

            # Try to clean up any remaining dust on exchange
            await self._cleanup_dust(tracked)

        except Exception as e:
            logger.error(f"Error handling external close for {tracked.symbol}: {e}")
            # Still try to close the signal
            try:
                exit_price = self.current_prices.get(tracked.symbol) or tracked.executed_price or 0
                await self._close_signal(signal_id, exit_price, "EXTERNAL_CLOSE_ERROR")
            except Exception as e:
                logger.error(f"Failed to close signal {signal_id} after external close error: {e}")

    async def _cleanup_dust(self, tracked: "TrackedSignal") -> None:
        """
        Clean up any dust (tiny remaining position) on exchange after main position closed.

        This prevents dust from accumulating and causing issues with future trades.
        """
        if not self.connector:
            return

        try:
            # Get current position on exchange
            positions = await self.connector.get_positions()

            for pos in positions:
                if pos.get("symbol") != tracked.symbol:
                    continue

                pos_side = pos.get("side", "").upper()
                if pos_side in ("BUY", "LONG"):
                    pos_side = "BUY"
                else:
                    pos_side = "SELL"

                if pos_side != tracked.side:
                    continue

                dust_size = float(pos.get("size", 0))
                if dust_size <= 0:
                    continue

                # Found dust - try to close it
                dust_value = dust_size * (tracked.executed_price or 1)
                if dust_value > 50:  # If > $50, it's not dust
                    logger.debug(
                        f"Position {tracked.symbol} has ${dust_value:.2f} remaining - not dust"
                    )
                    continue

                logger.info(
                    f"🧹 Cleaning up dust: {tracked.symbol} {dust_size:.6f} (${dust_value:.2f})"
                )

                # Close dust with market order
                close_side = "SELL" if tracked.side == "BUY" else "BUY"
                position_idx = 1 if tracked.side == "BUY" else 2

                try:
                    result = await self.connector.place_order(
                        {
                            "symbol": tracked.symbol,
                            "side": close_side,
                            "type": "MARKET",
                            "quantity": 0,  # Close entire position
                            "reduce_only": True,
                            "close_on_trigger": True,
                            "position_idx": position_idx,
                        }
                    )

                    if result.get("success") or result.get("order_id"):
                        logger.info(f"✅ Dust cleaned: {tracked.symbol}")
                    else:
                        logger.warning(
                            f"Could not clean dust for {tracked.symbol}: {result.get('error')}"
                        )

                except Exception as e:
                    logger.warning(f"Error cleaning dust for {tracked.symbol}: {e}")

        except Exception as e:
            logger.debug(f"Dust cleanup check failed for {tracked.symbol}: {e}")

    async def _sync_balance_after_orphan_close(self) -> None:
        """
        Sync bot balance with exchange after closing an orphan position.

        Orphan positions are recovered from previous sessions and their unrealized P&L
        was already included in the initial balance. So we don't add their P&L again,
        instead we sync with exchange to get accurate balance.
        """
        if not self.connector:
            return

        try:
            await asyncio.sleep(0.5)  # Small delay for exchange to process

            if hasattr(self.connector, "get_wallet_balance_detailed"):
                balance_info = await self.connector.get_wallet_balance_detailed("USDT")

                if balance_info:
                    exchange_balance = balance_info.get("wallet_balance", 0)
                    old_balance = self.stats["current_balance"]

                    if exchange_balance > 0:
                        self.stats["current_balance"] = exchange_balance
                        diff = exchange_balance - old_balance

                        logger.info(
                            f"📊 Orphan close balance sync: ${old_balance:.2f} → ${exchange_balance:.2f} "
                            f"(diff: {diff:+.2f})"
                        )
        except Exception as e:
            logger.debug(f"Error syncing balance after orphan close: {e}")

    async def _sync_balance_with_exchange(self) -> None:
        """
        Sync balance with exchange to track actual vs bot balance.

        Gets wallet balance (without uPnL) and logs difference with bot balance.
        """
        if not self.connector:
            return

        try:
            # Get detailed balance from exchange
            if hasattr(self.connector, "get_wallet_balance_detailed"):
                balance_info = await self.connector.get_wallet_balance_detailed("USDT")

                if balance_info:
                    # Available balance excludes uPnL and locked margin
                    self._exchange_balance = balance_info.get("wallet_balance", 0)
                    self._exchange_balance_available = balance_info.get("available_balance", 0)
                    exchange_upnl = balance_info.get("unrealized_pnl", 0)

                    # Calculate difference with bot balance
                    bot_balance = self.stats["current_balance"]

                    # For comparison, use wallet_balance (includes realized PnL)
                    # The difference should mainly be from funding fees and commissions
                    diff = self._exchange_balance - bot_balance
                    diff_percent = (diff / bot_balance * 100) if bot_balance > 0 else 0

                    if self.config.balance_sync_log_diff and abs(diff_percent) > 0.1:
                        logger.info(
                            f"💰 Balance sync: Exchange ${self._exchange_balance:.2f} | "
                            f"Bot ${bot_balance:.2f} | Diff: {diff:+.2f} ({diff_percent:+.2f}%) | "
                            f"uPnL: {exchange_upnl:+.2f}"
                        )

            else:
                # Fallback to simple balance
                balances = await self.connector.get_balance()
                if "USDT" in balances:
                    self._exchange_balance = balances["USDT"]

        except Exception as e:
            logger.debug(f"Balance sync error: {e}")

    def get_exchange_balance_info(self) -> Dict[str, Any]:
        """
        Get current exchange balance information.

        Returns:
            Dict with exchange balance details
        """
        return {
            "exchange_balance": self._exchange_balance,
            "exchange_available": self._exchange_balance_available,
            "bot_balance": self.stats["current_balance"],
            "difference": (self._exchange_balance - self.stats["current_balance"])
            if self._exchange_balance
            else None,
        }

    async def add_to_position(self, signal_id: str, add_percent: float = 50.0) -> bool:
        """
        Add to an existing position.

        Args:
            signal_id: Signal ID
            add_percent: Percentage of the current position to add

        Returns:
            True on success
        """
        tracked = self.tracked_signals.get(signal_id)
        if not tracked or tracked.status != "EXECUTED":
            return False

        add_size_usdt = tracked.position_size_usdt * (add_percent / 100)
        current_price = self.current_prices.get(tracked.symbol)

        if not current_price:
            return False

        try:
            order = await self.connector.place_market_order(
                symbol=tracked.symbol, side=tracked.side, size_usdt=add_size_usdt
            )

            if order:
                tracked.position_size_usdt += add_size_usdt
                old_value = tracked.executed_price * (tracked.position_size_usdt - add_size_usdt)
                new_value = current_price * add_size_usdt
                tracked.executed_price = (old_value + new_value) / tracked.position_size_usdt
                return True
        except Exception as e:
            logger.error(f"Failed to add to position {signal_id}: {e}")
        return False

    async def move_take_profit(self, signal_id: str, tp_index: int, new_price: float) -> bool:
        tracked = self.tracked_signals.get(signal_id)
        if not tracked or tracked.status != "EXECUTED":
            return False
        if tp_index >= len(tracked.take_profit):
            return False

        old_price = tracked.take_profit[tp_index]
        tracked.take_profit[tp_index] = new_price

        if tp_index < len(tracked.tp_order_ids) and tracked.tp_order_ids[tp_index]:
            try:
                await self.connector.cancel_order(
                    symbol=tracked.symbol, order_id=tracked.tp_order_ids[tp_index]
                )

                new_order = await self.connector.place_limit_order(
                    symbol=tracked.symbol,
                    side="SELL" if tracked.side == "BUY" else "BUY",
                    price=new_price,
                    size_usdt=tracked.position_size_usdt * (tracked.remaining_percent / 100),
                )

                if new_order:
                    tracked.tp_order_ids[tp_index] = new_order.get("orderId")

            except Exception as e:
                logger.error(f"Failed to move TP for {signal_id}: {e}")
                tracked.take_profit[tp_index] = old_price
                return False
        return True

    async def partial_close_signal(
        self, signal_id: str, close_percent: float, reason: str = "MANUAL"
    ) -> bool:
        tracked = self.tracked_signals.get(signal_id)
        if not tracked or tracked.status != "EXECUTED":
            return False

        if tracked.remaining_percent <= 0:
            return False

        close_size = (
            tracked.position_size_usdt * (tracked.remaining_percent / 100) * (close_percent / 100)
        )

        try:
            order = await self.connector.place_market_order(
                symbol=tracked.symbol,
                side="SELL" if tracked.side == "BUY" else "BUY",
                size_usdt=close_size,
            )

            if order:
                current_price = self.current_prices.get(tracked.symbol, tracked.executed_price)

                if tracked.side == "BUY":
                    pnl_pct = (
                        (current_price - tracked.executed_price) / tracked.executed_price
                    ) * 100
                else:
                    pnl_pct = (
                        (tracked.executed_price - current_price) / tracked.executed_price
                    ) * 100
                pnl_usdt = close_size * (pnl_pct / 100)

                actual_close = tracked.remaining_percent * (close_percent / 100)
                tracked.remaining_percent -= actual_close
                tracked.realized_pnl += pnl_pct * (actual_close / 100)
                tracked.realized_pnl_usdt += pnl_usdt

                tracked.partial_closes.append(
                    {
                        "reason": reason,
                        "close_percent": actual_close,
                        "exit_price": current_price,
                        "pnl_percent": pnl_pct,
                        "pnl_usdt": pnl_usdt,
                    }
                )

                return True
        except Exception as e:
            logger.error(f"Failed to partial close {signal_id}: {e}")
        return False
