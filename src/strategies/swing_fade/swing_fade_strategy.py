"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 31.05.2026.
    File        : swing_fade_strategy.py
    Project     : Project-103
    Description : Swing Fade mean-reversion strategy for XAUUSD — fades completed ZigZag swings with limit entries at the pivot.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.indicators.base_indicator import Candle, IndicatorResult
from src.strategies.base_strategy import BaseStrategy, Signal

logger = logging.getLogger(__name__)


@dataclass
class SwingFadeConfig:
    # ZigZag swing detection
    swing_threshold: float = 10.0  # min $ size for a swing to qualify

    # Entry window (UTC hours, [start, end))
    window_start_hour: int = 13
    window_end_hour: int = 17

    # Risk parameters (price units)
    stop_dollars: float = 3.0  # tighter stop for limit-at-pivot fill
    target_dollars: float = 12.0  # smaller target → higher hit rate

    # Limit-entry timeout: after detection, wait this many minutes for price
    # to retrace back to the pivot. If not reached → cancel pending entry.
    limit_fill_timeout_min: int = 15

    # Max hold time (minutes) after entry.
    max_hold_minutes: int = 240

    # Trade direction filter: "both" | "long_only" | "short_only"
    trade_direction: str = "both"

    # Stop/target reference: "pivot" (Mode B — market entry, SL/TP from pivot)
    # or "entry" (default — limit-at-pivot, SL/TP from entry which == pivot on fill)
    stop_target_reference: str = "entry"

    # Limit-at-pivot fill mechanics (emitted in signal.metadata for engine):
    #   use_limit_entry: True → engine creates pending limit at pivot price
    #                    False → engine uses market entry (next-candle open)
    #   cancel_on_limit_timeout: True → engine cancels expired limit (no market fallback)
    #                            False → engine market-fills at timeout (drags WR by ~14pp)
    use_limit_entry: bool = True
    cancel_on_limit_timeout: bool = True

    # Max swing signals emitted per UTC day per symbol. 1 = strict (default,
    # original behavior). Higher = more attempts when earlier limits cancel
    # or earlier positions close — but raises slippage / cancel-cost exposure.
    max_trades_per_day: int = 1

    # Position sizing for the portfolio manager (fixed_fractional by default)
    initial_position_size: float = 1.0
    position_sizing_method: str = "fixed_fractional"
    risk_per_trade: float = 0.05

    # Confidence to pass min_confidence gates
    signal_confidence: float = 0.7


@dataclass
class _ZigZagState:
    """Per-symbol real-time ZigZag tracking state."""

    direction: int = 0  # 0 = undetermined, 1 = up-leg, -1 = down-leg
    cur_max: float = 0.0
    cur_max_ts: int = 0
    cur_min: float = 0.0
    cur_min_ts: int = 0
    swing_start_price: float = 0.0
    swing_start_ts: int = 0
    last_processed_ts: int = 0
    initialized: bool = False


@dataclass
class _ArmedEntry:
    """Pending limit-entry state per symbol after a window pivot was detected."""

    pivot_price: float = 0.0  # target fill level (= swing pivot)
    direction: int = 0  # +1 for BUY fade, -1 for SELL fade
    deadline_ts: int = 0  # ms epoch — cancel if not filled by this time
    pivot_date: str = ""  # date of the pivot — used for daily one-trade gate


