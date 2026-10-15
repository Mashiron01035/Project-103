"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 11.01.2026.
    File        : smart_entry.py
    Project     : Project-103
    Description : Order-book analyzer for optimal limit entry — target price, spread/liquidity, fill probability, and a recommended timeout.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SmartEntryResult:
    """Result of order book analysis for smart entry."""

    recommended_price: float  # Optimal limit price
    spread_percent: float  # Current bid-ask spread %
    spread_absolute: float  # Absolute spread
    best_bid: float  # Best bid price
    best_ask: float  # Best ask price
    mid_price: float  # Mid price
    liquidity_score: float  # 0-1, liquidity at our size level
    fill_probability: float  # 0-1, estimated fill probability
    max_wait_seconds: int  # Recommended timeout
    use_limit: bool  # Whether to use limit (vs market)
    reason: str  # Explanation


class SmartEntryAnalyzer:
    """
    Analyzes order book to determine optimal entry price for limit orders.

    Strategy:
    - If spread is tight (<0.05%) → place limit at mid price
    - If spread is wide → place limit closer to our side
    - If liquidity is low → fallback to market
    - Timeout based on volatility/spread
    """

    def __init__(
        self,
        connector,
        min_liquidity_score: float = 0.3,
        min_fill_probability: float = 0.5,
        price_offset_percent: float = 0.02,  # 0.02% offset from best price
        default_timeout: int = 30,
    ):
        """
        Initialize SmartEntryAnalyzer.

        Args:
            connector: Exchange connector with get_orderbook method
            min_liquidity_score: Minimum liquidity to use limit order
            min_fill_probability: Minimum fill probability to use limit
            price_offset_percent: Offset from best price (%)
            default_timeout: Default timeout in seconds
        """
        self.connector = connector
        self.min_liquidity_score = min_liquidity_score
        self.min_fill_probability = min_fill_probability
        self.price_offset_percent = price_offset_percent
        self.default_timeout = default_timeout

    async def analyze(
        self, symbol: str, side: str, quantity: float, current_price: Optional[float] = None
    ) -> SmartEntryResult:
        """
        Analyze order book and determine optimal entry price.

        Args:
            symbol: Trading pair symbol
            side: "BUY" or "SELL"
            quantity: Order quantity
            current_price: Current market price (optional, for fallback)

        Returns:
            SmartEntryResult with recommended price and analysis
        """
        try:
            # Get order book
            orderbook = await self.connector.get_orderbook(symbol, limit=20)

            if not orderbook or not orderbook.get("bids") or not orderbook.get("asks"):
                logger.warning(f"Empty orderbook for {symbol}, using market order")
                return self._market_fallback(current_price or 0, "Empty orderbook")

            bids = orderbook["bids"]  # [[price, qty], ...]
            asks = orderbook["asks"]  # [[price, qty], ...]

            if not bids or not asks:
                return self._market_fallback(current_price or 0, "No bids/asks")

            # Extract best prices
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid_price = (best_bid + best_ask) / 2

            # Calculate spread
            spread_absolute = best_ask - best_bid
            spread_percent = (spread_absolute / mid_price) * 100

            # Calculate liquidity at our size level
            liquidity_score = self._calculate_liquidity(
                bids if side == "BUY" else asks, quantity, mid_price
            )

            # Determine optimal price
            recommended_price = self._calculate_optimal_price(
                side, best_bid, best_ask, spread_percent
            )

            # Estimate fill probability
            fill_probability = self._estimate_fill_probability(spread_percent, liquidity_score)

            # Determine timeout based on spread
            max_wait = self._calculate_timeout(spread_percent)

            # Decide whether to use limit
            use_limit = (
                liquidity_score >= self.min_liquidity_score
                and fill_probability >= self.min_fill_probability
                and spread_percent < 0.5  # Max 0.5% spread for limit
            )

            reason = self._build_reason(
                spread_percent, liquidity_score, fill_probability, use_limit
            )

            result = SmartEntryResult(
                recommended_price=recommended_price,
                spread_percent=spread_percent,
                spread_absolute=spread_absolute,
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=mid_price,
                liquidity_score=liquidity_score,
                fill_probability=fill_probability,
                max_wait_seconds=max_wait,
                use_limit=use_limit,
                reason=reason,
            )

            logger.debug(
                f"SmartEntry {symbol} {side}: price={recommended_price:.4f}, "
                f"spread={spread_percent:.3f}%, liq={liquidity_score:.2f}, "
                f"fill_prob={fill_probability:.2f}, use_limit={use_limit}"
            )

            return result

        except Exception as e:
            logger.error(f"Error analyzing orderbook for {symbol}: {e}")
            return self._market_fallback(current_price or 0, f"Error: {e}")

    def _calculate_liquidity(self, levels: List[List], quantity: float, mid_price: float) -> float:
        """
        Calculate liquidity score based on order book depth.

        Args:
            levels: Order book levels [[price, qty], ...]
            quantity: Our order quantity
            mid_price: Mid price for value calculation

        Returns:
            Liquidity score 0-1
        """
        if not levels:
            return 0.0

        # Sum quantity at top 5 levels
        total_qty = sum(float(level[1]) for level in levels[:5])

        # Calculate total value
        total_value = total_qty * mid_price
        our_value = quantity * mid_price

        # If our order is small relative to book, high liquidity
        if total_value == 0:
            return 0.0

        ratio = our_value / total_value

        # Score: 1.0 if our order is <5% of book, decreasing after
        if ratio < 0.05:
            return 1.0
        elif ratio < 0.1:
            return 0.8
        elif ratio < 0.2:
            return 0.6
        elif ratio < 0.5:
            return 0.4
        else:
            return 0.2

    def _calculate_optimal_price(
        self, side: str, best_bid: float, best_ask: float, spread_percent: float
    ) -> float:
        """
        Calculate optimal limit price.

        Strategy:
        - Tight spread (<0.03%): place at mid price
        - Normal spread (0.03-0.1%): place 30% into spread
        - Wide spread (>0.1%): place closer to our side
        """
        spread = best_ask - best_bid

        if spread_percent < 0.03:
            # Tight spread - place at mid
            offset_ratio = 0.5
        elif spread_percent < 0.1:
            # Normal spread - 30% into spread
            offset_ratio = 0.3
        else:
            # Wide spread - stay closer to our side
            offset_ratio = 0.2

        if side == "BUY":
            # BUY: start from bid, go up into spread
            price = best_bid + (spread * offset_ratio)
        else:
            # SELL: start from ask, go down into spread
            price = best_ask - (spread * offset_ratio)

        return price

    def _estimate_fill_probability(self, spread_percent: float, liquidity_score: float) -> float:
        """
        Estimate probability of limit order being filled.

        Factors:
        - Tight spread → higher fill probability
        - High liquidity → higher fill probability
        """
        # Base probability from spread
        if spread_percent < 0.02:
            spread_factor = 0.95
        elif spread_percent < 0.05:
            spread_factor = 0.85
        elif spread_percent < 0.1:
            spread_factor = 0.75
        elif spread_percent < 0.2:
            spread_factor = 0.6
        else:
            spread_factor = 0.4

        # Combine with liquidity
        fill_probability = spread_factor * (0.5 + 0.5 * liquidity_score)

        return min(1.0, max(0.0, fill_probability))

    def _calculate_timeout(self, spread_percent: float) -> int:
        """
        Calculate recommended timeout based on spread.

        Wider spread = shorter timeout (market is less liquid/active)
        """
        if spread_percent < 0.02:
            return 60  # Very tight - can wait longer
        elif spread_percent < 0.05:
            return 45
        elif spread_percent < 0.1:
            return 30
        else:
            return 15  # Wide spread - don't wait long

    def _build_reason(
        self,
        spread_percent: float,
        liquidity_score: float,
        fill_probability: float,
        use_limit: bool,
    ) -> str:
        """Build explanation string."""
        if not use_limit:
            reasons = []
            if liquidity_score < self.min_liquidity_score:
                reasons.append(f"low liquidity ({liquidity_score:.2f})")
            if fill_probability < self.min_fill_probability:
                reasons.append(f"low fill prob ({fill_probability:.2f})")
            if spread_percent >= 0.5:
                reasons.append(f"wide spread ({spread_percent:.2f}%)")
            return f"Market order: {', '.join(reasons)}"
        else:
            return (
                f"Limit order: spread={spread_percent:.3f}%, "
                f"liq={liquidity_score:.2f}, fill_prob={fill_probability:.2f}"
            )

    def _market_fallback(self, price: float, reason: str) -> SmartEntryResult:
        """Return result indicating market order fallback."""
        return SmartEntryResult(
            recommended_price=price,
            spread_percent=0.0,
            spread_absolute=0.0,
            best_bid=price,
            best_ask=price,
            mid_price=price,
            liquidity_score=0.0,
            fill_probability=0.0,
            max_wait_seconds=0,
            use_limit=False,
            reason=f"Market fallback: {reason}",
        )
