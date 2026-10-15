"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : slippage_model.py
    Project     : Project-103
    Description : Slippage models for backtesting. Provides different slippage
                  calculation strategies: fixed, volume-based, and volatility-adjusted.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MarketState:
    """Current market state for slippage calculation."""

    symbol: str
    mid_price: float
    bid_price: float
    ask_price: float
    spread: float
    volume_24h: float
    volatility: float  # Recent volatility
    average_volume: float = 0.0


class SlippageModel(ABC):
    """Abstract base class for slippage models."""

    @abstractmethod
    def calculate(
        self, side: str, quantity: float, price: float, market_state: Optional[MarketState] = None
    ) -> float:
        """
        Calculate slippage for an order.

        Args:
            side: Order side ('BUY' or 'SELL')
            quantity: Order quantity
            price: Expected execution price
            market_state: Current market conditions

        Returns:
            Slippage amount (positive = worse execution)
        """
        pass


class FixedSlippage(SlippageModel):
    """
    Simple fixed percentage slippage model.

    Applies a constant slippage percentage regardless of market conditions.
    """

    def __init__(self, slippage_pct: float = 0.0001):
        """
        Initialize fixed slippage model.

        Args:
            slippage_pct: Fixed slippage as percentage of price (default 0.01%)
        """
        self.slippage_pct = slippage_pct

    def calculate(
        self, side: str, quantity: float, price: float, market_state: Optional[MarketState] = None
    ) -> float:
        """Calculate fixed slippage."""
        slippage = price * self.slippage_pct

        # Slippage is always unfavorable
        if side == "BUY":
            return slippage  # Pay more
        else:
            return -slippage  # Receive less


class AbsoluteSlippage(SlippageModel):
    """Slippage as a fixed amount IN PRICE, not a percentage.

    A broker spread is a dollar amount ($0.13 on XAUUSD+) and barely depends on
    the price level. A percentage model cannot express that: a fraction computed
    on gold at $2900 gives a 60% higher cost on gold at $4650, and over 16 years
    of GC history ($1046..$5623) it is off by a factor of five.

    Half the spread per side: entry at mid + half, exit at mid - half, i.e. the
    full spread per round trip.
    """

    def __init__(self, spread_usd: float):
        self.half = abs(spread_usd) / 2.0

    def calculate(
        self, side: str, quantity: float, price: float, market_state: Optional[MarketState] = None
    ) -> float:
        return self.half if side == "BUY" else -self.half


class VolumeBasedSlippage(SlippageModel):
    """
    Volume-based slippage model.

    Larger orders relative to average volume experience more slippage.
    """

    def __init__(
        self,
        base_slippage: float = 0.0001,
        volume_impact: float = 0.001,
        max_slippage: float = 0.01,
    ):
        """
        Initialize volume-based slippage model.

        Args:
            base_slippage: Base slippage percentage
            volume_impact: Additional slippage per % of average volume
            max_slippage: Maximum slippage cap
        """
        self.base_slippage = base_slippage
        self.volume_impact = volume_impact
        self.max_slippage = max_slippage

    def calculate(
        self, side: str, quantity: float, price: float, market_state: Optional[MarketState] = None
    ) -> float:
        """Calculate slippage based on order size relative to volume."""
        notional = quantity * price

        # Calculate volume ratio
        if market_state and market_state.average_volume > 0:
            volume_ratio = notional / market_state.average_volume
        else:
            volume_ratio = 0.01  # Default assumption

        # Calculate slippage percentage
        slippage_pct = self.base_slippage + (self.volume_impact * volume_ratio)
        slippage_pct = min(slippage_pct, self.max_slippage)

        slippage = price * slippage_pct

        if side == "BUY":
            return slippage
        else:
            return -slippage