class SwingFadeStrategy(BaseStrategy):
    """Fade first ZigZag swing of US session."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, name: str = "swing_fade"):
        super().__init__(name, config or {})
        c = config or {}
        self.sf_config = SwingFadeConfig(
            swing_threshold=float(c.get("swing_threshold", 10.0)),
            window_start_hour=int(c.get("window_start_hour", 13)),
            window_end_hour=int(c.get("window_end_hour", 17)),
            stop_dollars=float(c.get("stop_dollars", 5.0)),
            target_dollars=float(c.get("target_dollars", 20.0)),
            max_hold_minutes=int(c.get("max_hold_minutes", 240)),
            trade_direction=str(c.get("trade_direction", "both")),
            stop_target_reference=str(c.get("stop_target_reference", "entry")),
            use_limit_entry=bool(c.get("use_limit_entry", True)),
            cancel_on_limit_timeout=bool(c.get("cancel_on_limit_timeout", True)),
            max_trades_per_day=int(c.get("max_trades_per_day", 1)),
            initial_position_size=float(c.get("initial_position_size", 1.0)),
            risk_per_trade=float(c.get("risk_per_trade", 0.05)),
            position_sizing_method=str(c.get("position_sizing_method", "fixed_lots")),
            signal_confidence=float(c.get("signal_confidence", 0.7)),
        )
        self._states: Dict[str, _ZigZagState] = {}
        # Per (symbol, "YYYY-MM-DD") counter of signals emitted today; capped by
        # SwingFadeConfig.max_trades_per_day. Counter ticks at signal emission,
        # not at fill, so cancelled limits also consume a slot.
        self._trade_count: Dict[tuple, int] = {}
        self._armed: Dict[str, _ArmedEntry] = {}

    def get_indicators_config(self) -> List[str]:
        # Strategy operates on raw candles, but engine requires at least one
        # indicator to invoke analyze(). ATR is also useful for later
        # vol-adaptive stop/target variants.
        return ["atr{14}"]

    def _get_state(self, symbol: str, candle: Candle) -> _ZigZagState:
        st = self._states.get(symbol)
        if st is None or not st.initialized:
            st = _ZigZagState(
                direction=0,
                cur_max=candle.high,
                cur_max_ts=candle.timestamp,
                cur_min=candle.low,
                cur_min_ts=candle.timestamp,
                last_processed_ts=candle.timestamp,
                initialized=True,
            )
            self._states[symbol] = st
        return st

    def _step_zigzag(self, st: _ZigZagState, candle: Candle, threshold: float) -> Optional[dict]:
        """Advance ZigZag state with one M1 candle.
        Returns swing-completion event {direction, end_ts, end_price, size} or None.
        """
        h, l, ts = candle.high, candle.low, candle.timestamp  # noqa: E741 - OHLC shorthand

        if h > st.cur_max:
            st.cur_max = h
            st.cur_max_ts = ts
        if l < st.cur_min:
            st.cur_min = l
            st.cur_min_ts = ts

        completed = None

        if st.direction == 0:
            if st.cur_max - l >= threshold:
                # downswing starting
                st.direction = -1
                st.swing_start_price = st.cur_max
                st.swing_start_ts = st.cur_max_ts
                st.cur_min = l
                st.cur_min_ts = ts
            elif h - st.cur_min >= threshold:
                st.direction = 1
                st.swing_start_price = st.cur_min
                st.swing_start_ts = st.cur_min_ts
                st.cur_max = h
                st.cur_max_ts = ts
        elif st.direction == 1:
            # up-leg in progress — confirm pivot at cur_max when retrace >= threshold
            if st.cur_max - l >= threshold:
                completed = {
                    "direction": 1,
                    "end_ts": st.cur_max_ts,
                    "end_price": st.cur_max,
                    "size": st.cur_max - st.swing_start_price,
                }
                st.direction = -1
                st.swing_start_price = st.cur_max
                st.swing_start_ts = st.cur_max_ts
                st.cur_min = l
                st.cur_min_ts = ts
        elif st.direction == -1:
            if h - st.cur_min >= threshold:
                completed = {
                    "direction": -1,
                    "end_ts": st.cur_min_ts,
                    "end_price": st.cur_min,
                    "size": st.swing_start_price - st.cur_min,
                }
                st.direction = 1
                st.swing_start_price = st.cur_min
                st.swing_start_ts = st.cur_min_ts
                st.cur_max = h
                st.cur_max_ts = ts

        st.last_processed_ts = ts
        return completed

    async def analyze(
        self,
        symbol: str,
        candles: List[Candle],
        indicators: Dict[str, IndicatorResult],
    ) -> Optional[List[Signal]]:
        if not candles:
            return None

        cfg = self.sf_config
        st = self._get_state(symbol, candles[0])

        new_candles = [c for c in candles if c.timestamp > st.last_processed_ts]
        if not new_candles and st.last_processed_ts != 0:
            return None
        if not new_candles:
            new_candles = candles

        emitted: Optional[Signal] = None
        for c in new_candles:
            ev = self._step_zigzag(st, c, cfg.swing_threshold)
            if ev is None:
                continue

            ts_ms = ev["end_ts"]
            if hasattr(ts_ms, "timestamp"):
                ts_ms = int(ts_ms.timestamp() * 1000)
            end_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            if end_dt.hour < cfg.window_start_hour or end_dt.hour >= cfg.window_end_hour:
                continue

            candle_date = end_dt.strftime("%Y-%m-%d")
            slot_key = (symbol, candle_date)
            if self._trade_count.get(slot_key, 0) >= cfg.max_trades_per_day:
                continue

            first_dir = ev["direction"]
            sign = -1 if first_dir == 1 else 1
            if cfg.trade_direction == "long_only" and sign != 1:
                continue
            if cfg.trade_direction == "short_only" and sign != -1:
                continue

            pivot = float(ev["end_price"])
            if cfg.stop_target_reference == "pivot":
                # Mode B: market entry at current candle close, SL/TP measured from pivot
                entry_price = float(c.close)
                if sign == 1:
                    side_str = "BUY"
                    stop_loss = pivot - cfg.stop_dollars
                    take_profit = [pivot + cfg.target_dollars]
                else:
                    side_str = "SELL"
                    stop_loss = pivot + cfg.stop_dollars
                    take_profit = [pivot - cfg.target_dollars]
            else:
                # Default: entry = pivot (engine treats as limit when limit_entry enabled)
                entry_price = pivot
                if sign == 1:
                    side_str = "BUY"
                    stop_loss = entry_price - cfg.stop_dollars
                    take_profit = [entry_price + cfg.target_dollars]
                else:
                    side_str = "SELL"
                    stop_loss = entry_price + cfg.stop_dollars
                    take_profit = [entry_price - cfg.target_dollars]

            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit[0] - entry_price)
            rr = reward / risk if risk > 0 else 0.0

            metadata: Dict[str, Any] = {
                "strategy_variant": "swing_fade_limit_at_pivot",
                "pivot_price": pivot,
                "faded_swing_direction": "up" if first_dir == 1 else "down",
                "faded_swing_size": float(ev["size"]),
                "swing_threshold": cfg.swing_threshold,
                "window": f"{cfg.window_start_hour:02d}-{cfg.window_end_hour:02d}",
                "max_hold_minutes": cfg.max_hold_minutes,
                "initial_position_size": cfg.initial_position_size,
                "position_sizing_method": cfg.position_sizing_method,
                "risk_per_trade": cfg.risk_per_trade,
                # Per-signal limit-entry flags (read by engine via metadata).
                # Defaults True — strategy edge requires fill AT pivot, and bad
                # market fallback (~27% WR) tanks WR far below limit-fills (~65%).
                "cancel_on_limit_timeout": cfg.cancel_on_limit_timeout,
                "use_limit_entry": cfg.use_limit_entry,
                "limit_fill_timeout_min": cfg.limit_fill_timeout_min,
            }

            emitted = Signal(
                id=str(_uuid.uuid4()),
                timestamp=c.timestamp,
                symbol=symbol,
                strategy_name=self.name,
                timeframe=self.timeframes[0] if self.timeframes else "1m",
                side=side_str,
                confidence=cfg.signal_confidence,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_reward_ratio=rr,
                indicators_snapshot={},
                metadata=metadata,
            )

            self._trade_count[slot_key] = self._trade_count.get(slot_key, 0) + 1
            slot_used = self._trade_count[slot_key]
            logger.info(
                f"[{self.name.upper()}] {symbol} {side_str} limit @ pivot {entry_price:.2f}  "
                f"SL={stop_loss:.2f} TP={take_profit[0]:.2f} RR={rr:.1f}  "
                f"faded {'up' if first_dir == 1 else 'down'}-swing size=${ev['size']:.2f}  "
                f"(slot {slot_used}/{cfg.max_trades_per_day})"
            )
            break  # one signal per analyze call

        return [emitted] if emitted else None

    def reset_warmup_state(self) -> None:
        self._states.clear()
        self._trade_count.clear()
        self._armed.clear()
        logger.info(f"[{self.name.upper()}] warmup state reset")
