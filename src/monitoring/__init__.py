"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : __init__
    Project     : Project-103
    Description : Monitoring module initialization. This module provides performance tracking, metrics calculation, logging, health checks, alerts, and web dashboard.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.monitoring.health_checker import ComponentHealth, HealthChecker, SystemHealth
from src.monitoring.logger import StructuredLogger, TradingLogger, generate_session_id
from src.monitoring.metrics_calculator import MetricsCalculator
from src.monitoring.performance_tracker import (
    PerformanceEvent,
    PerformanceMetrics,
    PerformanceSnapshot,
    PerformanceTracker,
)

__all__ = [
    "PerformanceTracker",
    "PerformanceEvent",
    "PerformanceSnapshot",
    "PerformanceMetrics",
    "MetricsCalculator",
    "TradingLogger",
    "StructuredLogger",
    "generate_session_id",
    "HealthChecker",
    "ComponentHealth",
    "SystemHealth",
]

__version__ = "1.0.0"
