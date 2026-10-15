"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : models.py
    Project     : Project-103
    Description : Backtesting data models and configurations. Contains all data
                  classes used for backtesting configuration, results, and
                  intermediate data structures.

    SPDX-License-Identifier: AGPL-3.0

"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

import pandas as pd


class ExecutionMode(Enum):
    """Order execution mode."""

    MARKET = "market"
    LIMIT = "limit"
    REALISTIC = "realistic"


class DataSource(Enum):
    """Data source type."""

    DATABASE = "database"
    CSV = "csv"
    API = "api"


@dataclass
class SlippageConfig:
    """Slippage model configuration."""

    fixed_slippage: float = 0.0001  # Fixed slippage in price percentage (0.01%)
    # Spread IN DOLLARS. If set, it replaces the percentage model: the broker's
    # spread barely depends on the price level, and a fraction calibrated on gold
    # at $2900 overstates costs 1.6x at $4650, and 5x over 16 years of GC history
    # ($1046..$5623).
    absolute_spread_usd: float = 0.0
    variable_slippage: float = 0.0002  # Variable slippage based on volume
    volatility_factor: float = 0.01  # Volatility impact factor (reduced from 1.0)


@dataclass
class CommissionConfig:
    """Commission model configuration."""

    maker_fee: float = 0.0002  # 0.02%
    taker_fee: float = 0.0004  # 0.04%
    use_tiered: bool = False
    tiers: Optional[List[Dict[str, Any]]] = None


@dataclass
class RiskConfig:
    """Risk management configuration."""

    max_position_size: float = 0.1  # 10% of balance per position
    max_drawdown: float = 0.2  # 20% max drawdown
    max_open_positions: int = 10
    max_risk_per_trade: float = 0.02  # 2% risk per trade
    risk_override: bool = False  # True when --risk CLI flag was explicitly passed
    leverage: float = 10.0  # Default 10x leverage
    position_sizing_method: str = "fixed_fractional"  # "fixed_fractional" or "fixed_balance"
    dynamic_risk: bool = False  # Reduce risk during drawdowns
    balance_cap: float = 0.0  # Cap effective balance for sizing (0 = disabled)
    # Volatility tilt: scale position size by trailing 5-day average daily range.
    # None = disabled. Keys: ref (reference range in $), min/max (multiplier clamp),
    # mode ('ratio' = continuous range5/ref, 'step' = min below ref / max at or above).
    vol_tilt: Optional[Dict[str, Any]] = None


@dataclass
class DataConfig:
    """Data loading configuration."""

    source: DataSource = DataSource.DATABASE
    database_url: Optional[str] = None
    csv_path: Optional[str] = None
    cache_enabled: bool = True
    cache_dir: str = "./cache"


@dataclass
class StrategyConfig:
    """Strategy configuration for backtesting."""

    name: str
    class_name: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    timeframes: List[str] = field(default_factory=lambda: ["15m"])


@dataclass
class LimitEntryBacktestConfig:
    """Configuration for limit entry simulation in backtest."""

    enabled: bool = True  # Enable limit entry simulation
    timeout_candles: int = 2  # Wait up to N candles for fill
    price_offset_percent: float = 0.02  # Limit price offset from signal price
    maker_fee: float = 0.0002  # Maker fee (0.02%)
    taker_fee: float = 0.00055  # Taker fee (0.055%)


