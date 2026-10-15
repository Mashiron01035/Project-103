"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : __init__
    Project     : Project-103
    Description : Technical indicators module. Provides momentum, volume, trend, and volatility indicators for market analysis.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult, SignalType
from src.indicators.factory import IndicatorFactory, get_indicator_factory

# Momentum indicators
from src.indicators.momentum import MACD, RSI, AwesomeOscillator, Stochastic

# Trend indicators
from src.indicators.trend import EMA, SMA, BollingerBands

# Volatility indicators
from src.indicators.volatility import ATR, StandardDeviation

# Volume indicators
from src.indicators.volume import OBV, VWAP, VolumeProfile

__all__ = [
    # Base classes
    "BaseIndicator",
    "IndicatorResult",
    "Candle",
    "SignalType",
    # Factory
    "IndicatorFactory",
    "get_indicator_factory",
    # Momentum
    "RSI",
    "Stochastic",
    "MACD",
    "AwesomeOscillator",
    # Volume
    "VWAP",
    "VolumeProfile",
    "OBV",
    # Trend
    "EMA",
    "SMA",
    "BollingerBands",
    # Volatility
    "ATR",
    "StandardDeviation",
]