class VolatilityAdjustedSlippage(SlippageModel):
    """
    Volatility-adjusted slippage model.

    Higher volatility leads to higher slippage.
    """

    def __init__(
        self,
        base_slippage: float = 0.0001,
        volatility_factor: float = 0.01,  # Reduced from 0.5
        volume_factor: float = 0.0001,  # Reduced from 0.001
        spread_factor: float = 0.5,
    ):
        """
        Initialize volatility-adjusted slippage model.

        Args:
            base_slippage: Base slippage percentage
            volatility_factor: Multiplier for volatility component
            volume_factor: Multiplier for volume component
            spread_factor: How much of spread contributes to slippage
        """
        self.base_slippage = base_slippage
        self.volatility_factor = volatility_factor
        self.volume_factor = volume_factor
        self.spread_factor = spread_factor

    def calculate(
        self, side: str, quantity: float, price: float, market_state: Optional[MarketState] = None
    ) -> float:
        """Calculate slippage adjusted for volatility and volume."""
        if market_state is None:
            # Fallback to fixed slippage
            slippage = price * self.base_slippage
        else:
            notional = quantity * price

            # Volume impact
            if market_state.average_volume > 0:
                volume_ratio = notional / market_state.average_volume
            else:
                volume_ratio = 0.01

            # Components
            base = self.base_slippage * price
            vol_component = self.volatility_factor * market_state.volatility * price
            volume_component = self.volume_factor * volume_ratio * price
            spread_component = self.spread_factor * market_state.spread

            slippage = base + vol_component + volume_component + spread_component

        if side == "BUY":
            return slippage
        else:
            return -slippage


class RealisticSlippage(SlippageModel):
    """
    Realistic slippage model simulating order book execution.

    Models market impact using Kyle's Lambda model and order book depth.
    """

    def __init__(
        self,
        lambda_factor: float = 0.1,
        permanent_impact: float = 0.5,
        temporary_impact: float = 0.5,
    ):
        """
        Initialize realistic slippage model.

        Args:
            lambda_factor: Kyle's Lambda (price impact per unit volume)
            permanent_impact: Fraction of impact that is permanent
            temporary_impact: Fraction of impact that is temporary
        """
        self.lambda_factor = lambda_factor
        self.permanent_impact = permanent_impact
        self.temporary_impact = temporary_impact

    def calculate(
        self, side: str, quantity: float, price: float, market_state: Optional[MarketState] = None
    ) -> float:
        """Calculate realistic slippage using market microstructure model."""
        notional = quantity * price

        if market_state is None:
            # Fallback
            return price * 0.0002 * (1 if side == "BUY" else -1)

        # Start with half spread
        slippage = market_state.spread / 2

        # Kyle's Lambda - price impact
        if market_state.average_volume > 0:
            normalized_volume = notional / market_state.average_volume
            market_impact = self.lambda_factor * np.sqrt(normalized_volume) * price
            slippage += market_impact

        # Volatility adjustment
        vol_adjustment = market_state.volatility * price * 0.1
        slippage += vol_adjustment

        if side == "BUY":
            return slippage
        else:
            return -slippage


class ZeroSlippage(SlippageModel):
    """Zero slippage model for ideal execution testing."""

    def calculate(
        self, side: str, quantity: float, price: float, market_state: Optional[MarketState] = None
    ) -> float:
        """Return zero slippage."""
        return 0.0


def create_slippage_model(model_type: str = "fixed", **kwargs) -> SlippageModel:
    """
    Factory function to create slippage models.

    Args:
        model_type: Type of model ('fixed', 'volume', 'volatility', 'realistic', 'zero')
        **kwargs: Model-specific parameters

    Returns:
        SlippageModel instance
    """
    if model_type == "fixed":
        return FixedSlippage(slippage_pct=kwargs.get("fixed_slippage", 0.0001))
    elif model_type == "volume":
        return VolumeBasedSlippage(
            base_slippage=kwargs.get("base_slippage", 0.0001),
            volume_impact=kwargs.get("volume_impact", 0.001),
            max_slippage=kwargs.get("max_slippage", 0.01),
        )
    elif model_type == "volatility":
        return VolatilityAdjustedSlippage(
            base_slippage=kwargs.get("base_slippage", 0.0001),
            volatility_factor=kwargs.get("volatility_factor", 0.5),
            volume_factor=kwargs.get("volume_factor", 0.001),
        )
    elif model_type == "realistic":
        return RealisticSlippage(lambda_factor=kwargs.get("lambda_factor", 0.1))
    elif model_type == "zero":
        return ZeroSlippage()
    else:
        raise ValueError(f"Unknown slippage model type: {model_type}")