@dataclass
class BacktestConfig:
    """Main backtest configuration."""

    # Time range
    start_date: datetime
    end_date: datetime

    # Account
    initial_balance: Decimal = Decimal("10000")
    base_currency: str = "USDT"

    # Trading pairs
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT"])

    # Strategies
    strategies: List[StrategyConfig] = field(default_factory=list)

    # Timeframes
    timeframes: List[str] = field(default_factory=lambda: ["15m"])

    # Execution timeframe for SL/TP checks (None = same as signal TF)
    execution_timeframe: Optional[str] = None

    # Tick-level execution: use MT5 tick data to resolve ambiguous SL/TP
    tick_execution: bool = False
    tick_cache_dir: str = "./cache/ticks"

    # Fixed spread in price units for realistic bid/ask entry pricing
    fixed_spread: float = 0.0

    # Execution
    execution_mode: ExecutionMode = ExecutionMode.REALISTIC
    slippage_config: SlippageConfig = field(default_factory=SlippageConfig)
    commission_config: CommissionConfig = field(default_factory=CommissionConfig)
    latency_ms: int = 50

    # Risk
    risk_config: RiskConfig = field(default_factory=RiskConfig)

    # Data
    data_config: DataConfig = field(default_factory=DataConfig)

    # Limit Entry (smart entry simulation)
    limit_entry_config: LimitEntryBacktestConfig = field(default_factory=LimitEntryBacktestConfig)

    # Realistic entry: enter at next candle's open instead of current candle's close
    entry_on_next_open: bool = True

    # Daily profit target: stop trading for the day when reached
    daily_profit_target_pct: Optional[float] = None  # e.g. 15.0 = stop at +15%
    daily_target_mode: str = "stop_new"  # stop_new | close_all | close_profitable

    # Live hedge semantics: emulate signal_lifecycle_manager's per-strategy gate.
    # False (default) = backtest rule: any own position on the symbol blocks a new
    # entry, so `max_positions` caps them regardless of side.
    # True = live rule under hedge_mode: only SAME-side own positions block, so a
    # strategy may hold a long and a short on one symbol at the same time.
    live_hedge_semantics: bool = False

    # Indicator window: compute indicators on last N candles (0 = full history)
    # Set to 500 to match live trading behavior where 500 candles are fetched each cycle
    indicator_window: int = 0

    # Regime gate (simple): block listed strategies when ADX exceeds threshold
    regime_gate_strategies: List[str] = field(default_factory=list)
    regime_gate_adx_threshold: float = 35.0
    regime_gate_adx_tf: str = "1m"

    # Regime gate (per-strategy): {strategy_name: {"min": float|None, "max": float|None, "tf": str}}
    # If a rule exists, it overrides the simple gate above for that strategy.
    # Block if ADX < min OR ADX > max.
    regime_gate_rules: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # EMA direction filter per-strategy:
    # {strategy_name: {"tf": "4h", "period": 50, "require_above": True, "require_slope_positive": True}}
    direction_filters: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Volatility filter per-strategy (block when ATR percentile is too low):
    # {strategy_name: {"atr_tf": "1m", "atr_period": 14, "lookback": 1440, "min_pct": 30}}
    volatility_filters: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Output options
    verbose: bool = True
    save_trades: bool = True
    save_signals: bool = True
    generate_report: bool = True
    report_format: Literal["html", "json"] = "html"
    output_dir: str = "./reports"


@dataclass
class EquityPoint:
    """Single point on equity curve."""

    timestamp: datetime
    balance: Decimal
    unrealized_pnl: Decimal
    total_equity: Decimal
    positions_count: int
    drawdown: float = 0.0
    drawdown_pct: float = 0.0


