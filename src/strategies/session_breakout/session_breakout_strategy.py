"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 01.04.2026.
    File        : session_breakout_strategy.py
    Project     : Project-103
    Description : Session opening-range breakout strategy for XAUUSD — tracks the session open range and enters on a break with R:R-based SL/TP, one trade per session.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.indicators.base_indicator import Candle, IndicatorResult
from src.strategies.base_strategy import BaseStrategy, Signal

logger = logging.getLogger(__name__)


@dataclass
class SessionBreakoutConfig:
    """Configuration for Session Breakout strategy."""

    # Session timing (UTC hours)
    session_start_hour: int = 8  # Start tracking range
    range_minutes: int = 30  # Minutes to form range (8:00-8:30)
    session_end_hour: int = 15  # Close any open position

    # Entry
    breakout_buffer_atr: float = 0.3  # Buffer above/below range for entry
    min_range_atr: float = 0.5  # Min range size in ATR (skip tiny ranges)
    max_range_atr: float = 4.0  # Max range size (skip huge gap opens)

    # Risk management
    sl_buffer_atr: float = 0.5  # SL = opposite range edge + buffer
    tp_rr_ratio: float = 2.0  # Take profit = risk × ratio
    tp2_rr_ratio: float = 3.0  # Second TP level
    tp_distribution: str = "60/40"  # % at TP1 / % trailing after TP1

    # Trailing after TP1
    use_trailing_after_tp1: bool = True
    trailing_callback_atr: float = 0.5  # Trail by 0.5 ATR after TP1

    # Filters
    atr_period: int = 14
    use_asgma_filter: bool = False  # Only enter when ASGMA confirms direction
    skip_friday: bool = False
    skip_monday: bool = False
    skip_wednesday: bool = False
    long_only: bool = False
    short_only: bool = False

    # Max one trade per session
    max_trades_per_session: int = 1

    # Candle-close confirmation: require previous candle close beyond breakout level
    require_candle_confirm: bool = False

    # EMA trend filter: only trade in direction of EMA
    use_ema_filter: bool = False
    ema_period: int = 200

    # Momentum filter: breakout candle body must exceed this fraction of ATR
    min_body_atr: float = 0.0

    # Daily loss limit
    use_daily_loss_limit: bool = False
    daily_loss_limit_pct: float = 10.0


