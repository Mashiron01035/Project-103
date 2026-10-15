"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : backtest_engine.py
    Project     : Project-103
    Description : Main backtest engine. Coordinates the backtesting process by
                  loading historical data, running strategies on each candle,
                  managing portfolio and positions, and collecting results.

    SPDX-License-Identifier: AGPL-3.0

"""

import bisect
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import polars as pl

from src.backtesting.analysis.performance_analyzer import PerformanceAnalyzer
from src.backtesting.data.data_loader import DataLoader
from src.backtesting.engine.pending_orders import (
    LimitEntryConfig,
    PendingOrder,
    PendingOrdersManager,
)
from src.backtesting.models import (
    BacktestConfig,
    BacktestResult,
    DrawdownInfo,
    StrategyMetrics,
    SymbolMetrics,
    TradeResult,
)
from src.backtesting.portfolio.commission_model import create_commission_model
from src.backtesting.portfolio.portfolio_manager import PortfolioManager
from src.backtesting.portfolio.slippage_model import MarketState, create_slippage_model
from src.core.trading_pairs import trading_pairs
from src.strategies import MarketRegime, StrategySelector

logger = logging.getLogger(__name__)


def _norm_side(side) -> str:
    """Normalise a side to LONG/SHORT.

    Signals use BUY/SELL, portfolio positions use LONG/SHORT; both may arrive as
    an enum. Returns "" for anything unrecognised so it never matches.
    """
    v = getattr(side, "value", side)
    v = str(v or "").strip().upper()
    if v in ("BUY", "LONG"):
        return "LONG"
    if v in ("SELL", "SHORT"):
        return "SHORT"
    return ""


class BacktestEngine:
    """
    Main backtesting engine.

    Coordinates all components to run historical simulations
    of trading strategies.
    """

    def __init__(self, config: BacktestConfig):
        """
        Initialize backtest engine.

        Args:
            config: Backtest configuration
        """
        self.config = config
        self.data_loader = DataLoader(config.data_config)

        # Create commission model (per-symbol override from trading_pairs.yaml)
        pair_info = trading_pairs.get(config.symbols[0]) if config.symbols else None
        if pair_info and pair_info.commission_type == "per_lot":
            commission_model = create_commission_model(
                model_type="per_lot", commission_per_lot=pair_info.commission_per_lot
            )
            logger.info(f"Using per-lot commission: ${pair_info.commission_per_lot}/lot")
        else:
            commission_model = create_commission_model(
                model_type="fixed",
                maker_fee=config.commission_config.maker_fee,
                taker_fee=config.commission_config.taker_fee,
            )

        if getattr(config.slippage_config, "absolute_spread_usd", 0) > 0:
            # Spread is given in dollars — half per side, the full spread per round trip.
            from src.backtesting.portfolio.slippage_model import AbsoluteSlippage

            slippage_model = AbsoluteSlippage(config.slippage_config.absolute_spread_usd)
        elif config.slippage_config.fixed_slippage == 0:
            slippage_model = create_slippage_model(model_type="zero")
        else:
            slippage_model = create_slippage_model(
                model_type="fixed",
                fixed_slippage=config.slippage_config.fixed_slippage,
            )

        self.portfolio = PortfolioManager(
            initial_balance=config.initial_balance,
            commission_model=commission_model,
            slippage_model=slippage_model,
            leverage=config.risk_config.leverage,
            balance_cap=config.risk_config.balance_cap,
        )

        self.analyzer = PerformanceAnalyzer()

        # Strategy selector for regime-based switching
        # DISABLED by default - strategies work better independently
        # Transition logic causes conflicts, confirmation too slow (20 candles = 5h)
        self.strategy_selector = StrategySelector(use_transition_logic=False)
        self.regime_switching_enabled = False  # DISABLED - each strategy decides its conditions

        # State
        self.strategies: Dict[str, Any] = {}
        self.data: Dict[str, pd.DataFrame] = {}
        self.signals: List[Dict[str, Any]] = []
        self.current_prices: Dict[str, float] = {}
        self.current_highs: Dict[str, float] = {}  # For accurate SL check
        self.current_lows: Dict[str, float] = {}  # For accurate SL check
        self.current_opens: Dict[str, float] = {}  # For next-candle entry

        # Queue for next-candle-open entry (realistic entry simulation)
        self._pending_next_open_signals: List[Dict[str, Any]] = []

        # Pre-calculated data cache (for speed optimization)
        self._candles_cache: Dict[str, List] = {}  # symbol_tf -> List[Candle]
        self._indicators_cache: Dict[str, Dict] = {}  # symbol_tf_strategy -> indicators
        self._candle_index_cache: Dict[
            str, Dict[int, int]
        ] = {}  # symbol_tf -> {timestamp_ms: index}
        # Bounded history window handed to strategy.analyze() — avoids the O(n^2)
        # prefix-copy per candle. Set to the LIVE warmup window (config
        # warmup.lookback_candles = 60000) so backtest sees exactly what live
        # sees → no backtest/live divergence, and it covers every strategy's
        # lookback (e.g. a 4h EMA(50) ≈ 12k 1m bars). Parity-gated.
        self._signal_history_window = 60000

        # Pending Orders Manager for limit entry simulation.
        # Always instantiated so per-signal `metadata.use_limit_entry=True` can
        # opt-in even when global limit_entry is disabled.
        self.pending_orders_manager: Optional[PendingOrdersManager] = None
        self._limit_entry_global_enabled = bool(config.limit_entry_config.enabled)
        limit_config = LimitEntryConfig(
            enabled=True,
            timeout_candles=config.limit_entry_config.timeout_candles,
            price_offset_percent=config.limit_entry_config.price_offset_percent,
            maker_fee=config.limit_entry_config.maker_fee,
            taker_fee=config.limit_entry_config.taker_fee,
        )
        self.pending_orders_manager = PendingOrdersManager(
            config=limit_config,
            on_order_filled=self._on_pending_order_filled,
            on_order_timeout=self._on_pending_order_timeout,
        )
        if self._limit_entry_global_enabled:
            logger.info(
                f"Limit entry enabled globally (timeout={limit_config.timeout_candles} candles)"
            )
        else:
            logger.info("Limit entry available per-signal via metadata.use_limit_entry=True")

        # Tick-level execution for accurate SL/TP resolution
        self.tick_resolver = None
        self._tick_data_loader = None
        self.current_candle_times: Dict[str, tuple] = {}  # symbol -> (start_ms, end_ms)
        if getattr(config, "tick_execution", False):
            from src.backtesting.data.tick_data_loader import TickDataLoader
            from src.backtesting.engine.tick_resolver import TickResolver

            cache_dir = getattr(config, "tick_cache_dir", "./cache/ticks")
            self._tick_data_loader = TickDataLoader(cache_dir=cache_dir)
            self.tick_resolver = TickResolver(self._tick_data_loader)
            logger.info("Tick-level execution enabled for SL/TP resolution")

        # Candle index tracking for limit orders
        self._current_candle_idx: int = 0
        self._current_ts: Optional[datetime] = None

        # Volatility tilt: day -> size multiplier, built from trailing daily range
        self._vol_tilt_map: Dict[str, float] = {}

        # Daily profit target tracking
        self._daily_profit_target_pct = config.daily_profit_target_pct
        self._daily_target_mode = config.daily_target_mode
        self._day_start_balance: float = float(config.initial_balance)
        self._current_day: Optional[str] = None
        self._daily_target_hit: bool = False

        # Regime gate: ADX series per (symbol, tf), per-strategy rules
        self._regime_adx: Dict[str, Dict[str, pd.Series]] = {}
        self._regime_gate_strategies = set(getattr(config, "regime_gate_strategies", []) or [])
        self._regime_gate_adx_threshold = float(getattr(config, "regime_gate_adx_threshold", 35.0))
        self._regime_gate_adx_tf = str(getattr(config, "regime_gate_adx_tf", "1m"))
        self._regime_gate_rules: Dict[str, Dict[str, Any]] = (
            getattr(config, "regime_gate_rules", {}) or {}
        )
        self._regime_gate_blocks: Dict[str, int] = {}

        # Direction filter: EMA series per (symbol, tf, period)
        self._direction_filters: Dict[str, Dict[str, Any]] = (
            getattr(config, "direction_filters", {}) or {}
        )
        self._direction_emas: Dict[str, Dict[str, pd.Series]] = {}
        if self._direction_filters:
            logger.info("Direction filter ENABLED:")
            for strat, f in self._direction_filters.items():
                tf = f.get("tf", "4h")
                period = f.get("period", 50)
                conds = []
                if f.get("require_above"):
                    conds.append(f"price > EMA({period},{tf})")
                if f.get("require_slope_positive"):
                    conds.append("slope > 0")
                logger.info(f"  {strat}: keep when {' AND '.join(conds)}")

        # Volatility filter: ATR percentile series per (symbol, tf, period, lookback)
        self._volatility_filters: Dict[str, Dict[str, Any]] = (
            getattr(config, "volatility_filters", {}) or {}
        )
        self._volatility_atr_pct: Dict[str, Dict[str, pd.Series]] = {}
        if self._volatility_filters:
            logger.info("Volatility filter ENABLED:")
            for strat, f in self._volatility_filters.items():
                tf = f.get("atr_tf", "1m")
                period = f.get("atr_period", 14)
                lookback = f.get("lookback", 1440)
                min_pct = f.get("min_pct")
                logger.info(
                    f"  {strat}: keep when ATR({period},{tf}) percentile "
                    f"(rolling {lookback}) >= {min_pct}"
                )
        if self._regime_gate_rules:
            logger.info("Per-strategy regime gate ENABLED:")
            for strat, rule in self._regime_gate_rules.items():
                tf = rule.get("tf", "1m")
                lo = rule.get("min")
                hi = rule.get("max")
                cond = []
                if lo is not None:
                    cond.append(f"ADX({tf}) >= {lo}")
                if hi is not None:
                    cond.append(f"ADX({tf}) <= {hi}")
                logger.info(f"  {strat}: keep when {' AND '.join(cond) if cond else 'always'}")
        elif self._regime_gate_strategies:
            logger.info(
                f"Simple regime gate ENABLED: blocking {sorted(self._regime_gate_strategies)} "
                f"when ADX({self._regime_gate_adx_tf}) > {self._regime_gate_adx_threshold}"
            )

        logger.info(f"BacktestEngine initialized for {config.start_date} to {config.end_date}")

    @property
    def _price_tf_minutes(self) -> float:
        """Duration of the price candle in minutes (timestamp = open, prices = close)."""
        from src.backtesting.data.data_loader import TIMEFRAME_MINUTES

        price_tf = self.config.execution_timeframe or self.config.timeframes[0]
        return float(TIMEFRAME_MINUTES.get(price_tf, 1))

    def _compute_adx_for_tf(self, symbol: str, tf: str) -> Optional[pd.Series]:
        """Compute ADX(14) series for given symbol and timeframe."""
        key = f"{symbol}_{tf}"
        # If TF not loaded directly, resample from 1m
        if key not in self.data:
            base_key = f"{symbol}_1m"
            if base_key not in self.data:
                logger.warning(f"Regime gate: no 1m data for {symbol}, cannot compute {tf}")
                return None
            base = (
                self.data[base_key].to_pandas().set_index("timestamp")
            )  # TEMP bridge (Stage 2 → polars)
            tf_to_rule = {
                "1m": "1min",
                "3m": "3min",
                "5m": "5min",
                "15m": "15min",
                "30m": "30min",
                "1h": "1H",
                "4h": "4H",
                "1d": "1D",
            }
            if tf not in tf_to_rule:
                logger.warning(f"Regime gate: unsupported TF {tf}")
                return None
            df = (
                base.resample(tf_to_rule[tf])
                .agg(
                    open=("open", "first"),
                    high=("high", "max"),
                    low=("low", "min"),
                    close=("close", "last"),
                )
                .dropna()
            )
        else:
            df = self.data[key].to_pandas().set_index("timestamp")  # TEMP bridge (Stage 2 → polars)
        h, l, c = df["high"], df["low"], df["close"]  # noqa: E741 - OHLC shorthand
        tr = pd.concat(
            [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(14, min_periods=14).mean()
        plus_dm = h.diff().where((h.diff() > l.shift().sub(l).abs()) & (h.diff() > 0), 0.0)
        minus_dm = (
            l.shift()
            .sub(l)
            .where((l.shift().sub(l) > h.diff().abs()) & (l.shift().sub(l) > 0), 0.0)
        )
        plus_di = 100.0 * plus_dm.rolling(14, min_periods=14).mean() / atr
        minus_di = 100.0 * minus_dm.rolling(14, min_periods=14).mean() / atr
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.rolling(14, min_periods=14).mean()
        return adx

    def _compute_regime_adx(self, symbol: str) -> None:
        """Pre-compute ADX(14) series for all TFs needed by regime gate rules."""
        tfs_needed = set()
        if self._regime_gate_rules:
            for rule in self._regime_gate_rules.values():
                tfs_needed.add(rule.get("tf", "1m"))
        if self._regime_gate_strategies:
            tfs_needed.add(self._regime_gate_adx_tf)
        if not tfs_needed:
            return

        if symbol not in self._regime_adx:
            self._regime_adx[symbol] = {}

        for tf in tfs_needed:
            adx = self._compute_adx_for_tf(symbol, tf)
            if adx is None:
                continue
            self._regime_adx[symbol][tf] = adx
            logger.info(
                f"Regime gate: computed ADX({tf}) for {symbol}, "
                f"{adx.notna().sum()}/{len(adx)} valid values, "
                f"mean={adx.mean():.1f}, p75={adx.quantile(0.75):.1f}"
            )

    def _adx_at(self, symbol: str, tf: str, timestamp: datetime) -> Optional[float]:
        adx_series = self._regime_adx.get(symbol, {}).get(tf)
        if adx_series is None:
            return None
        ts = pd.Timestamp(timestamp).floor("min")
        try:
            val = adx_series.asof(ts)
        except (KeyError, ValueError):
            return None
        if pd.isna(val):
            return None
        return float(val)

    def _compute_direction_emas(self, symbol: str) -> None:
        """Pre-compute EMA + slope series for direction filters."""
        if not self._direction_filters:
            return
        base_key = f"{symbol}_1m"
        if base_key not in self.data:
            logger.warning(f"Direction filter: no 1m data for {symbol}")
            return
        base = self.data[base_key]
        tf_to_rule = {
            "1m": "1min",
            "3m": "3min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1h",
            "4h": "4h",
            "1d": "1D",
        }
        configs_needed = set()
        for f in self._direction_filters.values():
            tf = f.get("tf", "4h")
            period = int(f.get("period", 50))
            configs_needed.add((tf, period))

        if symbol not in self._direction_emas:
            self._direction_emas[symbol] = {}

        for tf, period in configs_needed:
            if tf not in tf_to_rule:
                continue
            df_tf = (
                base.resample(tf_to_rule[tf])
                .agg(
                    close=("close", "last"),
                )
                .dropna()
            )
            ema = df_tf["close"].ewm(span=period, adjust=False).mean()
            slope = ema.diff(3)
            price_vs = df_tf["close"] - ema
            key_ema = f"ema_{period}_{tf}"
            key_slope = f"slope_{period}_{tf}"
            key_pvs = f"pvs_{period}_{tf}"
            self._direction_emas[symbol][key_ema] = ema
            self._direction_emas[symbol][key_slope] = slope
            self._direction_emas[symbol][key_pvs] = price_vs
            logger.info(
                f"Direction filter: computed EMA({period},{tf}) for {symbol}, "
                f"{ema.notna().sum()}/{len(ema)} valid"
            )

    def _compute_volatility_atr(self, symbol: str) -> None:
        """Pre-compute ATR percentile series for volatility filters."""
        if not self._volatility_filters:
            return
        base_key = f"{symbol}_1m"
        if base_key not in self.data:
            logger.warning(f"Volatility filter: no 1m data for {symbol}")
            return
        base = self.data[base_key]
        tf_to_rule = {
            "1m": "1min",
            "3m": "3min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1h",
            "4h": "4h",
            "1d": "1D",
        }
        configs_needed = set()
        for f in self._volatility_filters.values():
            tf = f.get("atr_tf", "1m")
            period = int(f.get("atr_period", 14))
            lookback = int(f.get("lookback", 1440))
            configs_needed.add((tf, period, lookback))

        if symbol not in self._volatility_atr_pct:
            self._volatility_atr_pct[symbol] = {}

        for tf, period, lookback in configs_needed:
            if tf not in tf_to_rule:
                continue
            if tf == "1m":
                df_tf = base
            else:
                df_tf = (
                    base.resample(tf_to_rule[tf])
                    .agg(
                        high=("high", "max"),
                        low=("low", "min"),
                        close=("close", "last"),
                    )
                    .dropna()
                )
            h = df_tf["high"]
            lo = df_tf["low"]
            c = df_tf["close"]
            tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(
                axis=1
            )
            atr = tr.rolling(period, min_periods=period).mean()
            atr_pct = (
                atr.rolling(lookback, min_periods=max(lookback // 2, period)).rank(pct=True) * 100
            )
            key = f"atr_pct_{period}_{tf}_{lookback}"
            self._volatility_atr_pct[symbol][key] = atr_pct
            logger.info(
                f"Volatility filter: computed ATR({period},{tf}) pct over {lookback} bars "
                f"for {symbol}, mean={atr_pct.mean():.1f}, p25={atr_pct.quantile(0.25):.1f}"
            )

    def _is_blocked_by_volatility_filter(
        self, strategy_name: str, symbol: str, timestamp: datetime
    ) -> bool:
        if not self._volatility_filters:
            return False
        f = self._volatility_filters.get(strategy_name)
        if not f:
            return False
        tf = f.get("atr_tf", "1m")
        period = int(f.get("atr_period", 14))
        lookback = int(f.get("lookback", 1440))
        min_pct = f.get("min_pct")
        if min_pct is None:
            return False
        key = f"atr_pct_{period}_{tf}_{lookback}"
        series = self._volatility_atr_pct.get(symbol, {}).get(key)
        if series is None:
            return False
        ts = pd.Timestamp(timestamp).floor("min")
        try:
            val = series.asof(ts)
        except (KeyError, ValueError):
            return False
        if val is None or pd.isna(val):
            return False
        if val < float(min_pct):
            self._regime_gate_blocks[f"{strategy_name}_vol"] = (
                self._regime_gate_blocks.get(f"{strategy_name}_vol", 0) + 1
            )
            return True
        return False

    def _is_blocked_by_direction_filter(
        self, strategy_name: str, symbol: str, timestamp: datetime
    ) -> bool:
        if not self._direction_filters:
            return False
        f = self._direction_filters.get(strategy_name)
        if not f:
            return False
        tf = f.get("tf", "4h")
        period = int(f.get("period", 50))
        require_above = bool(f.get("require_above", False))
        require_slope_pos = bool(f.get("require_slope_positive", False))

        emas = self._direction_emas.get(symbol, {})
        ts = pd.Timestamp(timestamp).floor("min")

        if require_above:
            pvs = emas.get(f"pvs_{period}_{tf}")
            if pvs is not None:
                try:
                    val = pvs.asof(ts)
                except (KeyError, ValueError):
                    val = None
                if val is None or pd.isna(val) or val <= 0:
                    self._regime_gate_blocks[f"{strategy_name}_dir"] = (
                        self._regime_gate_blocks.get(f"{strategy_name}_dir", 0) + 1
                    )
                    return True

        if require_slope_pos:
            slope = emas.get(f"slope_{period}_{tf}")
            if slope is not None:
                try:
                    val = slope.asof(ts)
                except (KeyError, ValueError):
                    val = None
                if val is None or pd.isna(val) or val <= 0:
                    self._regime_gate_blocks[f"{strategy_name}_dir"] = (
                        self._regime_gate_blocks.get(f"{strategy_name}_dir", 0) + 1
                    )
                    return True

        return False

    def _is_blocked_by_regime_gate(
        self, strategy_name: str, symbol: str, timestamp: datetime
    ) -> bool:
        """Return True if this signal should be blocked by regime gate."""
        # Per-strategy rules take priority
        if self._regime_gate_rules and strategy_name in self._regime_gate_rules:
            rule = self._regime_gate_rules[strategy_name]
            tf = rule.get("tf", "1m")
            adx_val = self._adx_at(symbol, tf, timestamp)
            if adx_val is None:
                return False
            lo = rule.get("min")
            hi = rule.get("max")
            if lo is not None and adx_val < lo:
                self._regime_gate_blocks[strategy_name] = (
                    self._regime_gate_blocks.get(strategy_name, 0) + 1
                )
                return True
            if hi is not None and adx_val > hi:
                self._regime_gate_blocks[strategy_name] = (
                    self._regime_gate_blocks.get(strategy_name, 0) + 1
                )
                return True
            return False

        # Fallback: simple gate
        if not self._regime_gate_strategies:
            return False
        if strategy_name not in self._regime_gate_strategies:
            return False
        adx_val = self._adx_at(symbol, self._regime_gate_adx_tf, timestamp)
        if adx_val is None:
            return False
        if adx_val > self._regime_gate_adx_threshold:
            self._regime_gate_blocks[strategy_name] = (
                self._regime_gate_blocks.get(strategy_name, 0) + 1
            )
            return True
        return False

    async def run(self) -> BacktestResult:
        """
        Run the backtest.

        Returns:
            BacktestResult with all metrics and trade details
        """
        start_time = datetime.now()
        logger.info("Starting backtest...")

        try:
            # Load data
            await self._load_data()

            # Initialize strategies
            self._initialize_strategies()

            # Setup regime gate / direction filter from YAML (after strategies init)
            self._setup_regime_filters()

            # Volatility tilt map (needs loaded candles)
            self._build_vol_tilt_map()

            # Pre-calculate all indicators (major speed optimization)
            self._precalculate_indicators()

            # Run simulation
            await self._run_simulation()

            # Generate results
            result = self._generate_results(start_time)

            self._log_rejection_reports()
            logger.info(f"Backtest completed in {datetime.now() - start_time}")
            logger.info(result.summary())

            return result

        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            raise

    def _log_rejection_reports(self, top_days: int = 3) -> None:
        """Why a strategy did not trade — one block at the end of the run.

        Answering that used to mean grepping hundreds of thousands of
        "REJECTED: ..." lines, one per rejected candle. This is the same
        information, counted: which filter fired how many times and on which days
        it cut the most.

        Days are taken from the CANDLE time, not the log write time — in a backtest
        these are completely different things, three years replay in minutes.
        """
        reports, rows = [], []
        for name, info in self.strategies.items():
            strategy = info.get("instance")
            if strategy is None or not getattr(strategy, "rejections", None):
                continue
            reports.append(strategy.rejection_report(top_days=top_days))
            for day, counts in getattr(strategy, "_rejections_by_day", {}).items():
                for reason, n in counts.items():
                    rows.append((name, day, reason, n))
        if not reports:
            return
        logger.info("Strategy rejections (why there were fewer entries):\n" + "\n".join(reports))
        self._dump_rejections_csv(rows)

    @staticmethod
    def _fmt_span(td) -> str:
        if td is None:
            return "—"
        total_h = td.total_seconds() / 3600
        if total_h < 48:
            return f"{total_h:.0f}h"
        return f"{total_h / 24:.1f}d"

    def _log_drawdown_episodes(self, info, top: int = 8) -> None:
        """Drawdown breakdown: how many, how deep and how long they lasted.

        A single "max drawdown" number hides what matters in practice: sitting
        through 15% over two days and the same 15% over six weeks are very
        different experiences with an identical metric. So this reports depth,
        time to trough and time to recovery.
        """
        eps = getattr(info, "episodes", None)
        if not eps:
            return

        deep = sum(1 for e in eps if e["depth_pct"] >= 5.0)
        unrecovered = sum(1 for e in eps if not e["recovered"])
        lines = [
            f"DRAWDOWNS: total {len(eps)}, deeper than 5% — {deep}, "
            f"max {info.max_drawdown_pct_true:.2f}%",
            f"  (the engine prints {info.max_drawdown_pct:.2f}% — the percentage at "
            f"the DOLLAR maximum, not comparable across runs)",
            f"{'depth':>9s}{'$':>10s}{'start':>14s}"
            f"{'to trough':>10s}{'recovery':>10s}{'total':>9s}",
        ]
        for e in eps[:top]:
            tail = "" if e["recovered"] else "   not recovered"
            # year omitted: a run always sits within one period, and the year
            # breaks the column width; full dates are in the CSV
            started = f"{e['peak_time']:%m-%d %H:%M}"
            lines.append(
                f"{e['depth_pct']:>8.2f}%"
                f"{e['depth']:>10,.0f}"
                f"{started:>14s}"
                f"{self._fmt_span(e['to_trough']):>10s}"
                f"{self._fmt_span(e['to_recover']):>10s}"
                f"{self._fmt_span(e['total']):>9s}{tail}"
            )
        if len(eps) > top:
            lines.append(f"  ... {len(eps) - top} more, full list in the CSV")
        if unrecovered:
            lines.append(f"  not recovered by the end of the period: {unrecovered}")
        logger.info("\n".join(lines))
        self._dump_drawdowns_csv(eps)

    def _dump_drawdowns_csv(self, episodes) -> None:
        import csv
        import os

        log_path = os.environ.get("BACKTEST_SESSION_LOG", "")
        if not log_path:
            return
        out = os.path.splitext(log_path)[0] + "_drawdowns.csv"
        try:
            with open(out, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(
                    [
                        "depth_pct",
                        "depth_usd",
                        "peak_time",
                        "trough_time",
                        "end_time",
                        "peak_equity",
                        "trough_equity",
                        "hours_to_trough",
                        "hours_to_recover",
                        "hours_total",
                        "recovered",
                    ]
                )
                for e in episodes:
                    w.writerow(
                        [
                            round(e["depth_pct"], 4),
                            round(e["depth"], 2),
                            e["peak_time"],
                            e["trough_time"],
                            e["end_time"] or "",
                            round(e["peak_equity"], 2),
                            round(e["trough_equity"], 2),
                            round(e["to_trough"].total_seconds() / 3600, 2),
                            round(e["to_recover"].total_seconds() / 3600, 2)
                            if e["to_recover"] is not None
                            else "",
                            round(e["total"].total_seconds() / 3600, 2),
                            int(e["recovered"]),
                        ]
                    )
            logger.info(f"Full drawdown breakdown -> {out}")
        except OSError as exc:
            logger.warning(f"Could not write the drawdown breakdown: {exc}")

    def _dump_rejections_csv(self, rows) -> None:
        """Full per-day breakdown — next to the log rather than inside it.

        The log keeps a few-line summary; the full day x reason table goes to a
        CSV, so "why were there no entries on the 15th" is one filter in a table
        instead of a grep through hundreds of thousands of lines.
        """
        if not rows:
            return
        import csv
        import os

        log_path = os.environ.get("BACKTEST_SESSION_LOG", "")
        if not log_path:
            return
        out = os.path.splitext(log_path)[0] + "_rejections.csv"
        try:
            with open(out, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["strategy", "day", "reason", "count"])
                w.writerows(sorted(rows))
            logger.info(f"Per-day rejection breakdown -> {out}")
        except OSError as exc:
            logger.warning(f"Could not write the rejection breakdown: {exc}")

    async def _load_data(self):
        """Load historical data for all symbols."""
        logger.info(f"Loading data for {self.config.symbols}")

        for symbol in self.config.symbols:
            for timeframe in self.config.timeframes:
                data = await self.data_loader.load_candles(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_date=self.config.start_date,
                    end_date=self.config.end_date,
                )

                if data.is_empty():
                    logger.warning(f"No data for {symbol} {timeframe}")
                    continue

                key = f"{symbol}_{timeframe}"
                self.data[key] = data

                logger.info(f"Loaded {len(data)} candles for {key}")

            # Load execution timeframe data if configured
            exec_tf = self.config.execution_timeframe
            if exec_tf and exec_tf not in self.config.timeframes:
                exec_data = await self.data_loader.load_candles(
                    symbol=symbol,
                    timeframe=exec_tf,
                    start_date=self.config.start_date,
                    end_date=self.config.end_date,
                )

                if not exec_data.is_empty():
                    exec_key = f"{symbol}_{exec_tf}"
                    self.data[exec_key] = exec_data
                    logger.info(f"Loaded {len(exec_data)} execution candles for {exec_key}")
                else:
                    logger.warning(f"No execution TF data for {symbol} {exec_tf}")

        # Prefetch tick data if tick execution enabled
        if self._tick_data_loader is not None:
            for symbol in self.config.symbols:
                logger.info(f"Prefetching tick data for {symbol}...")
                tick_count = await self._tick_data_loader.prefetch_ticks(
                    symbol, self.config.start_date, self.config.end_date
                )
                if tick_count == 0:
                    logger.warning(
                        f"No tick data for {symbol} — "
                        f"tick execution will fall back to candle-based"
                    )

    def _build_vol_tilt_map(self):
        """Build day -> position size multiplier from trailing 5-day average daily range.

        Only completed prior days are used (shift 1): entering day D, the multiplier
        is derived from days D-5..D-1, so nothing from D leaks into its own sizing.
        Days without 5 prior sessions get 1.0 (no tilt).
        """
        cfg = self.config.risk_config.vol_tilt
        if not cfg:
            return

        key = f"{self.config.symbols[0]}_{self.config.timeframes[0]}"
        if key not in self.data:
            logger.warning(f"Vol tilt: no data for {key}, tilt disabled")
            return

        df = self.data[key].to_pandas().set_index("timestamp")
        day = pd.DataFrame(
            {
                "h": df["high"].resample("1D").max(),
                "l": df["low"].resample("1D").min(),
                "n": df["close"].resample("1D").count(),
            }
        ).dropna()
        day = day[day["n"] > 200]  # drop weekends/holidays/half sessions

        range5 = (day["h"] - day["l"]).rolling(5).mean().shift(1)

        ref = float(cfg.get("ref", 80.0))
        lo = float(cfg.get("min", 0.5))
        hi = float(cfg.get("max", 1.0))
        mode = str(cfg.get("mode", "ratio"))

        if mode == "step":
            mult = range5.apply(lambda r: lo if r < ref else hi)
        else:
            mult = (range5 / ref).clip(lower=lo, upper=hi)

        self._vol_tilt_map = {d.strftime("%Y-%m-%d"): float(m) for d, m in mult.dropna().items()}

        vals = list(self._vol_tilt_map.values())
        logger.info(
            f"Vol tilt built ({mode}, ref=${ref:.0f}, clamp {lo:.2f}-{hi:.2f}): "
            f"{len(vals)} days, mean mult={sum(vals)/len(vals):.3f}, "
            f"days at floor={sum(1 for v in vals if v <= lo + 1e-9)}"
        )

    def _vol_tilt_multiplier(self) -> float:
        """Size multiplier for the current simulation day (1.0 when disabled/unknown)."""
        if not self._vol_tilt_map or self._current_ts is None:
            return 1.0
        return self._vol_tilt_map.get(self._current_ts.strftime("%Y-%m-%d"), 1.0)

    def _setup_regime_filters(self):
        """Build regime gate and direction filter rules from YAML
        (only for strategies not already configured via CLI), then
        pre-compute the required ADX/EMA series for each symbol.
        """
        for strategy_name, info in self.strategies.items():
            params = info.get("parameters", {}) or {}

            # YAML regime_gate (skip if enabled=false)
            yaml_gate = params.get("regime_gate")
            if (
                yaml_gate
                and yaml_gate.get("enabled", True)
                and strategy_name not in self._regime_gate_rules
            ):
                rule: Dict[str, Any] = {"tf": yaml_gate.get("adx_tf", "1m")}
                if yaml_gate.get("adx_max") is not None:
                    rule["max"] = float(yaml_gate["adx_max"])
                if yaml_gate.get("adx_min") is not None:
                    rule["min"] = float(yaml_gate["adx_min"])
                if "max" in rule or "min" in rule:
                    self._regime_gate_rules[strategy_name] = rule
                    logger.info(f"YAML regime_gate for {strategy_name}: {rule}")
            elif yaml_gate and not yaml_gate.get("enabled", True):
                logger.info(f"YAML regime_gate for {strategy_name}: DISABLED")

            # YAML direction_filter (skip if enabled=false)
            yaml_dir = params.get("direction_filter")
            if (
                yaml_dir
                and yaml_dir.get("enabled", True)
                and strategy_name not in self._direction_filters
            ):
                rule = {
                    "tf": yaml_dir.get("ema_tf", "4h"),
                    "period": int(yaml_dir.get("ema_period", 50)),
                    "require_above": bool(yaml_dir.get("require_above", False)),
                    "require_slope_positive": bool(
                        yaml_dir.get(
                            "require_rising",
                            yaml_dir.get("require_slope_positive", False),
                        )
                    ),
                }
                if rule["require_above"] or rule["require_slope_positive"]:
                    self._direction_filters[strategy_name] = rule
                    logger.info(f"YAML direction_filter for {strategy_name}: {rule}")
            elif yaml_dir and not yaml_dir.get("enabled", True):
                logger.info(f"YAML direction_filter for {strategy_name}: DISABLED")

            # YAML volatility_filter (skip if enabled=false)
            yaml_vol = params.get("volatility_filter")
            if (
                yaml_vol
                and yaml_vol.get("enabled", True)
                and strategy_name not in self._volatility_filters
            ):
                rule = {
                    "atr_tf": yaml_vol.get("atr_tf", "1m"),
                    "atr_period": int(yaml_vol.get("atr_period", 14)),
                    "lookback": int(yaml_vol.get("lookback", 1440)),
                    "min_pct": yaml_vol.get("min_pct"),
                }
                if rule["min_pct"] is not None:
                    self._volatility_filters[strategy_name] = rule
                    logger.info(f"YAML volatility_filter for {strategy_name}: {rule}")
            elif yaml_vol and not yaml_vol.get("enabled", True):
                logger.info(f"YAML volatility_filter for {strategy_name}: DISABLED")

        # Pre-compute ADX for regime gate
        if self._regime_gate_strategies or self._regime_gate_rules:
            for symbol in self.config.symbols:
                self._compute_regime_adx(symbol)

        # Pre-compute EMA for direction filter
        if self._direction_filters:
            for symbol in self.config.symbols:
                self._compute_direction_emas(symbol)

        # Pre-compute ATR percentile for volatility filter
        if self._volatility_filters:
            for symbol in self.config.symbols:
                self._compute_volatility_atr(symbol)

    def _initialize_strategies(self):
        """Initialize strategy instances using the strategy registry."""
        from src.strategies import create_strategy, list_available_strategies

        available = list_available_strategies()
        logger.debug(f"Available strategies: {available}")

        for strategy_config in self.config.strategies:
            if not strategy_config.enabled:
                continue

            try:
                strategy_name = strategy_config.name

                # Inject account balance so strategies can track daily PnL limits
                if "account_balance" not in strategy_config.parameters:
                    strategy_config.parameters["account_balance"] = self.config.initial_balance

                # Create strategy instance using registry
                strategy = create_strategy(strategy_name, strategy_config.parameters)

                if strategy is not None:
                    self.strategies[strategy_name] = {
                        "instance": strategy,
                        "config": strategy_config,
                        "parameters": strategy_config.parameters,
                        "timeframes": strategy_config.timeframes,
                    }
                    logger.info(f"Initialized strategy: {strategy_name}")
                else:
                    logger.warning(
                        f"Strategy '{strategy_name}' not found in registry. "
                        f"Available: {available}. Using fallback EMA strategy."
                    )
                    # Fallback to config-only mode (will use simple EMA)
                    self.strategies[strategy_name] = {
                        "instance": None,
                        "config": strategy_config,
                        "parameters": strategy_config.parameters,
                        "timeframes": strategy_config.timeframes,
                    }

            except Exception as e:
                logger.error(f"Failed to initialize strategy {strategy_config.name}: {e}")

        # Setup regime mappings after all strategies are initialized
        self._setup_regime_mappings()

    def _setup_regime_mappings(self):
        """Configure which strategies to use for each market regime."""
        # Mean reversion for ranging/sideways markets (with movement)
        self.strategy_selector.map_strategy_to_regime(
            "mean_reversion", [MarketRegime.RANGING, MarketRegime.CONSOLIDATION]
        )

        # Mean reversion pure (classic, no momentum)
        self.strategy_selector.map_strategy_to_regime(
            "mean_reversion_pure", [MarketRegime.RANGING, MarketRegime.CONSOLIDATION]
        )

        # Mean reversion V2 (optimized: trend filter, momentum, R:R gate)
        self.strategy_selector.map_strategy_to_regime(
            "mean_reversion_v2", [MarketRegime.RANGING, MarketRegime.CONSOLIDATION]
        )

        # Low vol scalper for quiet, low-movement markets
        self.strategy_selector.map_strategy_to_regime(
            "low_vol_scalper", [MarketRegime.LOW_VOLATILITY]
        )

        # Mean reversion low vol for quiet, low-movement markets
        self.strategy_selector.map_strategy_to_regime(
            "mean_reversion_low_vol", [MarketRegime.LOW_VOLATILITY]
        )

        # BB Grid scalper for quiet, low-movement markets (grid approach)
        self.strategy_selector.map_strategy_to_regime(
            "bb_grid_scalper", [MarketRegime.LOW_VOLATILITY]
        )

        # Scalper strategy for consolidation and low volatility regimes
        self.strategy_selector.map_strategy_to_regime(
            "scalper", [MarketRegime.CONSOLIDATION, MarketRegime.LOW_VOLATILITY]
        )

        # Grid strategy for ranging and consolidation markets
        self.strategy_selector.map_strategy_to_regime(
            "grid", [MarketRegime.RANGING, MarketRegime.CONSOLIDATION]
        )

        # Trend following for trending/volatile markets
        self.strategy_selector.map_strategy_to_regime(
            "trend_following",
            [
                MarketRegime.TRENDING_UP,
                MarketRegime.TRENDING_DOWN,
                MarketRegime.HIGH_VOLATILITY,
                MarketRegime.BREAKOUT,
            ],
        )

        # Momentum/Breakout strategy for trending, breakout, and ranging conditions
        self.strategy_selector.map_strategy_to_regime(
            "momentum",
            [
                MarketRegime.TRENDING_UP,
                MarketRegime.TRENDING_DOWN,
                MarketRegime.BREAKOUT,
                MarketRegime.RANGING,
                MarketRegime.CONSOLIDATION,
                MarketRegime.LOW_VOLATILITY,
                MarketRegime.HIGH_VOLATILITY,
            ],
        )

        # Multi-indicator as fallback for unknown regimes
        self.strategy_selector.map_strategy_to_regime("multi_indicator", [MarketRegime.UNKNOWN])

        logger.info("Regime-strategy mappings configured (3-level system)")

        # Register strategy pairs from config
        for strategy_name, strategy_info in self.strategies.items():
            params = strategy_info.get("parameters", {})
            pairs = params.get("pairs", [])
            if pairs:
                self.strategy_selector.register_strategy_pairs(strategy_name, pairs)
                logger.debug(f"Registered {len(pairs)} pairs for {strategy_name}: {pairs}")

    def _build_candle_cache(self):
        """
        Build candle cache for all loaded data.

        This is separate from indicator calculation to ensure
        all symbol+timeframe combinations are cached.
        """
        from src.indicators.base_indicator import Candle as IndicatorCandle

        logger.info("Building candle cache for fast lookups...")

        for key, data in self.data.items():
            if key in self._candles_cache:
                continue

            # Vectorized column extraction from polars (avoids per-row access).
            # ms timestamps via dt.epoch are integer-exact for minute candles.
            ts_ms = data["timestamp"].dt.epoch(time_unit="ms").to_numpy()
            opens = data["open"].to_numpy()
            highs = data["high"].to_numpy()
            lows = data["low"].to_numpy()
            closes = data["close"].to_numpy()
            volumes = data["volume"].to_numpy()
            has_spread = "spread" in data.columns
            spreads = data["spread"].to_numpy() if has_spread else None

            candles = []
            timestamps = []
            for i in range(len(data)):
                tsi = int(ts_ms[i])
                sp = float(spreads[i]) / 100.0 if (has_spread and spreads[i] > 0) else 0.0
                candles.append(
                    IndicatorCandle(
                        timestamp=tsi,
                        open=float(opens[i]),
                        high=float(highs[i]),
                        low=float(lows[i]),
                        close=float(closes[i]),
                        volume=float(volumes[i]),
                        spread=sp,
                    )
                )
                timestamps.append(tsi)

            self._candles_cache[key] = candles
            self._candle_index_cache[key] = timestamps

        logger.info(f"Cached {len(self._candles_cache)} candle sets")

    def _precompute_all_indicators(self):
        """
        Pre-compute indicator values for every candle position in O(n) time.

        Instead of recalculating all indicators from scratch on each candle
        (O(n) per candle × n candles = O(n²)), we compute the full series
        once in a single O(n) pass and store values for lookup.

        When indicator_window > 0, pre-computation is skipped because indicators
        must be computed on a sliding window (like live trading with 500 candles).
        """
        self._indicator_series: Dict[str, Dict[str, list]] = {}

        if self.config.indicator_window > 0:
            logger.info(
                f"Indicator window mode: {self.config.indicator_window} candles "
                f"(matching live trading). Pre-computation skipped."
            )
            return

        from src.indicators.factory import parse_indicator_key

        total_computed = 0

        for strategy_name, strategy_data in self.strategies.items():
            strategy_instance = strategy_data.get("instance")
            if not strategy_instance:
                continue

            indicators_config = strategy_instance.get_indicators_config()
            timeframe = strategy_data["timeframes"][0]

            for symbol in self.config.symbols:
                cache_key = f"{symbol}_{timeframe}"
                series_key = f"{cache_key}_{strategy_name}"

                if cache_key not in self._candles_cache:
                    continue

                all_candles = self._candles_cache[cache_key]
                self._indicator_series[series_key] = {}

                for indicator_key in indicators_config:
                    indicator_type, params = parse_indicator_key(indicator_key)
                    series = self._compute_indicator_series(all_candles, indicator_type, params)
                    self._indicator_series[series_key][indicator_key] = series
                    total_computed += 1

        logger.info(
            f"Pre-computed {total_computed} indicator series "
            f"for {len(self._indicator_series)} strategy/symbol combos"
        )

    def _compute_indicator_series(self, candles: list, indicator_type: str, params: dict) -> list:
        """
        Compute indicator values at every candle position in a single O(n) pass.

        Returns a list where result[i] is the indicator values dict for candles[:i+1],
        or None if not enough data at that position.
        """
        if indicator_type == "ema":
            return self._series_ema(candles, params.get("period", 20))
        elif indicator_type == "atr":
            return self._series_atr(candles, params.get("period", 14))
        elif indicator_type == "bollinger":
            return self._series_bollinger(
                candles, params.get("period", 20), params.get("std_dev", 2.0)
            )
        elif indicator_type == "adx":
            return self._series_adx(candles, params.get("period", 14))
        elif indicator_type == "sma":
            return self._series_sma(candles, params.get("period", 20))
        elif indicator_type == "rsi":
            return self._series_rsi(candles, params.get("period", 14))
        elif indicator_type == "asgma":
            return self._series_asgma(candles, params)
        else:
            # Fallback: use standard indicator (still O(n²) but rare)
            return self._series_fallback(candles, indicator_type, params)

    def _series_ema(self, candles: list, period: int) -> list:
        """EMA series in O(n)."""
        n = len(candles)
        result = [None] * n

        if n < period:
            return result

        multiplier = 2 / (period + 1)
        closes = [c.close for c in candles]

        # Initial SMA as seed
        ema = sum(closes[:period]) / period
        result[period - 1] = {"value": round(ema, 8)}

        for i in range(period, n):
            ema = (closes[i] - ema) * multiplier + ema
            result[i] = {"value": round(ema, 8)}

        return result

    def _series_atr(self, candles: list, period: int) -> list:
        """ATR series in O(n)."""
        n = len(candles)
        result = [None] * n

        if n < period + 1:
            return result

        # Compute all true ranges
        true_ranges = []
        for i in range(1, n):
            tr = max(
                candles[i].high - candles[i].low,
                abs(candles[i].high - candles[i - 1].close),
                abs(candles[i].low - candles[i - 1].close),
            )
            true_ranges.append(tr)

        # First ATR = SMA of first `period` true ranges
        atr = sum(true_ranges[:period]) / period
        result[period] = {"value": round(atr, 8)}

        # Wilder's smoothing
        for j in range(period, len(true_ranges)):
            atr = (atr * (period - 1) + true_ranges[j]) / period
            result[j + 1] = {"value": round(atr, 8)}

        return result

    def _series_bollinger(self, candles: list, period: int, std_dev: float) -> list:
        """Bollinger Bands series in O(n * period)."""
        import math

        n = len(candles)
        result = [None] * n

        if n < period:
            return result

        closes = [c.close for c in candles]

        for i in range(period - 1, n):
            window = closes[i - period + 1 : i + 1]
            middle = sum(window) / period
            variance = sum((x - middle) ** 2 for x in window) / period
            std = math.sqrt(variance)

            upper = middle + (std * std_dev)
            lower = middle - (std * std_dev)
            width = upper - lower

            result[i] = {
                "upper": round(upper, 8),
                "middle": round(middle, 8),
                "lower": round(lower, 8),
                "width": round(width, 8),
            }

        return result

    def _series_adx(self, candles: list, period: int) -> list:
        """ADX series in O(n)."""
        n = len(candles)
        result = [None] * n
        min_candles = 2 * period + 1

        if n < min_candles:
            return result

        # Step 1: TR, +DM, -DM for each bar
        true_ranges = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, n):
            high = candles[i].high
            low = candles[i].low
            prev_high = candles[i - 1].high
            prev_low = candles[i - 1].low
            prev_close = candles[i - 1].close

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

            up_move = high - prev_high
            down_move = prev_low - low
            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        # Step 2: First smoothed values
        smoothed_tr = sum(true_ranges[:period])
        smoothed_plus_dm = sum(plus_dm_list[:period])
        smoothed_minus_dm = sum(minus_dm_list[:period])

        plus_di = 100.0 * smoothed_plus_dm / smoothed_tr if smoothed_tr > 0 else 0.0
        minus_di = 100.0 * smoothed_minus_dm / smoothed_tr if smoothed_tr > 0 else 0.0
        di_sum = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0

        dx_values = [dx]

        # Step 3: Wilder's smoothing, collect DX values
        for i in range(period, len(true_ranges)):
            smoothed_tr = smoothed_tr - (smoothed_tr / period) + true_ranges[i]
            smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
            smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

            plus_di = 100.0 * smoothed_plus_dm / smoothed_tr if smoothed_tr > 0 else 0.0
            minus_di = 100.0 * smoothed_minus_dm / smoothed_tr if smoothed_tr > 0 else 0.0
            di_sum = plus_di + minus_di
            dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
            dx_values.append(dx)

            # candle index for this DX: i+1 (since true_ranges[i] uses candle i+1)
            candle_idx = i + 1

            # Step 4: ADX available after `period` DX values are collected
            dx_count = len(dx_values)
            if dx_count == period:
                # First ADX = simple average
                adx = sum(dx_values) / period
                result[candle_idx] = {
                    "value": round(adx, 2),
                    "plus_di": round(plus_di, 2),
                    "minus_di": round(minus_di, 2),
                }
            elif dx_count > period:
                adx = (adx * (period - 1) + dx) / period
                result[candle_idx] = {
                    "value": round(adx, 2),
                    "plus_di": round(plus_di, 2),
                    "minus_di": round(minus_di, 2),
                }

        return result

    def _series_sma(self, candles: list, period: int) -> list:
        """SMA series in O(n) with sliding window."""
        n = len(candles)
        result = [None] * n

        if n < period:
            return result

        closes = [c.close for c in candles]
        window_sum = sum(closes[:period])
        result[period - 1] = {"value": round(window_sum / period, 8)}

        for i in range(period, n):
            window_sum += closes[i] - closes[i - period]
            result[i] = {"value": round(window_sum / period, 8)}

        return result

    def _series_rsi(self, candles: list, period: int) -> list:
        """RSI series in O(n) with Wilder's smoothing."""
        n = len(candles)
        result = [None] * n

        if n < period + 1:
            return result

        closes = [c.close for c in candles]

        # Calculate price changes
        gains = []
        losses = []
        for i in range(1, n):
            change = closes[i] - closes[i - 1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))

        # First average
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rs = avg_gain / avg_loss if avg_loss > 0 else float("inf")
        rsi = 100 - (100 / (1 + rs)) if avg_loss > 0 else 100.0
        result[period] = {"value": round(rsi, 2)}

        # Wilder's smoothing
        for j in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[j]) / period
            avg_loss = (avg_loss * (period - 1) + losses[j]) / period

            rs = avg_gain / avg_loss if avg_loss > 0 else float("inf")
            rsi = 100 - (100 / (1 + rs)) if avg_loss > 0 else 100.0
            result[j + 1] = {"value": round(rsi, 2)}

        return result

    def _series_asgma(self, candles: list, params: dict) -> list:
        """ASGMA series in O(n) — compute all values in single pass."""
        import numpy as np

        from src.indicators.momentum.asgma import _alma_np, _chande_np, _ema_np, _rsi_np

        smooth = params.get("smooth", 1)
        alma_length = params.get("alma_length", 25)
        gma_length = params.get("gma_length", 14)
        vol_period = params.get("vol_period", 20)

        n = len(candles)
        result = [None] * n
        min_needed = max(alma_length, gma_length, vol_period, 14, 9) + smooth + 10
        if n < min_needed:
            return result

        closes = np.array([c.close for c in candles], dtype=np.float64)

        # 1. Percentage change
        pchange = np.zeros(n)
        pchange[smooth:] = (
            (closes[smooth:] - closes[:-smooth])
            / np.where(closes[:-smooth] != 0, closes[:-smooth], 1.0)
            * 100
        )

        # 2. ALMA of pchange — full series
        avp = _alma_np(pchange, alma_length, 0.85, 7.0)
        avp = np.nan_to_num(avp, nan=0.0)

        # 3. RSI and Chande — full series
        rsi_arr = _rsi_np(closes, 14)
        cmo_arr = _chande_np(closes, 9)

        # 4. GMA series — rolling gaussian weighted (high+low)/2 of avpchange
        #    Then EMA(7) smoothing
        gma_raw = np.zeros(n)
        for i in range(min_needed, n):
            subset_c = closes[max(0, i - vol_period + 1) : i + 1]
            sigma = float(np.std(subset_c))
            if sigma < 0.01:
                sigma = 0.01

            g = 0.0
            sw = 0.0
            for j in range(gma_length):
                w = np.exp(-(((j - (gma_length - 1)) / (2 * sigma)) ** 2) / 2)
                lb = max(0, i - j)
                seg = avp[lb : i + 1]
                if len(seg) == 0:
                    continue
                g += (float(seg.max()) + float(seg.min())) * w
                sw += w
            gma_raw[i] = (g / sw / 2) if sw > 0 else 0.0

        gma_ema = _ema_np(gma_raw, 7)

        # 5. Build result series
        for i in range(min_needed, n):
            av = float(avp[i])
            av_prev = float(avp[i - 1])
            gm = float(gma_ema[i])
            gm_prev = float(gma_ema[i - 1])

            direction = 1.0 if av >= gm else -1.0
            bullish_cross = av_prev < gm_prev and av >= gm
            bearish_cross = av_prev > gm_prev and av <= gm
            signal = 1.0 if bullish_cross else (-1.0 if bearish_cross else 0.0)

            rsi_rising = rsi_arr[i] > rsi_arr[i - 1]
            rsi_falling = rsi_arr[i] < rsi_arr[i - 1]
            cmo_rising = cmo_arr[i] > cmo_arr[i - 1]
            cmo_falling = cmo_arr[i] < cmo_arr[i - 1]

            confirmed = 0.0
            if signal == 1.0 and rsi_rising and cmo_rising:
                confirmed = 1.0
            elif signal == -1.0 and rsi_falling and cmo_falling:
                confirmed = -1.0

            result[i] = {
                "avpchange": round(av, 6),
                "gma": round(gm, 6),
                "signal": signal,
                "confirmed": confirmed,
                "direction": direction,
                "rsi": round(float(rsi_arr[i]), 2),
                "cmo": round(float(cmo_arr[i]), 2),
            }

        return result

    def _series_fallback(self, candles: list, indicator_type: str, params: dict) -> list:
        """
        Fallback: compute indicator at each position using the standard
        indicator class. Still O(n²) but used only for uncommon indicators.
        """
        from src.indicators.factory import get_indicator_factory

        factory = get_indicator_factory()
        n = len(candles)
        result = [None] * n

        # Only compute every candle if dataset is small, otherwise sample
        for i in range(50, n):
            try:
                ind_result = factory.calculate(indicator_type, candles[: i + 1], **params)
                result[i] = dict(ind_result.values)
            except Exception:
                pass

        return result

    def _precalculate_indicators(self):
        """
        Build candle cache and pre-compute indicator values for all candle
        positions in O(n) time instead of O(n²) per-candle recalculation.
        """
        # Build candle cache for all data
        self._build_candle_cache()

        # Pre-compute indicator series for each strategy/symbol/timeframe
        self._precompute_all_indicators()

    async def _run_simulation(self):
        """Run the main simulation loop."""
        # Get unified timeline
        all_timestamps = self._get_unified_timeline()

        if not all_timestamps:
            logger.warning("No data available for simulation")
            return

        # Build signal timestamp set for multi-TF mode
        signal_timestamps = None
        if self.config.execution_timeframe:
            signal_timestamps = self._build_signal_timestamps()
            logger.info(
                f"Multi-TF: execution on {self.config.execution_timeframe}, "
                f"signals on {self.config.timeframes[0]}"
            )

        logger.info(f"Running simulation over {len(all_timestamps)} time points")

        for i, timestamp in enumerate(all_timestamps):
            # Update candle index for limit order tracking
            self._current_candle_idx = i
            self._current_ts = timestamp

            # Update current prices
            self._update_current_prices(timestamp)

            if not self.current_prices:
                continue

            # Execute signals queued for next-candle-open entry
            # In multi-TF mode: only execute at signal TF boundaries (next 15m open, not next 1m)
            if self.config.entry_on_next_open and self._pending_next_open_signals:
                if signal_timestamps is None or timestamp in signal_timestamps:
                    self._execute_pending_next_open_signals(timestamp)

            # Process pending limit orders (check for fills)
            if self.pending_orders_manager:
                for symbol in self.config.symbols:
                    if symbol in self.current_prices:
                        candle = {
                            "open": self.current_prices.get(symbol, 0),
                            "high": self.current_highs.get(symbol, 0),
                            "low": self.current_lows.get(symbol, 0),
                            "close": self.current_prices.get(symbol, 0),
                        }
                        self.pending_orders_manager.process_candle(
                            symbol=symbol,
                            candle=candle,
                            current_candle_idx=self._current_candle_idx,
                            timestamp=timestamp,
                        )

            # --- Fan batch monitoring (tick-level, matches live monitor) ---
            # In live, position monitor checks every 1-5s. Here we replay
            # ticks within the candle and close batches immediately, so
            # strategies see updated state before signal generation.
            if self._tick_data_loader:
                fan_closed = self.portfolio.check_fan_batches_tick_by_tick(
                    self.current_prices,
                    timestamp,
                    tick_data_loader=self._tick_data_loader,
                    candle_times=self.current_candle_times,
                )
                for trade in fan_closed:
                    if self._is_partial_exit(trade):
                        continue
                    self._notify_strategies_of_close(trade, timestamp)

            # --- SL/TP checks (skip fan batches — already handled above) ---
            closed_trades = self.portfolio.check_risk_levels(
                self.current_prices,
                timestamp,
                highs=self.current_highs,
                lows=self.current_lows,
                tick_resolver=self.tick_resolver,
                candle_times=self.current_candle_times if self.tick_resolver else None,
                tick_data_loader=self._tick_data_loader,
                skip_fan_batches=bool(self._tick_data_loader),
                bar_minutes=self._price_tf_minutes,
                short_trigger_spread=self.config.fixed_spread,
            )

            for trade in closed_trades:
                if self._is_partial_exit(trade):
                    continue
                self._notify_strategies_of_close(trade, timestamp)

            # Daily profit target check
            if self._daily_profit_target_pct is not None:
                day_key = timestamp.strftime("%Y-%m-%d")
                if day_key != self._current_day:
                    self._current_day = day_key
                    self._day_start_balance = float(self.portfolio.current_balance)
                    self._daily_target_hit = False

                if not self._daily_target_hit and self._day_start_balance > 0:
                    current_balance = float(self.portfolio.current_balance)
                    daily_pnl_pct = (
                        (current_balance - self._day_start_balance) / self._day_start_balance * 100
                    )
                    if daily_pnl_pct >= self._daily_profit_target_pct:
                        self._daily_target_hit = True

                        closed_count = 0
                        kept_count = 0

                        if self._daily_target_mode in ("close_all", "close_profitable"):
                            for pos_id in list(self.portfolio.positions.keys()):
                                pos = self.portfolio.positions[pos_id]
                                exit_price = self.current_prices.get(pos.symbol, 0)
                                if exit_price <= 0:
                                    continue
                                if self._daily_target_mode == "close_profitable":
                                    if pos.side == "LONG":
                                        unrealized = exit_price - pos.entry_price
                                    else:
                                        unrealized = pos.entry_price - exit_price
                                    if unrealized <= 0:
                                        kept_count += 1
                                        continue
                                result = self.portfolio.close_position(
                                    pos_id, exit_price, timestamp, exit_reason="daily_target"
                                )
                                if result:
                                    closed_count += 1

                        mode_msg = {
                            "stop_new": "stopping new trades",
                            "close_all": f"closed {closed_count} positions",
                            "close_profitable": f"closed {closed_count} profitable, kept {kept_count} losing",
                        }.get(self._daily_target_mode, "stopping new trades")

                        logger.info(
                            f"Daily profit target +{daily_pnl_pct:.1f}% reached "
                            f"(target {self._daily_profit_target_pct}%) on {day_key} "
                            f"- {mode_msg}"
                        )

            # Generate signals from strategies (only at signal TF boundaries in multi-TF mode)
            if signal_timestamps is None or timestamp in signal_timestamps:
                if not self._daily_target_hit:
                    signals = await self._generate_signals(timestamp)

                    # Process signals
                    for signal in signals:
                        await self._process_signal(signal, timestamp)

            # Update equity curve
            self.portfolio.update_equity(timestamp, self.current_prices)

            # Progress logging
            if i > 0 and i % 1000 == 0:
                progress = (i / len(all_timestamps)) * 100
                logger.info(f"Simulation progress: {progress:.1f}%")

        # Log tick resolver stats
        if self.tick_resolver is not None:
            stats = self.tick_resolver.stats
            logger.info(
                f"Tick resolver: {stats['resolved']} ambiguous candles resolved, "
                f"{stats['fallback']} fell back to candle-based"
            )

        # Don't close remaining positions - they are excluded from results
        # This gives more honest results based only on completed trades
        open_positions_count = len(self.portfolio.positions)
        if open_positions_count > 0:
            logger.info(
                f"Backtest ended with {open_positions_count} open position(s) - excluded from results"
            )

        # Regime gate stats
        if self._regime_gate_blocks:
            total = sum(self._regime_gate_blocks.values())
            logger.info(f"Regime gate: {total} signals blocked total")
            for strat, n in sorted(self._regime_gate_blocks.items(), key=lambda x: -x[1]):
                logger.info(f"  {strat}: {n} blocked")

    def _get_unified_timeline(self) -> List[datetime]:
        """Get sorted list of all unique timestamps across all data."""
        all_timestamps = set()

        for key, data in self.data.items():
            all_timestamps.update(data["timestamp"].to_list())

        return sorted(all_timestamps)

    def _build_signal_timestamps(self) -> set:
        """Build set of timestamps where signal generation should occur (signal TF boundaries)."""
        signal_tf = self.config.timeframes[0]
        signal_timestamps = set()

        for symbol in self.config.symbols:
            key = f"{symbol}_{signal_tf}"
            if key in self.data:
                signal_timestamps.update(self.data[key]["timestamp"].to_list())

        logger.info(f"Multi-TF: {len(signal_timestamps)} signal timestamps from {signal_tf}")
        return signal_timestamps

    def _is_partial_exit(self, trade) -> bool:
        """TP1 exits are partial only while the position keeps running. When a
        single-TP position closes 100% at TP1 the exit_reason is still
        'take_profit_1' but nothing remains — that close MUST be notified,
        otherwise strategies tracking their positions (pyramid baskets,
        index_trend _position, daily-PnL) never learn the trade ended."""
        if trade.exit_reason != "take_profit_1":
            return False
        return any(
            p.symbol == trade.symbol and p.strategy_name == trade.strategy_name
            for p in self.portfolio.positions.values()
        )

    def _notify_strategies_of_close(self, trade, timestamp: datetime):
        """Notify all strategies about a closed trade for risk management."""
        for strategy_name, strategy_data in self.strategies.items():
            strategy_instance = strategy_data.get("instance")
            if not strategy_instance:
                continue

            if hasattr(strategy_instance, "notify_trade_closed"):
                metadata = dict(trade.metadata) if trade.metadata else {}
                metadata["account_balance"] = float(self.portfolio.current_balance)
                strategy_instance.notify_trade_closed(
                    trade.symbol,
                    trade.pnl_pct,
                    exit_reason=trade.exit_reason,
                    metadata=metadata,
                    net_pnl=trade.net_pnl,
                    exit_time=trade.exit_time,
                )
            elif trade.exit_reason == "stop_loss" and hasattr(strategy_instance, "register_loss"):
                candle_idx = self._get_candle_index(trade.symbol, timestamp)
                strategy_instance.register_loss(trade.symbol, candle_idx)

    def _get_candle_index(self, symbol: str, timestamp: datetime) -> int:
        """Get candle index for a symbol at a timestamp."""
        primary_tf = self.config.timeframes[0]
        key = f"{symbol}_{primary_tf}"
        ts_ms = int(
            timestamp.replace(tzinfo=timezone.utc).timestamp() * 1000
        )  # treat naive timeline as UTC (pandas parity)

        if key in self._candle_index_cache:
            timestamps = self._candle_index_cache[key]
            idx = bisect.bisect_right(timestamps, ts_ms)
            return idx  # Current candle count
        return 0

    def _get_half_spread(self, symbol: str, price: float) -> float:
        """Get half-spread for a symbol from candle data or config.

        Candle 'spread' column is average spread in points (100ths of price unit).
        E.g., spread=20 means 0.20 price units on XAUUSD.
        """
        # Try to get spread from current candle data
        price_tf = self.config.execution_timeframe or self.config.timeframes[0]
        key = f"{symbol}_{price_tf}"
        if key in self.data:
            if hasattr(self, "_current_candle_idx") and key in self._candles_cache:
                candles = self._candles_cache[key]
                idx = min(self._current_candle_idx, len(candles) - 1)
                if idx >= 0 and hasattr(candles[idx], "spread"):
                    spread_points = candles[idx].spread
                    if spread_points and spread_points > 0:
                        return spread_points / 100 / 2  # points → price units → half

        # Fallback: use configured fixed spread
        if self.config.fixed_spread > 0:
            return self.config.fixed_spread / 2
        return price * 0.0001  # 0.01% fallback

    def _update_current_prices(self, timestamp: datetime):
        """Update current prices (close, high, low, open) for all symbols at timestamp."""
        self.current_prices.clear()
        self.current_highs.clear()
        self.current_lows.clear()
        self.current_opens.clear()
        ts_ms = int(
            timestamp.replace(tzinfo=timezone.utc).timestamp() * 1000
        )  # treat naive timeline as UTC (pandas parity)

        for symbol in self.config.symbols:
            price_tf = self.config.execution_timeframe or self.config.timeframes[0]
            key = f"{symbol}_{price_tf}"

            # Fast path: use cached candles with binary search
            if key in self._candles_cache and key in self._candle_index_cache:
                timestamps = self._candle_index_cache[key]
                candles = self._candles_cache[key]
                idx = bisect.bisect_right(timestamps, ts_ms)
                if idx > 0:
                    candle = candles[idx - 1]
                    self.current_prices[symbol] = candle.close
                    self.current_highs[symbol] = candle.high
                    self.current_lows[symbol] = candle.low
                    self.current_opens[symbol] = candle.open
            elif key in self.data:
                # Fallback: polars as-of lookup (the cache path covers the hot loop)
                data = self.data[key]
                sub = data.filter(pl.col("timestamp") <= timestamp)
                if sub.height > 0:
                    row = sub.row(-1, named=True)
                    self.current_prices[symbol] = float(row["close"])
                    self.current_highs[symbol] = float(row["high"])
                    self.current_lows[symbol] = float(row["low"])
                    self.current_opens[symbol] = float(row["open"])

        # Compute candle time boundaries for tick resolver
        if self.tick_resolver is not None:
            self.current_candle_times.clear()
            from src.backtesting.data.data_loader import TIMEFRAME_MINUTES

            price_tf = self.config.execution_timeframe or self.config.timeframes[0]
            tf_minutes = TIMEFRAME_MINUTES.get(price_tf, 1)
            tf_ms = tf_minutes * 60 * 1000
            for symbol in self.config.symbols:
                self.current_candle_times[symbol] = (ts_ms, ts_ms + tf_ms)

    async def _generate_signals(self, timestamp: datetime) -> List[Dict[str, Any]]:
        """
        Generate trading signals from strategies based on market regime.

        Uses per-symbol regime detection to select appropriate strategies.
        """
        signals = []

        for symbol in self.config.symbols:
            # Only the regime-switching path needs the full candle history (an
            # O(n) copy); the disabled path just needs the count → O(log n).
            # Avoids an O(n^2) prefix-copy every candle when regime is off.
            if self.regime_switching_enabled:
                candles = self._get_candles_for_regime_detection(symbol, timestamp)
                if len(candles) < 50:
                    continue
                market_condition = self.strategy_selector.detect_market_regime(candles)
                selected_strategies = self.strategy_selector.select_strategies(
                    market_condition, list(self.strategies.keys()), symbol=symbol
                )
                regime_value = market_condition.regime.value
            else:
                if self._count_candles_up_to(symbol, timestamp) < 50:
                    continue
                selected_strategies = list(self.strategies.keys())
                regime_value = "disabled"

            # Run only selected strategies for this symbol
            for strategy_name in selected_strategies:
                strategy_info = self.strategies.get(strategy_name)
                if not strategy_info:
                    continue

                strategy_instance = strategy_info.get("instance")
                timeframe = strategy_info["timeframes"][0]
                key = f"{symbol}_{timeframe}"

                if key not in self.data:
                    continue

                # Use real strategy if available (fast cached path)
                if strategy_instance is not None:
                    signal = await self._generate_signal_from_strategy(
                        strategy_instance, symbol, timestamp, None, timeframe
                    )
                else:
                    # Fallback to simple EMA strategy (needs pandas DataFrame)
                    data = self.data[key].to_pandas().set_index("timestamp")
                    historical = data[data.index <= timestamp]

                    if len(historical) < 50:
                        continue

                    signal = self._generate_simple_signal(
                        symbol, timestamp, historical, strategy_name
                    )

                if signal:
                    # Handle single signal or list of signals (hedge mode)
                    if isinstance(signal, list):
                        for s in signal:
                            if self._is_blocked_by_regime_gate(strategy_name, symbol, timestamp):
                                continue
                            if self._is_blocked_by_direction_filter(
                                strategy_name, symbol, timestamp
                            ):
                                continue
                            if self._is_blocked_by_volatility_filter(
                                strategy_name, symbol, timestamp
                            ):
                                continue
                            s["regime"] = regime_value
                            signals.append(s)
                    else:
                        if self._is_blocked_by_regime_gate(strategy_name, symbol, timestamp):
                            continue
                        if self._is_blocked_by_direction_filter(strategy_name, symbol, timestamp):
                            continue
                        if self._is_blocked_by_volatility_filter(strategy_name, symbol, timestamp):
                            continue
                        signal["regime"] = regime_value
                        signals.append(signal)

        return signals

    def _get_candles_for_regime_detection(self, symbol: str, timestamp: datetime) -> list:
        """Get candles for market regime detection."""
        # Try 15m first, then fallback to other available timeframes
        for tf in ["15m", "5m", "1h", "30m", "3m", "2m", "1m"]:
            key = f"{symbol}_{tf}"
            if key in self._candles_cache and key in self._candle_index_cache:
                break
        else:
            return []

        ts_ms = int(
            timestamp.replace(tzinfo=timezone.utc).timestamp() * 1000
        )  # treat naive timeline as UTC (pandas parity)
        all_candles = self._candles_cache[key]
        timestamps = self._candle_index_cache[key]

        # Binary search for candles up to timestamp
        idx = bisect.bisect_right(timestamps, ts_ms)
        return all_candles[:idx]

    def _count_candles_up_to(self, symbol: str, timestamp: datetime) -> int:
        """Cheap O(log n) count of candles up to timestamp (no list copy)."""
        for tf in ["15m", "5m", "1h", "30m", "3m", "2m", "1m"]:
            key = f"{symbol}_{tf}"
            if key in self._candle_index_cache:
                ts_ms = int(timestamp.replace(tzinfo=timezone.utc).timestamp() * 1000)
                return bisect.bisect_right(self._candle_index_cache[key], ts_ms)
        return 0

    async def _generate_signal_from_strategy(
        self,
        strategy,
        symbol: str,
        timestamp: datetime,
        data: Optional[pd.DataFrame],
        timeframe: str = "15m",
    ) -> Optional[Dict[str, Any]]:
        """
        Generate signal using actual strategy instance.

        Uses pre-computed indicator series for O(1) lookup per candle.
        """
        from src.indicators.base_indicator import IndicatorResult

        try:
            cache_key = f"{symbol}_{timeframe}"
            ts_ms = int(
                timestamp.replace(tzinfo=timezone.utc).timestamp() * 1000
            )  # treat naive timeline as UTC (pandas parity)

            # Fast path: use cached candles with binary search (O(log n))
            if cache_key in self._candles_cache and cache_key in self._candle_index_cache:
                all_candles = self._candles_cache[cache_key]
                timestamps = self._candle_index_cache[cache_key]
                idx = bisect.bisect_right(timestamps, ts_ms)
                # Bounded tail instead of the whole growing prefix (kills O(n^2)).
                # candle_idx below stays GLOBAL (idx-1), so precomputed-indicator
                # lookup is unaffected; candles[-1] is still the current bar.
                start = max(0, idx - self._signal_history_window)
                candles = all_candles[start:idx]
            elif data is not None:
                # Fallback: convert from DataFrame (slow path)
                from src.indicators.base_indicator import Candle as IndicatorCandle

                candles = []
                for row_idx, row in data.iterrows():
                    candle = IndicatorCandle(
                        timestamp=int(row_idx.timestamp() * 1000),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                    candles.append(candle)
            else:
                # No cache and no data - skip
                return None

            if len(candles) < 50:
                return None

            # Get indicators config from strategy
            indicators_config = strategy.get_indicators_config()

            # Windowed mode: compute indicators on last N candles (like live trading)
            series_key = f"{cache_key}_{strategy.name}"
            if self.config.indicator_window > 0:
                from src.indicators.factory import get_indicator_factory

                factory = get_indicator_factory()
                window_size = self.config.indicator_window
                indicator_candles = (
                    candles[-window_size:] if len(candles) > window_size else candles
                )
                indicators = factory.calculate_multiple(indicator_candles, indicators_config)
                if not indicators:
                    return None
            # Use pre-computed indicator series (O(1) lookup vs O(n) recalculation)
            elif series_key in self._indicator_series:
                candle_idx = idx - 1  # idx is bisect result, candle_idx is 0-based
                indicators = {}
                for ind_key in indicators_config:
                    series = self._indicator_series[series_key].get(ind_key)
                    if series and candle_idx < len(series) and series[candle_idx] is not None:
                        indicators[ind_key] = IndicatorResult(
                            name=ind_key,
                            timestamp=candles[-1].timestamp,
                            values=series[candle_idx],
                            signals=[],
                            metadata={},
                        )
                if not indicators:
                    return None
            else:
                # Fallback: calculate on the fly (for strategies without pre-computation)
                from src.indicators.factory import get_indicator_factory

                factory = get_indicator_factory()
                indicators = factory.calculate_multiple(candles, indicators_config)

            # Call strategy analyze
            result = await strategy.analyze(symbol, candles, indicators)

            if result is None:
                return None

            # Handle List[Signal] (hedge mode) or single Signal
            # Per-strategy position checks (strategies don't block each other)
            if isinstance(result, list):
                max_positions = getattr(strategy, "max_positions", 5)

                if self.config.live_hedge_semantics:
                    # Live rule (signal_lifecycle_manager under hedge_mode): only a
                    # SAME-side own position blocks a new entry, so one strategy can
                    # hold a long and a short on the same symbol simultaneously.
                    allowed = []
                    for sig in result:
                        if not sig:
                            continue
                        want = _norm_side(getattr(sig, "side", ""))
                        same_side = sum(
                            1
                            for p in self.portfolio.positions.values()
                            if p.symbol == symbol
                            and p.strategy_name == strategy.name
                            and _norm_side(p.side) == want
                        )
                        same_side += sum(
                            1 for a in allowed if _norm_side(getattr(a, "side", "")) == want
                        )
                        if same_side + 1 > max_positions:
                            continue
                        allowed.append(sig)
                    if not allowed:
                        return None
                    return [self._signal_to_dict(sig) for sig in allowed]

                # Backtest rule: any own position on the symbol counts, regardless
                # of side, so max_positions caps both directions together.
                symbol_positions = sum(
                    1
                    for p in self.portfolio.positions.values()
                    if p.symbol == symbol and p.strategy_name == strategy.name
                )

                if symbol_positions + len(result) > max_positions:
                    return None

                return [self._signal_to_dict(sig) for sig in result if sig]
            else:
                # Single signal mode: block only on this strategy's own position.
                # Under live hedge semantics only a SAME-side position blocks.
                if self.config.live_hedge_semantics:
                    want = _norm_side(getattr(result, "side", ""))
                    has_position = any(
                        p.symbol == symbol
                        and p.strategy_name == strategy.name
                        and _norm_side(p.side) == want
                        for p in self.portfolio.positions.values()
                    )
                else:
                    has_position = any(
                        p.symbol == symbol and p.strategy_name == strategy.name
                        for p in self.portfolio.positions.values()
                    )
                if has_position:
                    return None
                return self._signal_to_dict(result)

        except Exception as e:
            logger.debug(f"Strategy {strategy.name} error for {symbol}: {e}")
            return None

    def _signal_to_dict(self, signal) -> Dict[str, Any]:
        """Convert a Signal object to dict for backtest engine."""
        tp_distribution = None
        if signal.metadata:
            if "partial_exits" in signal.metadata:
                partial = signal.metadata["partial_exits"]
                tp_distribution = [
                    partial.get("tp1_percent", 60) / 100,
                    partial.get("tp2_percent", 30) / 100,
                    partial.get("tp3_percent", 10) / 100,
                ]
            elif "tp_distribution" in signal.metadata:
                tp_distribution = signal.metadata["tp_distribution"]

        result = {
            "symbol": signal.symbol,
            "side": "LONG" if signal.side == "BUY" else "SHORT",
            "price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit[0] if signal.take_profit else None,
            "take_profits": signal.take_profit if isinstance(signal.take_profit, list) else None,
            "tp_distribution": tp_distribution,
            "strategy_name": signal.strategy_name,
            "confidence": signal.confidence,
            "metadata": signal.metadata or {},  # Pass full metadata for hedge tracking
        }

        # Pass strategy-level position sizing if provided
        if signal.position_size_percent and signal.position_size_percent > 0:
            result["position_size_percent"] = signal.position_size_percent

        return result

    def _generate_simple_signal(
        self, symbol: str, timestamp: datetime, data: pd.DataFrame, strategy_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Generate a simple signal based on moving averages.

        This is a placeholder - replace with actual strategy logic.
        """
        if len(data) < 50:
            return None

        # Calculate EMAs
        close = data["close"]
        ema_fast = close.ewm(span=20, adjust=False).mean()
        ema_slow = close.ewm(span=50, adjust=False).mean()

        # Get current and previous values
        curr_fast = ema_fast.iloc[-1]
        curr_slow = ema_slow.iloc[-1]
        prev_fast = ema_fast.iloc[-2]
        prev_slow = ema_slow.iloc[-2]

        # Check for existing position
        has_position = any(p.symbol == symbol for p in self.portfolio.positions.values())

        # Generate signals based on EMA crossover
        signal = None

        if not has_position:
            # Bullish crossover
            if prev_fast <= prev_slow and curr_fast > curr_slow:
                current_price = float(close.iloc[-1])
                atr = self._calculate_atr(data)

                signal = {
                    "symbol": symbol,
                    "side": "LONG",
                    "price": current_price,
                    "stop_loss": current_price - (atr * 2),
                    "take_profit": current_price + (atr * 3),
                    "strategy_name": strategy_name,
                    "confidence": 0.7,
                }

            # Bearish crossover
            elif prev_fast >= prev_slow and curr_fast < curr_slow:
                current_price = float(close.iloc[-1])
                atr = self._calculate_atr(data)

                signal = {
                    "symbol": symbol,
                    "side": "SHORT",
                    "price": current_price,
                    "stop_loss": current_price + (atr * 2),
                    "take_profit": current_price - (atr * 3),
                    "strategy_name": strategy_name,
                    "confidence": 0.7,
                }

        return signal

    def _calculate_atr(self, data: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range."""
        high = data["high"]
        low = data["low"]
        close = data["close"]

        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean().iloc[-1]

        return float(atr)

    async def _process_signal(self, signal: Dict[str, Any], timestamp: datetime):
        """Process a trading signal."""
        symbol = signal["symbol"]
        side = signal["side"]

        # Calculate position size
        position_size = self._calculate_position_size(signal)

        if position_size <= 0:
            return

        # Add quantity to signal for pending orders
        signal["quantity"] = position_size

        # Per-signal override: use limit entry only when strategy explicitly opts in
        # or when global limit_entry is enabled.
        signal_meta = signal.get("metadata", {}) or {}
        per_signal_limit = bool(signal_meta.get("use_limit_entry", False))
        use_limit = per_signal_limit or self._limit_entry_global_enabled

        # Queue for next candle's open (realistic entry) — only when no limit path
        if self.config.entry_on_next_open and not use_limit:
            signal["_signal_timestamp"] = timestamp
            self._pending_next_open_signals.append(signal)
            return

        if use_limit and self.pending_orders_manager:
            signal_id = f"{symbol}_{side}_{self._current_candle_idx}"
            self.pending_orders_manager.create_pending_order(
                signal=signal,
                signal_id=signal_id,
                current_candle_idx=self._current_candle_idx,
                timestamp=timestamp,
            )
            logger.debug(f"Created pending limit order for {symbol} {side}")
            return

        # Fallback: immediate market order (original logic)
        self._open_position_from_signal(signal, timestamp)

    def _execute_pending_next_open_signals(self, timestamp: datetime):
        """
        Execute signals queued from the previous candle at current candle's open price.

        This simulates realistic entry: signal generated at candle N close,
        position opened at candle N+1 open.
        """
        if not self._pending_next_open_signals:
            return

        signals_to_process = self._pending_next_open_signals.copy()
        self._pending_next_open_signals.clear()

        for signal in signals_to_process:
            symbol = signal["symbol"]
            open_price = self.current_opens.get(symbol)

            if open_price is None or open_price <= 0:
                logger.debug(f"No open price for {symbol}, skipping queued signal")
                continue

            # Recalculate SL/TP from open price (preserve % distances from signal)
            adjusted_signal = self._recalculate_sl_tp_for_open_entry(signal, open_price)

            # Recalculate position size at the new price
            adjusted_signal["price"] = open_price
            adjusted_signal["quantity"] = self._calculate_position_size(adjusted_signal)

            if adjusted_signal["quantity"] <= 0:
                continue

            self._open_position_from_signal(adjusted_signal, timestamp, fill_price=open_price)

    def _recalculate_sl_tp_for_open_entry(
        self, signal: Dict[str, Any], open_price: float
    ) -> Dict[str, Any]:
        """
        Adjust TP levels for open-price entry, keeping SL at structural level.

        SL stays at the absolute price (structural level behind local extremum).
        TP is recalculated from new entry price preserving % distance.

        If the open price makes the trade invalid (already past SL), signal
        is skipped by returning quantity=0.
        """
        adjusted = signal.copy()
        signal_price = signal["price"]
        side = signal["side"]

        if signal_price <= 0:
            return adjusted

        # SL stays at structural level (absolute price, not recalculated)
        # But validate that open price hasn't already breached SL
        if signal.get("stop_loss"):
            sl = signal["stop_loss"]
            if side == "LONG" and open_price <= sl:
                adjusted["quantity"] = 0  # Signal invalid at this open price
                return adjusted
            elif side == "SHORT" and open_price >= sl:
                adjusted["quantity"] = 0
                return adjusted

        # Recalculate single take profit (preserve % from entry)
        if signal.get("take_profit"):
            tp_pct = abs(signal["take_profit"] - signal_price) / signal_price
            if side == "LONG":
                adjusted["take_profit"] = open_price * (1 + tp_pct)
            else:
                adjusted["take_profit"] = open_price * (1 - tp_pct)

        # Recalculate multiple take profits (preserve % from entry)
        if signal.get("take_profits"):
            new_tps = []
            for tp in signal["take_profits"]:
                tp_pct = abs(tp - signal_price) / signal_price
                if side == "LONG":
                    new_tps.append(open_price * (1 + tp_pct))
                else:
                    new_tps.append(open_price * (1 - tp_pct))
            adjusted["take_profits"] = new_tps

        return adjusted

    def _open_position_from_signal(
        self,
        signal: Dict[str, Any],
        timestamp: datetime,
        fill_price: Optional[float] = None,
        fee_type: str = "taker",
    ):
        """Open position from signal (called directly or from pending order fill)."""
        symbol = signal["symbol"]
        side = signal["side"]
        position_size = signal.get("quantity", self._calculate_position_size(signal))
        price = fill_price or signal["price"]

        # Create market state for slippage calculation
        half_spread = self._get_half_spread(symbol, price)
        market_state = MarketState(
            symbol=symbol,
            mid_price=price,
            bid_price=price - half_spread,
            ask_price=price + half_spread,
            spread=half_spread * 2,
            volume_24h=1000000,
            volatility=0.02,
        )

        # Build metadata - merge signal's metadata with regime info
        metadata = signal.get("metadata", {}).copy()
        metadata["regime"] = signal.get("regime", "unknown")
        metadata["fee_type"] = fee_type  # Track if maker or taker fee

        # Get contract size for this instrument
        contract_size = trading_pairs.get_contract_size(symbol)

        # Open position
        position = self.portfolio.open_position(
            symbol=symbol,
            side=side,
            quantity=position_size,
            price=price,
            timestamp=timestamp,
            stop_loss=signal.get("stop_loss"),
            take_profit=signal.get("take_profit"),
            take_profits=signal.get("take_profits"),
            tp_distribution=signal.get("tp_distribution"),
            strategy_name=signal.get("strategy_name", ""),
            market_state=market_state,
            metadata=metadata,
            contract_size=contract_size,
            fee_type=fee_type,
        )

        if position:
            self.signals.append(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "position_id": position.id,
                    "strategy_name": signal.get("strategy_name"),
                    "fee_type": fee_type,
                }
            )

    def _on_pending_order_filled(self, pending_order: PendingOrder):
        """Callback when a pending limit order is filled."""
        signal = pending_order.signal_data
        # V1 (relative SL/TP): recompute stop/target from the ACTUAL fill price
        # instead of the signal's absolute levels. Keeps risk distance constant
        # (no sizing inflation), unlike V2's absolute-from-signal levels.
        meta = signal.get("metadata", {}) if signal else {}
        if meta.get("limit_recompute_sltp"):
            fp = pending_order.fill_price
            slp = meta.get("sl_points")
            tpp = meta.get("tp_points")
            if slp and tpp and fp:
                signal = signal.copy()
                signal["stop_loss"] = fp - float(slp)
                signal["take_profit"] = [fp + float(tpp)]
        self._open_position_from_signal(
            signal=signal,
            timestamp=pending_order.filled_at,
            fill_price=pending_order.fill_price,
            fee_type="maker",  # Limit order = maker fee
        )
        logger.debug(
            f"Limit order filled: {pending_order.side} {pending_order.symbol} "
            f"@ {pending_order.fill_price:.4f}"
        )

    def _on_pending_order_timeout(self, pending_order: PendingOrder):
        """Callback when a pending limit order times out."""
        signal = pending_order.signal_data
        self._open_position_from_signal(
            signal=signal,
            timestamp=pending_order.filled_at,
            fill_price=pending_order.fill_price,
            fee_type="taker",  # Market fallback = taker fee
        )
        logger.debug(
            f"Limit order timeout, market fill: {pending_order.side} {pending_order.symbol} "
            f"@ {pending_order.fill_price:.4f}"
        )

    def _calculate_position_size(self, signal: Dict[str, Any]) -> float:
        """Calculate position size based on risk parameters."""
        metadata = signal.get("metadata", {})

        # Risk priority: CLI --risk (if explicitly passed) > per-strategy > signal-level > global default
        global_risk = self.config.risk_config.max_risk_per_trade
        strategy_risk = metadata.get("risk_per_trade")
        signal_risk = signal.get("position_size_percent")

        if self.config.risk_config.risk_override:
            # CLI --risk was explicitly passed, it overrides everything
            risk_per_trade = global_risk
        elif strategy_risk and strategy_risk > 0:
            risk_per_trade = float(strategy_risk)
        elif signal_risk and signal_risk > 0:
            risk_per_trade = signal_risk / 100.0  # Convert from percent to fraction
        else:
            risk_per_trade = global_risk
        max_position_pct = self.config.risk_config.max_position_size

        balance = float(self.portfolio.current_balance)

        # Balance cap: limit effective balance for position sizing.
        # Two modes (chosen by magnitude of `cap`):
        #   cap >= 100 → DOLLAR mode (legacy): hard cap, balance = min(balance, cap)
        #   0 < cap < 100 → PERCENT mode (dynamic): balance = current * (cap / 100)
        #     → sizing always uses cap% of current balance (compound moves up/down).
        #   cap == 0 → disabled.
        cap = self.config.risk_config.balance_cap
        if cap > 0:
            if cap < 100:
                balance = balance * (cap / 100.0)
            elif balance > cap:
                balance = cap

        # Dynamic risk: reduce risk during drawdowns
        if self.config.risk_config.dynamic_risk and self.portfolio.peak_equity > 0:
            dd_pct = (self.portfolio.peak_equity - balance) / self.portfolio.peak_equity
            if dd_pct > 0.25:
                risk_per_trade *= 0.25
                logger.debug(
                    f"Dynamic risk: DD {dd_pct:.1%} > 25%, risk reduced to {risk_per_trade:.4f}"
                )
            elif dd_pct > 0.10:
                risk_per_trade *= 0.50
                logger.debug(
                    f"Dynamic risk: DD {dd_pct:.1%} > 10%, risk reduced to {risk_per_trade:.4f}"
                )

        symbol = signal.get("symbol", "")

        # Get instrument config (contract_size, quantity_step, min/max)
        contract_size = trading_pairs.get_contract_size(symbol)

        # Validate price to avoid division by zero
        price = signal.get("price")
        if not price or price <= 0:
            logger.warning(f"Invalid price in signal: {price}, skipping position sizing")
            return 0.0

        # USDxxx (USDJPY+): PnL and notional are in the quote currency -> USD (x 1/price); else 1.0
        q2u = trading_pairs.quote_to_usd(symbol, price)

        stop_loss = signal.get("stop_loss")

        # Per-strategy sizing method override from metadata, then global config
        strategy_sizing = metadata.get("position_sizing_method")
        sizing_method = (
            strategy_sizing if strategy_sizing else self.config.risk_config.position_sizing_method
        )

        if sizing_method == "balance_ratio":
            # Simple linear scaling: $100 = 0.01 lot, $1000 = 0.10, $10000 = 1.0
            size = balance / 10000.0
        elif stop_loss:
            price_risk = abs(price - stop_loss)

            if sizing_method == "fixed_balance" and price_risk > 0:
                if contract_size > 1:
                    # MT5 instruments: leverage scales position size
                    # size = balance * max_position_pct / margin_per_lot
                    leverage = self.config.risk_config.leverage
                    size = (balance * max_position_pct * leverage) / (price * contract_size * q2u)
                else:
                    # Crypto: notional = balance (leverage only affects margin)
                    size = balance / (price * contract_size * q2u)
            elif price_risk > 0:
                # Risk-based sizing: risk_amount / (sl_distance * contract_size)
                # PnL per lot = price_risk * contract_size, so lots = risk / (price_risk * cs)
                risk_amount = balance * risk_per_trade
                size = risk_amount / (price_risk * contract_size * q2u)
            else:
                size = (balance * max_position_pct) / (price * contract_size * q2u)
        else:
            # Fixed percentage sizing
            size = (balance * max_position_pct) / (price * contract_size * q2u)

        # Limit to max position size (notional, accounting for leverage)
        leverage = self.config.risk_config.leverage
        max_notional = balance * max_position_pct * leverage
        max_size = max_notional / (price * contract_size * q2u)
        size = min(size, max_size)

        # Volatility tilt: scale by trailing daily range, then re-clamp to margin limit
        tilt = self._vol_tilt_multiplier()
        if tilt != 1.0:
            size = min(size * tilt, max_size)

        # Apply size modifier if present (for hedge or DCA initial positions)
        metadata = signal.get("metadata", {})

        # Check for hedge size modifier (LONG+SHORT hedge mode)
        hedge_size_pct = metadata.get("hedge_size_pct")
        if hedge_size_pct is not None and hedge_size_pct < 1.0:
            size = size * hedge_size_pct

        # Check for DCA size modifier (pure DCA mode)
        dca_size_pct = metadata.get("dca_size_pct")
        if dca_size_pct is not None and dca_size_pct < 1.0:
            size = size * dca_size_pct

        # Check for initial position size modifier (scaled entry for DCA)
        initial_position_size = metadata.get("initial_position_size")
        if initial_position_size is not None and initial_position_size < 1.0:
            size = size * initial_position_size

        # Round to quantity step and clamp to min/max
        size = trading_pairs.round_quantity(symbol, size, price)
        max_qty = trading_pairs.get_max_quantity(symbol)
        size = min(size, max_qty)

        return size

    async def _close_all_positions(self, timestamp: datetime):
        """Close all open positions at end of backtest."""
        positions_to_close = list(self.portfolio.positions.keys())

        for position_id in positions_to_close:
            position = self.portfolio.positions.get(position_id)
            if position and position.symbol in self.current_prices:
                self.portfolio.close_position(
                    position_id,
                    self.current_prices[position.symbol],
                    timestamp,
                    exit_reason="backtest_end",
                )

    def _generate_results(self, start_time: datetime) -> BacktestResult:
        """Generate backtest results."""
        stats = self.portfolio.get_statistics()
        trades = self.portfolio.trades

        # Create equity curve DataFrame
        equity_data = []
        for point in self.portfolio.equity_curve:
            equity_data.append(
                {
                    "timestamp": point.timestamp,
                    "balance": float(point.balance),
                    "unrealized_pnl": float(point.unrealized_pnl),
                    "total_equity": float(point.total_equity),
                    "drawdown": point.drawdown,
                    "drawdown_pct": point.drawdown_pct,
                }
            )

        equity_df = pd.DataFrame(equity_data)
        if not equity_df.empty:
            equity_df.set_index("timestamp", inplace=True)

        # Calculate returns
        if not equity_df.empty:
            equity_df["returns"] = equity_df["total_equity"].pct_change()
            daily_returns = equity_df["returns"].dropna()
        else:
            daily_returns = pd.Series()

        # Analyze performance
        metrics = self.analyzer.analyze(trades, equity_df)

        # Strategy performance breakdown
        strategy_perf = self._calculate_strategy_performance(trades)
        symbol_perf = self._calculate_symbol_performance(trades)

        # Drawdown info. The portfolio max_drawdown_pct is the percentage at the
        # maximum DOLLAR drawdown and is not comparable across runs; the true
        # percentage maximum and the episode breakdown come from the analyzer.
        episodes = self.analyzer.drawdown_episodes(equity_df)
        drawdown_info = DrawdownInfo(
            max_drawdown=self.portfolio.max_drawdown,
            max_drawdown_pct=self.portfolio.max_drawdown_pct,
            max_drawdown_pct_true=float(metrics.get("max_drawdown_pct", 0.0)),
            episodes=episodes,
        )
        self._log_drawdown_episodes(drawdown_info)

        # Calculate returns based only on realized PnL (closed trades)
        # Exclude unrealized PnL from open positions for honest results
        realized_pnl = sum(t.net_pnl for t in trades)
        final_balance = self.config.initial_balance + Decimal(str(realized_pnl))
        total_return = final_balance - self.config.initial_balance
        total_return_pct = (float(total_return) / float(self.config.initial_balance)) * 100

        # Log if there are open positions excluded
        open_positions = len(self.portfolio.positions)
        if open_positions > 0:
            unrealized = sum(p.unrealized_pnl for p in self.portfolio.positions.values())
            logger.info(
                f"Excluded {open_positions} open position(s) with unrealized PnL: ${unrealized:.2f}"
            )

        # Annualized return
        days = (self.config.end_date - self.config.start_date).days
        if days > 0 and float(self.config.initial_balance) > 0:
            total_return_decimal = float(total_return) / float(self.config.initial_balance)
            annualized_return = ((1 + total_return_decimal) ** (365 / days) - 1) * 100
        else:
            annualized_return = 0

        result = BacktestResult(
            open_positions=list(self.portfolio.positions.values()),
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            duration=self.config.end_date - self.config.start_date,
            config=self.config,
            initial_balance=self.config.initial_balance,
            final_balance=final_balance,  # Based on realized PnL only
            total_return=total_return,
            total_return_pct=total_return_pct,
            annualized_return=annualized_return,
            total_trades=stats["total_trades"],
            winning_trades=stats["winning_trades"],
            losing_trades=stats["losing_trades"],
            win_rate=stats["win_rate"] * 100,
            avg_win=stats["avg_win"],
            avg_loss=stats["avg_loss"],
            profit_factor=stats["profit_factor"],
            expectancy=metrics.get("expectancy", 0),
            max_drawdown=self.portfolio.max_drawdown,
            max_drawdown_pct=self.portfolio.max_drawdown_pct,
            sharpe_ratio=metrics.get("sharpe_ratio", 0),
            sortino_ratio=metrics.get("sortino_ratio", 0),
            calmar_ratio=metrics.get("calmar_ratio", 0),
            var_95=metrics.get("var_95", 0),
            cvar_95=metrics.get("cvar_95", 0),
            total_commission=stats["total_commission"],
            total_slippage=stats["total_slippage"],
            trades=trades,
            equity_curve=equity_df,
            drawdown_series=equity_df["drawdown_pct"] if not equity_df.empty else None,
            daily_returns=daily_returns,
            strategy_performance=strategy_perf,
            symbol_performance=symbol_perf,
            drawdown_info=drawdown_info,
        )

        return result

    def _calculate_strategy_performance(
        self, trades: List[TradeResult]
    ) -> Dict[str, StrategyMetrics]:
        """Calculate performance metrics for each strategy."""
        performance = {}

        # Group trades by strategy
        strategy_trades: Dict[str, List[TradeResult]] = {}
        for trade in trades:
            name = trade.strategy_name or "unknown"
            if name not in strategy_trades:
                strategy_trades[name] = []
            strategy_trades[name].append(trade)

        for strategy_name, strat_trades in strategy_trades.items():
            winning = [t for t in strat_trades if t.net_pnl > 0]
            losing = [t for t in strat_trades if t.net_pnl <= 0]

            win_rate = len(winning) / len(strat_trades) if strat_trades else 0
            avg_win = sum(t.net_pnl for t in winning) / len(winning) if winning else 0
            avg_loss = sum(t.net_pnl for t in losing) / len(losing) if losing else 0

            gross_profit = sum(t.net_pnl for t in winning)
            gross_loss = abs(sum(t.net_pnl for t in losing))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

            performance[strategy_name] = StrategyMetrics(
                strategy_name=strategy_name,
                total_trades=len(strat_trades),
                winning_trades=len(winning),
                losing_trades=len(losing),
                win_rate=win_rate,
                avg_win=avg_win,
                avg_loss=avg_loss,
                profit_factor=profit_factor,
                total_pnl=sum(t.net_pnl for t in strat_trades),
            )

        return performance

    def _calculate_symbol_performance(self, trades: List[TradeResult]) -> Dict[str, SymbolMetrics]:
        """Calculate performance metrics for each symbol."""
        performance = {}

        # Group trades by symbol
        symbol_trades: Dict[str, List[TradeResult]] = {}
        for trade in trades:
            if trade.symbol not in symbol_trades:
                symbol_trades[trade.symbol] = []
            symbol_trades[trade.symbol].append(trade)

        for symbol, sym_trades in symbol_trades.items():
            winning = [t for t in sym_trades if t.net_pnl > 0]
            losing = [t for t in sym_trades if t.net_pnl <= 0]

            win_rate = len(winning) / len(sym_trades) if sym_trades else 0
            total_pnl = sum(t.net_pnl for t in sym_trades)
            avg_pnl = total_pnl / len(sym_trades) if sym_trades else 0
            total_volume = sum(t.entry_quantity * t.entry_price for t in sym_trades)

            performance[symbol] = SymbolMetrics(
                symbol=symbol,
                total_trades=len(sym_trades),
                winning_trades=len(winning),
                losing_trades=len(losing),
                win_rate=win_rate,
                total_pnl=total_pnl,
                avg_trade_pnl=avg_pnl,
                total_volume=total_volume,
            )

        return performance
