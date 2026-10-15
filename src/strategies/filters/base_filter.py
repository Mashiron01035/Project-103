"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : base_filter
    Project     : Project-103
    Description : Base filter class. Abstract interface for strategy signal filters.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.indicators.base_indicator import Candle
from src.strategies.base_strategy import Signal

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Result of applying a filter"""

    passed: bool  # Whether the signal passed the filter
    reason: Optional[str] = None  # Rejection reason (if passed=False)
    metadata: Dict[str, Any] = None  # Additional information

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseFilter(ABC):
    """
    Abstract base class for signal filters

    Filters add extra validation of signals based on various
    conditions (time, volatility, volume, etc.)
    """

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the filter

        Args:
            name: Filter name
            config: Filter configuration
        """
        self.name = name
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

        logger.debug(f"Initialized filter: {self.name}")

    @abstractmethod
    def apply(
        self, signal: Signal, candles: list[Candle], market_data: Optional[Dict[str, Any]] = None
    ) -> FilterResult:
        """
        Apply the filter to a signal

        Args:
            signal: Signal to check
            candles: List of candles
            market_data: Additional market data

        Returns:
            FilterResult with the check result
        """
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', enabled={self.enabled})"
