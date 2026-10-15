"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : cleaner
    Project     : Project-103
    Description : Data cleaner for removing outliers and noise.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import statistics
from typing import Any, List, Optional

from src.models import Candle, Ticker

logger = logging.getLogger(__name__)


class DataCleaner:
    """Cleans data by removing outliers and noise."""

    def __init__(self, outlier_threshold: float = 3.0):
        """
        Initialize data cleaner.

        Args:
            outlier_threshold: Z-score threshold for outlier detection
        """
        self.outlier_threshold = outlier_threshold
        self.price_history = {}
        self.volume_history = {}

    def clean(self, data: Any, data_type: str) -> Optional[Any]:
        """
        Clean data object.

        Args:
            data: Data to clean
            data_type: Type of data

        Returns:
            Cleaned data or None if outlier
        """
        if data_type == "candle":
            return self._clean_candle(data)
        elif data_type == "ticker":
            return self._clean_ticker(data)

        return data

    def _clean_candle(self, candle: Candle) -> Optional[Candle]:
        """Clean candle data."""
        symbol = candle.symbol

        # Initialize history if needed
        if symbol not in self.price_history:
            self.price_history[symbol] = []
            self.volume_history[symbol] = []

        # Check for price outliers
        if self.price_history[symbol]:
            prices = [candle.open, candle.high, candle.low, candle.close]

            for price in prices:
                if self._is_outlier(price, self.price_history[symbol]):
                    logger.warning(f"Price outlier detected for {symbol}: {price}")
                    return None

        # Check for volume outliers
        if self.volume_history[symbol] and candle.volume > 0:
            if self._is_outlier(candle.volume, self.volume_history[symbol]):
                logger.warning(f"Volume outlier detected for {symbol}: {candle.volume}")
                # Don't reject, just set to average
                candle.volume = statistics.mean(self.volume_history[symbol][-20:])

        # Update history
        self.price_history[symbol].append(candle.close)
        if candle.volume > 0:
            self.volume_history[symbol].append(candle.volume)

        # Keep history limited
        if len(self.price_history[symbol]) > 100:
            self.price_history[symbol].pop(0)
        if len(self.volume_history[symbol]) > 100:
            self.volume_history[symbol].pop(0)

        return candle

    def _clean_ticker(self, ticker: Ticker) -> Optional[Ticker]:
        """Clean ticker data."""
        symbol = ticker.symbol

        if symbol not in self.price_history:
            self.price_history[symbol] = []

        # Check for price outliers
        if self.price_history[symbol]:
            if self._is_outlier(ticker.last_price, self.price_history[symbol]):
                logger.warning(f"Ticker price outlier for {symbol}: {ticker.last_price}")
                return None

        # Update history
        self.price_history[symbol].append(ticker.last_price)
        if len(self.price_history[symbol]) > 100:
            self.price_history[symbol].pop(0)

        return ticker

    def _is_outlier(self, value: float, history: List[float]) -> bool:
        """Check if value is an outlier."""
        if len(history) < 10:
            return False

        recent = history[-20:]
        mean = statistics.mean(recent)
        std = statistics.stdev(recent)

        if std == 0:
            return abs(value - mean) > mean * 0.1  # 10% threshold

        z_score = abs((value - mean) / std)
        return z_score > self.outlier_threshold
