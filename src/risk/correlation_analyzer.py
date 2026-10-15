"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : correlation_analyzer.py
    Project     : Project-103
    Description : Analyzes correlation between trading pairs to avoid overexposure.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CorrelationResult:
    """Correlation analysis result."""

    symbol1: str
    symbol2: str
    correlation: float  # -1 to 1
    is_correlated: bool  # Above threshold
    risk_level: str  # "low", "medium", "high"


class CorrelationAnalyzer:
    """
    Analyzes price correlation between symbols.

    Helps prevent overexposure by identifying correlated positions.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize CorrelationAnalyzer.

        Args:
            config: Analyzer configuration
        """
        self.config = config or {}
        self.correlation_threshold = self.config.get("correlation_threshold", 0.7)

        # Known correlations (simplified - should be calculated from price data)
        self.known_correlations: Dict[str, Dict[str, float]] = self.config.get(
            "known_correlations", {}
        )

        logger.info("CorrelationAnalyzer initialized")

    def check_correlation(self, symbol1: str, symbol2: str) -> CorrelationResult:
        """
        Check correlation between two symbols.

        Args:
            symbol1: First symbol
            symbol2: Second symbol

        Returns:
            CorrelationResult
        """
        # Get correlation coefficient
        corr = self._get_correlation(symbol1, symbol2)

        # Determine if correlated
        is_corr = abs(corr) >= self.correlation_threshold

        # Determine risk level
        if abs(corr) >= 0.9:
            risk_level = "high"
        elif abs(corr) >= 0.7:
            risk_level = "medium"
        else:
            risk_level = "low"

        return CorrelationResult(
            symbol1=symbol1,
            symbol2=symbol2,
            correlation=corr,
            is_correlated=is_corr,
            risk_level=risk_level,
        )

    def check_portfolio_correlation(
        self, new_symbol: str, existing_symbols: List[str]
    ) -> Dict[str, CorrelationResult]:
        """
        Check correlation of new symbol with existing portfolio.

        Args:
            new_symbol: Symbol to check
            existing_symbols: Existing symbols in portfolio

        Returns:
            Dict mapping symbols to correlation results
        """
        results = {}

        for symbol in existing_symbols:
            if symbol != new_symbol:
                results[symbol] = self.check_correlation(new_symbol, symbol)

        return results

    def is_safe_to_add(
        self, new_symbol: str, existing_symbols: List[str], max_correlated_positions: int = 2
    ) -> bool:
        """
        Check if it's safe to add a new position.

        Args:
            new_symbol: Symbol to add
            existing_symbols: Existing symbols
            max_correlated_positions: Max number of correlated positions allowed

        Returns:
            True if safe to add
        """
        correlations = self.check_portfolio_correlation(new_symbol, existing_symbols)

        correlated_count = sum(1 for result in correlations.values() if result.is_correlated)

        is_safe = correlated_count < max_correlated_positions

        if not is_safe:
            logger.warning(
                f"Cannot add {new_symbol}: {correlated_count} correlated positions "
                f"(max: {max_correlated_positions})"
            )

        return is_safe

    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """
        Get correlation coefficient between symbols.

        Args:
            symbol1: First symbol
            symbol2: Second symbol

        Returns:
            Correlation coefficient (-1 to 1)
        """
        # Check known correlations
        if symbol1 in self.known_correlations:
            if symbol2 in self.known_correlations[symbol1]:
                return self.known_correlations[symbol1][symbol2]

        if symbol2 in self.known_correlations:
            if symbol1 in self.known_correlations[symbol2]:
                return self.known_correlations[symbol2][symbol1]

        # Default: assume low correlation for unknown pairs
        return 0.0