@dataclass
class TradeResult:
    """Result of a single trade."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    side: str = ""  # LONG or SHORT

    # Entry
    entry_price: float = 0.0
    entry_quantity: float = 0.0
    entry_time: datetime = field(default_factory=datetime.now)
    entry_commission: float = 0.0

    # Exit
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    exit_commission: float = 0.0
    exit_reason: str = ""

    # P&L
    pnl: float = 0.0
    pnl_pct: float = 0.0
    net_pnl: float = 0.0

    # Strategy
    strategy_name: str = ""
    signal_id: Optional[str] = None

    # Additional
    slippage: float = 0.0
    hold_time: timedelta = field(default_factory=timedelta)
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0

    # Metadata (for regime, strategy details, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "entry_quantity": self.entry_quantity,
            "entry_time": self.entry_time,
            "exit_price": self.exit_price,
            "exit_time": self.exit_time,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "net_pnl": self.net_pnl,
            "exit_reason": self.exit_reason,
            "strategy_name": self.strategy_name,
            "hold_time": self.hold_time.total_seconds() if self.hold_time else 0,
        }


@dataclass
class StrategyMetrics:
    """Performance metrics for a single strategy."""

    strategy_name: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0


@dataclass
class SymbolMetrics:
    """Performance metrics for a single symbol."""

    symbol: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_trade_pnl: float = 0.0
    total_volume: float = 0.0


@dataclass
class DrawdownInfo:
    """Detailed drawdown information.

    max_drawdown_pct — the percentage AT THE MOMENT of the maximum DOLLAR drawdown.
    This is not the largest percentage drawdown: if the worst dollar dip happened
    with small capital and another, smaller in dollars, with large capital, their
    percentages diverge. Do not compare this percentage between runs with
    different equity paths.

    max_drawdown_pct_true — the largest percentage drawdown of the equity curve.
    Use this one for comparisons.
    """

    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_pct_true: float = 0.0
    episodes: List[Dict[str, Any]] = field(default_factory=list)
    peak_date: Optional[datetime] = None
    trough_date: Optional[datetime] = None
    recovery_date: Optional[datetime] = None
    drawdown_duration: Optional[timedelta] = None
    recovery_duration: Optional[timedelta] = None


@dataclass
class BacktestResult:
    """Complete backtest results."""

    # Metadata
    backtest_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    start_date: datetime = field(default_factory=datetime.now)
    end_date: datetime = field(default_factory=datetime.now)
    duration: timedelta = field(default_factory=timedelta)
    config: Optional[BacktestConfig] = None

    # Main metrics
    initial_balance: Decimal = Decimal("10000")
    final_balance: Decimal = Decimal("10000")
    total_return: Decimal = Decimal("0")
    total_return_pct: float = 0.0
    annualized_return: float = 0.0

    # Trading metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    # Risk metrics
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0

    # Time metrics
    avg_trade_duration: timedelta = field(default_factory=timedelta)
    max_trade_duration: timedelta = field(default_factory=timedelta)
    time_in_market: float = 0.0

    # Commissions
    total_commission: float = 0.0
    total_slippage: float = 0.0

    # Detailed data
    trades: List[TradeResult] = field(default_factory=list)
    # Positions still OPEN at the end of the window. NOT included in the stats (the
    # engine only counts completed trades — that is more honest), but needed in the
    # report: otherwise a run up to today silently drops everything still in the market.
    open_positions: List[Any] = field(default_factory=list)
    equity_curve: Optional[pd.DataFrame] = None
    drawdown_series: Optional[pd.Series] = None
    daily_returns: Optional[pd.Series] = None
    monthly_returns: Optional[pd.DataFrame] = None

    # Strategy breakdown
    strategy_performance: Dict[str, StrategyMetrics] = field(default_factory=dict)
    symbol_performance: Dict[str, SymbolMetrics] = field(default_factory=dict)

    # Drawdown details
    drawdown_info: DrawdownInfo = field(default_factory=DrawdownInfo)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "backtest_id": self.backtest_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "duration_days": self.duration.days,
            "initial_balance": float(self.initial_balance),
            "final_balance": float(self.final_balance),
            "total_return_pct": self.total_return_pct,
            "annualized_return": self.annualized_return,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "total_commission": self.total_commission,
            "expectancy": self.expectancy,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        return f"""
========== BACKTEST RESULTS ==========
Period: {self.start_date.date()} to {self.end_date.date()}
Duration: {self.duration.days} days

RETURNS
-------
Initial Balance: ${float(self.initial_balance):,.2f}
Final Balance: ${float(self.final_balance):,.2f}
Total Return: {self.total_return_pct:.2f}%
Annualized Return: {self.annualized_return:.2f}%

TRADING
-------
Total Trades: {self.total_trades}
Win Rate: {self.win_rate:.2f}%
Profit Factor: {self.profit_factor:.2f}
Expectancy: ${self.expectancy:.2f}

RISK
----
Max Drawdown: {self.max_drawdown_pct:.2f}%
Sharpe Ratio: {self.sharpe_ratio:.2f}
Sortino Ratio: {self.sortino_ratio:.2f}
VaR (95%): {self.var_95:.2f}%

COSTS
-----
Total Commission: ${self.total_commission:.2f}
Total Slippage: ${self.total_slippage:.2f}
======================================
"""
