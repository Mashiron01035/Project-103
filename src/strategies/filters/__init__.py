"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : __init__
    Project     : Project-103
    Description : Filters module. Provides various filters for validating trading signals.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.strategies.filters.base_filter import BaseFilter, FilterResult
from src.strategies.filters.time_filter import TimeFilter
from src.strategies.filters.trend_filter import TrendFilter
from src.strategies.filters.volatility_filter import VolatilityFilter
from src.strategies.filters.volume_filter import VolumeFilter

__all__ = [
    "BaseFilter",
    "FilterResult",
    "TimeFilter",
    "VolatilityFilter",
    "VolumeFilter",
    "TrendFilter",
]
