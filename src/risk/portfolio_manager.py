"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : portfolio_manager.py
    Project     : Project-103
    Description : Portfolio management including position limits, exposure tracking, and diversification.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.models.position import Position, PositionSide

logger = logging.getLogger(__name__)


@dataclass
class PortfolioMetrics:
    """Portfolio metrics and statistics."""

    total_positions: int
    open_long_positions: int
    open_short_positions: int
    total_exposure_usdt: float
    exposure_percent: float  # % of account
    symbols: List[str]
    avg_position_size_usdt: float
    largest_position_usdt: float
    smallest_position_usdt: float


class PortfolioManager:
    """
    Manages trading portfolio and exposure.

    Tracks open positions, enforces position limits, and ensures
    proper diversification.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize PortfolioManager.

        Args:
            config: Portfolio configuration
        """
        self.config = config or {}

        # Limits
        self.max_positions = self.config.get("max_positions", 5)
        self.max_exposure_percent = self.config.get("max_exposure_percent", 0.50)  # 50%
        self.max_position_per_symbol = self.config.get("max_position_per_symbol", 1)

        logger.info(f"PortfolioManager initialized with max_positions={self.max_positions}")

    def can_open_position(
        self, symbol: str, positions: List[Position], account_balance: float
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a new position can be opened.

        Args:
            symbol: Symbol to trade
            positions: Current open positions
            account_balance: Account balance

        Returns:
            Tuple of (can_open, reason)
        """
        # Check total position count
        if len(positions) >= self.max_positions:
            return False, f"Maximum positions reached ({self.max_positions})"

        # Check positions per symbol
        symbol_positions = [p for p in positions if p.symbol == symbol]
        if len(symbol_positions) >= self.max_position_per_symbol:
            return False, f"Maximum positions for {symbol} reached"

        # Check total exposure
        metrics = self.calculate_metrics(positions, account_balance)
        if metrics.exposure_percent >= self.max_exposure_percent * 100:
            return False, f"Maximum exposure reached ({self.max_exposure_percent*100}%)"

        return True, None

    def calculate_metrics(
        self, positions: List[Position], account_balance: float
    ) -> PortfolioMetrics:
        """
        Calculate portfolio metrics.

        Args:
            positions: List of positions
            account_balance: Account balance

        Returns:
            PortfolioMetrics
        """
        if not positions:
            return PortfolioMetrics(
                total_positions=0,
                open_long_positions=0,
                open_short_positions=0,
                total_exposure_usdt=0,
                exposure_percent=0,
                symbols=[],
                avg_position_size_usdt=0,
                largest_position_usdt=0,
                smallest_position_usdt=0,
            )

        # Count by side
        long_count = sum(1 for p in positions if p.side == PositionSide.LONG)
        short_count = sum(1 for p in positions if p.side == PositionSide.SHORT)

        # Calculate exposure
        position_sizes = [p.quantity * p.entry_price for p in positions]

        total_exposure = sum(position_sizes)
        exposure_pct = (total_exposure / account_balance * 100) if account_balance > 0 else 0

        # Get unique symbols
        symbols = list(set(p.symbol for p in positions))

        return PortfolioMetrics(
            total_positions=len(positions),
            open_long_positions=long_count,
            open_short_positions=short_count,
            total_exposure_usdt=total_exposure,
            exposure_percent=exposure_pct,
            symbols=symbols,
            avg_position_size_usdt=total_exposure / len(positions),
            largest_position_usdt=max(position_sizes),
            smallest_position_usdt=min(position_sizes),
        )

    def get_position_allocation(self, positions: List[Position]) -> Dict[str, float]:
        """
        Get allocation by symbol.

        Args:
            positions: List of positions

        Returns:
            Dict mapping symbols to exposure amounts
        """
        allocation: Dict[str, float] = {}

        for position in positions:
            exposure = position.quantity * position.entry_price
            if position.symbol in allocation:
                allocation[position.symbol] += exposure
            else:
                allocation[position.symbol] = exposure

        return allocation
