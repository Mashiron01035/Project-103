"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : __init__
    Project     : Project-103
    Description : Data collectors module. This module contains data collection components.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.data.collectors.aggregator import DataAggregator
from src.data.collectors.rest_collector import RestCollector
from src.data.collectors.websocket_collector import WebSocketCollector

__all__ = [
    "WebSocketCollector",
    "RestCollector",
    "DataAggregator",
]
