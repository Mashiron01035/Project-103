"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : time_filter
    Project     : Project-103
    Description : Time-based filter. Filters signals based on trading sessions and time windows.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from src.indicators.base_indicator import Candle
from src.strategies.base_strategy import Signal
from src.strategies.filters.base_filter import BaseFilter, FilterResult

logger = logging.getLogger(__name__)


class TimeFilter(BaseFilter):
    """
    Trading time filter

    Allows you to:
    - Define the allowed trading sessions
    - Exclude specific weekdays
    - Set trading windows
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the time filter

        Config:
            allowed_hours: List[int] - Allowed hours (UTC)
            excluded_weekdays: List[int] - Excluded weekdays (0=Monday)
            trading_sessions: List[dict] - Trading sessions
                [{"start": "08:00", "end": "16:00"}]
        """
        super().__init__("time_filter", config)

        self.allowed_hours = self.config.get("allowed_hours")
        self.excluded_weekdays = self.config.get("excluded_weekdays", [])
        self.trading_sessions = self.config.get("trading_sessions", [])

    def apply(
        self, signal: Signal, candles: list[Candle], market_data: Optional[Dict[str, Any]] = None
    ) -> FilterResult:
        """Apply the time filter"""
        if not self.enabled:
            return FilterResult(passed=True)

        # Convert the timestamp to a datetime
        dt = datetime.fromtimestamp(signal.timestamp / 1000)

        # Weekday check
        if dt.weekday() in self.excluded_weekdays:
            return FilterResult(
                passed=False,
                reason=f"Trading not allowed on {dt.strftime('%A')}",
                metadata={"weekday": dt.weekday()},
            )

        # Hour check
        if self.allowed_hours and dt.hour not in self.allowed_hours:
            return FilterResult(
                passed=False,
                reason=f"Trading not allowed at hour {dt.hour}",
                metadata={"hour": dt.hour},
            )

        # Trading session check
        if self.trading_sessions:
            current_time = dt.time()
            in_session = False

            for session in self.trading_sessions:
                start = datetime.strptime(session["start"], "%H:%M").time()
                end = datetime.strptime(session["end"], "%H:%M").time()

                if start <= current_time <= end:
                    in_session = True
                    break

            if not in_session:
                return FilterResult(
                    passed=False,
                    reason="Outside of trading session",
                    metadata={"current_time": current_time.strftime("%H:%M")},
                )

        return FilterResult(
            passed=True, metadata={"timestamp": signal.timestamp, "datetime": str(dt)}
        )