class SessionBreakoutStrategy(BaseStrategy):
    """
    Session Open Range Breakout — one high-quality trade per session.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, name: str = "session_breakout_eu"):
        default_config = {
            "enabled": True,
            "timeframes": ["1m"],
            "min_confidence": 0.30,
            "max_positions": 2,
        }
        if config:
            default_config.update(config)

        super().__init__(name, default_config)
        self.cfg = self._parse_config(config or {})

        # Session state tracking
        self._range_high: Dict[str, float] = {}  # symbol -> range high
        self._range_low: Dict[str, float] = {}  # symbol -> range low
        self._range_formed: Dict[str, bool] = {}  # symbol -> range complete
        self._traded_today: Dict[str, int] = {}  # symbol -> trades count today
        self._current_date: Optional[str] = None
        self._range_start_candle: Dict[str, int] = {}  # symbol -> candle index at range start

        self._daily_pnl: float = 0.0
        self._daily_date: Optional[str] = None
        self._daily_start_balance: float = float(default_config.get("account_balance", 0.0))
        self._daily_loss_triggered: bool = False

        self._ml_gate = self._init_ml_gate(config or {})

        logger.info(
            f"SessionBreakout initialized: "
            f"range={self.cfg.session_start_hour}:00+{self.cfg.range_minutes}m, "
            f"TP R:R={self.cfg.tp_rr_ratio}, "
            f"SL buffer={self.cfg.sl_buffer_atr} ATR"
        )

    def _init_ml_gate(self, config: Dict[str, Any]):
        """Optional ML gate. None if disabled in the config.

        lightgbm is imported ONLY when ml_gate.enabled=true.
        """
        gate_cfg = config.get("ml_gate", {}) or {}
        if not gate_cfg.get("enabled", False):
            return None
        try:
            from src.strategies.filters.ml_gate_filter import MLGateFilter

            gate = MLGateFilter(gate_cfg)
            logger.info(f"[{self.name.upper()}] ML gate enabled")
            return gate
        except Exception as e:  # noqa: BLE001
            logger.error(f"[{self.name.upper()}] ML gate not initialized: {e}")
            return None

    def _parse_config(self, config: Dict[str, Any]) -> SessionBreakoutConfig:
        return SessionBreakoutConfig(
            session_start_hour=config.get("session_start_hour", 8),
            range_minutes=config.get("range_minutes", 30),
            session_end_hour=config.get("session_end_hour", 15),
            breakout_buffer_atr=config.get("breakout_buffer_atr", 0.3),
            min_range_atr=config.get("min_range_atr", 0.5),
            max_range_atr=config.get("max_range_atr", 4.0),
            sl_buffer_atr=config.get("sl_buffer_atr", 0.5),
            tp_rr_ratio=config.get("tp_rr_ratio", 2.0),
            tp2_rr_ratio=config.get("tp2_rr_ratio", 3.0),
            use_trailing_after_tp1=config.get("use_trailing_after_tp1", True),
            trailing_callback_atr=config.get("trailing_callback_atr", 0.5),
            atr_period=config.get("atr_period", 14),
            use_asgma_filter=config.get("use_asgma_filter", False),
            skip_friday=config.get("skip_friday", False),
            skip_monday=config.get("skip_monday", False),
            skip_wednesday=config.get("skip_wednesday", False),
            long_only=config.get("long_only", False),
            short_only=config.get("short_only", False),
            max_trades_per_session=config.get("max_trades_per_session", 1),
            require_candle_confirm=config.get("require_candle_confirm", False),
            use_ema_filter=config.get("use_ema_filter", False),
            ema_period=config.get("ema_period", 200),
            min_body_atr=config.get("min_body_atr", 0.0),
            use_daily_loss_limit=config.get("use_daily_loss_limit", False),
            daily_loss_limit_pct=config.get("daily_loss_limit_pct", 10.0),
        )

    def get_indicators_config(self) -> List[str]:
        indicators = [f"atr{{{self.cfg.atr_period}}}"]
        if self.cfg.use_asgma_filter:
            indicators.append("asgma{1,25,14,20}")
        if self.cfg.use_ema_filter:
            indicators.append(f"ema{{{self.cfg.ema_period}}}")
        return indicators

    def reset_warmup_state(self) -> None:
        """Discard session ranges built from warmup candles.

        The strategy already auto-resets on date change, so this matters
        only when warmup and live start fall on the same UTC date.
        """
        self._range_high.clear()
        self._range_low.clear()
        self._range_formed.clear()
        self._traded_today.clear()
        self._range_start_candle.clear()
        self._current_date = None
        logger.info(f"[{self.name.upper()}] warmup state reset")

    async def analyze(
        self,
        symbol: str,
        candles: List[Candle],
        indicators: Dict[str, IndicatorResult],
    ) -> Optional[Signal]:
        if len(candles) < self.cfg.atr_period + 10:
            return None

        current_candle = candles[-1]
        current_price = current_candle.close
        candle_index = self._get_candle_index(candles)

        # --- Time parsing ---
        if isinstance(current_candle.timestamp, (int, float)):
            candle_dt = datetime.fromtimestamp(current_candle.timestamp / 1000, tz=timezone.utc)
        else:
            candle_dt = current_candle.timestamp

        hour = candle_dt.hour
        minute = candle_dt.minute
        weekday = candle_dt.weekday()
        current_date = candle_dt.strftime("%Y-%m-%d")

        # --- Day filters ---
        if self.cfg.skip_monday and weekday == 0:
            return None
        if self.cfg.skip_wednesday and weekday == 2:
            return None
        if self.cfg.skip_friday and weekday == 4:
            return None
        if weekday >= 5:  # Weekend
            return None

        # --- Reset on new day ---
        if current_date != self._current_date:
            self._current_date = current_date
            self._range_high.clear()
            self._range_low.clear()
            self._range_formed.clear()
            self._traded_today.clear()
            self._range_start_candle.clear()

        if current_date != self._daily_date:
            self._daily_date = current_date
            self._daily_pnl = 0.0
            self._daily_loss_triggered = False
        if self.cfg.use_daily_loss_limit and self._daily_loss_triggered:
            return None

        # --- Get ATR ---
        atr_key = f"atr{{{self.cfg.atr_period}}}"
        atr_ind = indicators.get(atr_key)
        if not atr_ind:
            return None
        atr_val = atr_ind.values.get("value", 0)
        if atr_val <= 0:
            return None

        cfg = self.cfg

        # --- Phase 1: Build opening range (first N minutes of session) ---
        session_start = cfg.session_start_hour
        range_end_minute = session_start * 60 + cfg.range_minutes
        current_minute_of_day = hour * 60 + minute

        if hour == session_start and minute == 0:
            # Reset range at session start
            if symbol not in self._range_start_candle:
                self._range_high[symbol] = current_candle.high
                self._range_low[symbol] = current_candle.low
                self._range_formed[symbol] = False
                self._range_start_candle[symbol] = candle_index

        # Building range
        if (
            current_minute_of_day >= session_start * 60
            and current_minute_of_day < range_end_minute
            and not self._range_formed.get(symbol, False)
        ):
            if symbol in self._range_high:
                self._range_high[symbol] = max(self._range_high[symbol], current_candle.high)
                self._range_low[symbol] = min(self._range_low[symbol], current_candle.low)
            return None  # Still building range

        # Mark range as formed
        if (
            current_minute_of_day >= range_end_minute
            and not self._range_formed.get(symbol, False)
            and symbol in self._range_high
        ):
            self._range_formed[symbol] = True
            range_size = self._range_high[symbol] - self._range_low[symbol]
            range_atr = range_size / atr_val if atr_val > 0 else 0
            logger.info(
                f"[SESSION_BREAKOUT] Range formed {symbol}: "
                f"H={self._range_high[symbol]:.2f} L={self._range_low[symbol]:.2f} "
                f"size={range_size:.2f} ({range_atr:.1f} ATR)"
            )

        # --- Phase 2: Check for breakout ---
        if not self._range_formed.get(symbol, False):
            return None

        # Outside trading hours
        if hour >= cfg.session_end_hour:
            return None

        # Already traded this session
        trades_today = self._traded_today.get(symbol, 0)
        if trades_today >= cfg.max_trades_per_session:
            return None

        range_high = self._range_high[symbol]
        range_low = self._range_low[symbol]
        range_size = range_high - range_low

        # Range size filter
        range_atr = range_size / atr_val
        if range_atr < cfg.min_range_atr or range_atr > cfg.max_range_atr:
            return None

        # Entry levels with buffer
        buy_level = range_high + atr_val * cfg.breakout_buffer_atr
        sell_level = range_low - atr_val * cfg.breakout_buffer_atr

        # --- Direction filter ---
        allow_buy = True
        allow_sell = True
        if cfg.long_only:
            allow_sell = False
        if cfg.short_only:
            allow_buy = False
        if cfg.use_asgma_filter:
            asgma_ind = indicators.get("asgma{1,25,14,20}")
            if asgma_ind:
                direction = asgma_ind.values.get("direction", 0)
                if direction < 0:
                    allow_buy = False
                if direction > 0:
                    allow_sell = False
        if cfg.use_ema_filter:
            ema_key = f"ema{{{cfg.ema_period}}}"
            ema_ind = indicators.get(ema_key)
            if ema_ind:
                ema_val = ema_ind.values.get("value", 0)
                if ema_val > 0:
                    if current_price < ema_val:
                        allow_buy = False
                    if current_price > ema_val:
                        allow_sell = False

        # --- Candle-close confirmation ---
        prev_close = candles[-2].close if len(candles) >= 2 else current_price

        # --- Momentum filter ---
        if cfg.min_body_atr > 0:
            candle_body = abs(current_candle.close - current_candle.open)
            if candle_body < atr_val * cfg.min_body_atr:
                return None

        # --- Check breakout ---
        signal = None

        if current_price >= buy_level and allow_buy:
            if cfg.require_candle_confirm and prev_close < buy_level:
                return None
            # Bullish breakout
            sl = range_low - atr_val * cfg.sl_buffer_atr
            risk = current_price - sl
            tp1 = current_price + risk * cfg.tp_rr_ratio

            metadata = {
                "strategy_type": "session_breakout",
                "range_high": range_high,
                "range_low": range_low,
                "range_size_atr": range_atr,
                "use_breakeven": True,
                "use_trailing_stop": cfg.use_trailing_after_tp1,
                "trailing_activation_percent": (risk / current_price) * 100,
                "trailing_callback_percent": (atr_val * cfg.trailing_callback_atr / current_price)
                * 100,
                "position_sizing_method": self.config.get("position_sizing_method"),
                "risk_per_trade": self.config.get("risk_per_trade"),
            }

            signal = self._create_buy_signal(
                symbol=symbol,
                candles=candles,
                indicators=indicators,
                confidence=0.75,
                stop_loss=sl,
                take_profit=[tp1],
                metadata=metadata,
            )

        elif current_price <= sell_level and allow_sell:
            if cfg.require_candle_confirm and prev_close > sell_level:
                return None
            # Bearish breakout
            sl = range_high + atr_val * cfg.sl_buffer_atr
            risk = sl - current_price
            tp1 = current_price - risk * cfg.tp_rr_ratio

            metadata = {
                "strategy_type": "session_breakout",
                "range_high": range_high,
                "range_low": range_low,
                "range_size_atr": range_atr,
                "use_breakeven": True,
                "use_trailing_stop": cfg.use_trailing_after_tp1,
                "trailing_activation_percent": (risk / current_price) * 100,
                "trailing_callback_percent": (atr_val * cfg.trailing_callback_atr / current_price)
                * 100,
                "position_sizing_method": self.config.get("position_sizing_method"),
                "risk_per_trade": self.config.get("risk_per_trade"),
            }

            signal = self._create_sell_signal(
                symbol=symbol,
                candles=candles,
                indicators=indicators,
                confidence=0.75,
                stop_loss=sl,
                take_profit=[tp1],
                metadata=metadata,
            )

        if signal:
            # The daily slot is consumed BEFORE the gate — a gated run yields a strict
            # subset of the baseline signals (a clean A/B).
            self._traded_today[symbol] = trades_today + 1

            if self._ml_gate is not None:
                gate_result = self._ml_gate.apply(signal, candles)
                if not gate_result.passed:
                    logger.info(
                        f"[SESSION_BREAKOUT] {signal.side} {symbol} "
                        f"@ {current_price:.2f} rejected by the ML gate: {gate_result.reason}"
                    )
                    return None

            logger.info(
                f"[SESSION_BREAKOUT] {signal.side} {symbol} @ {current_price:.2f} | "
                f"Range: {range_low:.2f}-{range_high:.2f} ({range_atr:.1f} ATR) | "
                f"SL: {signal.stop_loss:.2f}, TP: {signal.take_profit}"
            )

        return signal

    def notify_trade_closed(
        self,
        symbol: str,
        pnl_pct: float,
        exit_reason: str = "",
        metadata: dict = None,
        net_pnl: float = 0.0,
        exit_time=None,
    ):
        """Track daily PnL for daily loss limit."""
        if not self.cfg.use_daily_loss_limit or net_pnl == 0.0:
            return

        trade_date = None
        if exit_time is not None:
            if isinstance(exit_time, (int, float)):
                trade_date = datetime.fromtimestamp(exit_time / 1000, tz=timezone.utc).strftime(
                    "%Y-%m-%d"
                )
            elif hasattr(exit_time, "strftime"):
                trade_date = exit_time.strftime("%Y-%m-%d")

        if trade_date and trade_date != self._daily_date:
            self._daily_date = trade_date
            self._daily_pnl = 0.0
            self._daily_loss_triggered = False
            balance = float((metadata or {}).get("account_balance", 0))
            if balance > 0:
                self._daily_start_balance = balance

        self._daily_pnl += float(net_pnl)

        if self._daily_start_balance > 0:
            limit = self._daily_start_balance * self.cfg.daily_loss_limit_pct / 100.0
            if self._daily_pnl < -limit and not self._daily_loss_triggered:
                self._daily_loss_triggered = True
                logger.warning(
                    f"[{self.name.upper()}] Daily loss limit triggered: "
                    f"daily_pnl=${self._daily_pnl:.2f}, "
                    f"limit=-${limit:.2f} ({self.cfg.daily_loss_limit_pct}% of ${self._daily_start_balance:.2f})"
                )
