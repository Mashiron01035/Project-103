"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : __init__.py
    Project     : Project-103
    Description : Signal generation and management module. This module handles signal generation, validation, aggregation, and history tracking.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.signals.aggregator import SignalAggregator
from src.signals.generator import SignalGenerator
from src.signals.history import SignalHistory
from src.signals.inverter import BTCTrend, InversionMode, InversionResult, SignalInverter
from src.signals.validator import SignalValidator

__all__ = [
    "SignalGenerator",
    "SignalValidator",
    "SignalAggregator",
    "SignalHistory",
    "SignalInverter",
    "InversionResult",
    "BTCTrend",
    "InversionMode",
]
