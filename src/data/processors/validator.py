"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : validator
    Project     : Project-103
    Description : Data validator for checking data quality and consistency.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from datetime import datetime
from typing import Any, List, Optional

from src.models import Candle, OrderBook, Ticker

logger = logging.getLogger(__name__)


class DataValidator:
    """Validates market data for quality and consistency."""

    def __init__(self):
        """Initialize data validator."""
        self.validation_rules = {
            "candle": self._validate_candle,
            "ticker": self._validate_ticker,
            "orderbook": self._validate_orderbook,
        }

    def validate(self, data: Any, data_type: str) -> tuple[bool, List[str]]:
        """
        Validate data object.

        Args:
            data: Data to validate
            data_type: Type of data

        Returns:
            Tuple of (is_valid, errors)
        """
        if data_type in self.validation_rules:
            return self.validation_rules[data_type](data)

        return True, []

    def _validate_candle(self, candle: Candle) -> tuple[bool, List[str]]:
        """Validate candle data."""
        errors = []

        # Check required fields
        if not candle.symbol:
            errors.append("Missing symbol")

        # Check OHLC relationship
        if candle.high < candle.low:
            errors.append("High is less than low")

        if candle.open > candle.high or candle.open < candle.low:
            errors.append("Open is outside high-low range")

        if candle.close > candle.high or candle.close < candle.low:
            errors.append("Close is outside high-low range")

        # Check non-negative values
        if candle.volume < 0:
            errors.append("Negative volume")

        # Check timestamp
        if candle.timestamp <= 0:
            errors.append("Invalid timestamp")

        # Check for future timestamp
        current_time = int(datetime.now().timestamp() * 1000)
        if candle.timestamp > current_time + 60000:  # Allow 1 minute future
            errors.append("Future timestamp")

        return len(errors) == 0, errors

    def _validate_ticker(self, ticker: Ticker) -> tuple[bool, List[str]]:
        """Validate ticker data."""
        errors = []

        if not ticker.symbol:
            errors.append("Missing symbol")

        if ticker.last_price <= 0:
            errors.append("Invalid last price")

        if ticker.bid_price and ticker.ask_price:
            if ticker.bid_price > ticker.ask_price:
                errors.append("Bid price greater than ask price")

        return len(errors) == 0, errors

    def _validate_orderbook(self, orderbook: OrderBook) -> tuple[bool, List[str]]:
        """Validate orderbook data."""
        errors = []

        if not orderbook.symbol:
            errors.append("Missing symbol")

        # Check bid/ask ordering
        if orderbook.bids:
            for i in range(len(orderbook.bids) - 1):
                if orderbook.bids[i].price < orderbook.bids[i + 1].price:
                    errors.append("Bids not in descending order")
                    break

        if orderbook.asks:
            for i in range(len(orderbook.asks) - 1):
                if orderbook.asks[i].price > orderbook.asks[i + 1].price:
                    errors.append("Asks not in ascending order")
                    break

        # Check spread
        if orderbook.bids and orderbook.asks:
            if orderbook.bids[0].price >= orderbook.asks[0].price:
                errors.append("Crossed orderbook (best bid >= best ask)")

        return len(errors) == 0, errors
