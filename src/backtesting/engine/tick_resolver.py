"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 31.03.2026.
    File        : tick_resolver.py
    Project     : Project-103
    Description : Tick-level SL/TP resolution for backtesting.
                  When a candle's range covers both SL and TP, replays
                  actual tick sequence to determine which was hit first.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from src.backtesting.data.tick_data_loader import TickDataLoader
    from src.backtesting.portfolio.portfolio_manager import BacktestPosition

logger = logging.getLogger(__name__)


@dataclass
class TickResolution:
    """Result of a single tick-level event."""

    action: str  # "stop_loss", "breakeven", "take_profit_1", etc., "none"
    price: float  # Execution price (the SL/TP level, not the tick price)
    timestamp_msc: int  # Tick timestamp in UTC milliseconds
    tp_index: Optional[int] = None  # Which TP level was hit (0, 1, 2...)


class TickResolver:
    """
    Resolves ambiguous SL/TP situations using tick data.

    When both SL and TP fall within a single candle's high/low range,
    this class replays the actual tick sequence to determine which
    level was triggered first.
    """

    def __init__(self, tick_data_loader: "TickDataLoader"):
        self.tick_data_loader = tick_data_loader
        self._stats = {"resolved": 0, "fallback": 0}

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def is_ambiguous(
        self, position: "BacktestPosition", candle_high: float, candle_low: float
    ) -> bool:
        """
        Check if both SL and TP are within the candle's range.

        Returns True if tick resolution is needed.
        """
        sl = position.stop_loss
        if sl is None:
            return False

        # Determine which TPs are still active (not yet hit)
        active_tps = []
        if position.take_profits:
            for i, tp in enumerate(position.take_profits):
                if i < len(position.tp_hit) and position.tp_hit[i]:
                    continue
                active_tps.append(tp)

        if not active_tps:
            return False

        if position.side == "LONG":
            sl_in_range = candle_low <= sl
            tp_in_range = any(candle_high >= tp for tp in active_tps)
        else:  # SHORT
            sl_in_range = candle_high >= sl
            tp_in_range = any(candle_low <= tp for tp in active_tps)

        return sl_in_range and tp_in_range

    def resolve(
        self, symbol: str, candle_start_ms: int, candle_end_ms: int, position: "BacktestPosition"
    ) -> List[TickResolution]:
        """
        Replay ticks within a candle to determine SL/TP execution order.

        Handles compound events within a single candle:
        - TP1 hit → breakeven activation → subsequent BE SL hit
        - Trailing stop ratchet through tick sequence

        Args:
            symbol: Trading symbol
            candle_start_ms: Candle open time (UTC ms)
            candle_end_ms: Candle close time (UTC ms)
            position: The position to check

        Returns:
            List of TickResolution events in chronological order.
            Empty list means no ticks available (caller should use candle fallback).
        """
        ticks = self.tick_data_loader.get_ticks_for_candle(symbol, candle_start_ms, candle_end_ms)

        if ticks is None or len(ticks) == 0:
            self._stats["fallback"] += 1
            return []

        self._stats["resolved"] += 1

        # ticks columns: [time_msc, bid, ask]
        # For LONG exits: we sell at bid
        # For SHORT exits: we buy at ask
        is_long = position.side == "LONG"

        # Build mutable state for intra-candle simulation
        current_sl = position.stop_loss
        trailing_active = position.trailing_activated
        trailing_distance = position.trailing_stop_distance
        be_activated = position.breakeven_activated

        # Active (unhit) TP levels with their original indices
        active_tps = []
        if position.take_profits:
            for i, tp in enumerate(position.take_profits):
                if i < len(position.tp_hit) and position.tp_hit[i]:
                    continue
                active_tps.append((i, tp))

        results: List[TickResolution] = []

        for row in ticks:
            time_msc = int(row[0])
            bid = float(row[1])
            ask = float(row[2])

            # Price used for exit checks
            check_price = bid if is_long else ask

            # --- Trailing stop ratchet ---
            if trailing_active and trailing_distance > 0:
                if is_long:
                    new_sl = bid - trailing_distance
                    if current_sl is None or new_sl > current_sl:
                        current_sl = new_sl
                else:
                    new_sl = ask + trailing_distance
                    if current_sl is None or new_sl < current_sl:
                        current_sl = new_sl

            # --- Check TP levels first (lowest index first) ---
            tp_hit_this_tick = None
            for idx, (tp_idx, tp_price) in enumerate(active_tps):
                if is_long and check_price >= tp_price:
                    tp_hit_this_tick = (idx, tp_idx, tp_price)
                    break
                elif not is_long and check_price <= tp_price:
                    tp_hit_this_tick = (idx, tp_idx, tp_price)
                    break

            # --- Check SL ---
            sl_hit = False
            if current_sl is not None:
                if is_long and check_price <= current_sl:
                    sl_hit = True
                elif not is_long and check_price >= current_sl:
                    sl_hit = True

            # --- Determine which event happened on this tick ---
            # If both TP and SL trigger on the exact same tick,
            # TP wins (it would have been a limit order placed earlier)
            if tp_hit_this_tick is not None:
                list_idx, tp_idx, tp_price = tp_hit_this_tick

                results.append(
                    TickResolution(
                        action=f"take_profit_{tp_idx + 1}",
                        price=tp_price,
                        timestamp_msc=time_msc,
                        tp_index=tp_idx,
                    )
                )

                # Remove from active list
                active_tps.pop(list_idx)

                # --- Post-TP state transitions ---
                if tp_idx == 0 and not be_activated:
                    # TP1 hit → activate breakeven
                    metadata = position.metadata or {}
                    use_breakeven = metadata.get("use_breakeven", True)

                    if use_breakeven:
                        if metadata.get("use_trailing_stop", False):
                            # Activate trailing instead of fixed BE
                            trailing_active = True
                            trail_pct = metadata.get("trailing_stop_distance", 0.4) / 100
                            trailing_distance = position.entry_price * trail_pct
                            current_sl = tp_price  # Start trailing from TP1
                        else:
                            # Fixed breakeven: lock profit at entry + ratio * TP1 distance
                            lock_ratio = metadata.get("breakeven_lock_profit_ratio", 0.5)
                            tp1_distance = abs(tp_price - position.entry_price)
                            if is_long:
                                current_sl = position.entry_price + (tp1_distance * lock_ratio)
                            else:
                                current_sl = position.entry_price - (tp1_distance * lock_ratio)
                        be_activated = True

                elif tp_idx == 1 and not trailing_active:
                    # TP2 hit → activate trailing
                    trailing_active = True
                    trailing_distance = position.entry_price * 0.003
                    current_sl = position.take_profits[0] if position.take_profits else current_sl

                # If no more active TPs and all closed, we're done
                if not active_tps:
                    break

                # Don't process SL on same tick as TP
                continue

            if sl_hit:
                exit_reason = "breakeven" if be_activated else "stop_loss"
                results.append(
                    TickResolution(
                        action=exit_reason,
                        price=current_sl,
                        timestamp_msc=time_msc,
                    )
                )
                break  # Position fully closed

        return results
