"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : alert_manager
    Project     : Project-103
    Description : Alert management module. Manages and routes alerts to different notification channels.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertPriority(Enum):
    """Alert priority levels"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(Enum):
    """Alert types"""

    TRADE = "trade"
    SIGNAL = "signal"
    ERROR = "error"
    REPORT = "report"
    SYSTEM = "system"


@dataclass
class TelegramAlert:
    """Telegram alert data structure"""

    type: str  # "trade", "signal", "error", "report", "system"
    priority: str  # "low", "medium", "high", "critical"
    title: str
    message: str

    # Additional data
    data: Optional[Dict[str, Any]] = None

    # Formatting
    parse_mode: str = "Markdown"
    disable_notification: bool = False

    # Buttons
    buttons: Optional[List[List[Dict[str, str]]]] = None


class AlertManager:
    """Central alert manager"""

    def __init__(self):
        """Initialize alert manager"""
        self.notifiers = []
        self.alert_queue = asyncio.Queue()
        self.alert_history: List[TelegramAlert] = []
        self.rate_limits: Dict[str, List[float]] = {}

    def register_notifier(self, notifier):
        """Register a notification channel"""
        self.notifiers.append(notifier)
        logger.info(f"Registered notifier: {notifier.__class__.__name__}")

    async def send_alert(self, alert: TelegramAlert) -> bool:
        """
        Send alert to all registered notifiers

        Args:
            alert: Alert to send

        Returns:
            bool: Success status
        """
        # Check rate limiting
        if self._is_rate_limited(alert.type):
            logger.warning(f"Alert rate limited: {alert.type}")
            return False

        # Store in history
        self.alert_history.append(alert)
        if len(self.alert_history) > 1000:
            self.alert_history = self.alert_history[-1000:]

        # Send to all notifiers
        success = True
        for notifier in self.notifiers:
            try:
                await notifier.send_alert(alert)
            except Exception as e:
                logger.error(f"Failed to send alert via {notifier.__class__.__name__}: {e}")
                success = False

        return success

    def _is_rate_limited(self, alert_type: str, max_per_minute: int = 10) -> bool:
        """Check if alert type is rate limited"""
        import time

        now = time.time()
        minute_ago = now - 60

        # Clean old entries
        if alert_type in self.rate_limits:
            self.rate_limits[alert_type] = [
                t for t in self.rate_limits[alert_type] if t > minute_ago
            ]
        else:
            self.rate_limits[alert_type] = []

        # Check limit
        if len(self.rate_limits[alert_type]) >= max_per_minute:
            return True

        # Add current
        self.rate_limits[alert_type].append(now)
        return False

    async def process_queue(self):
        """Process alert queue"""
        while True:
            try:
                alert = await self.alert_queue.get()
                await self.send_alert(alert)
                await asyncio.sleep(0.5)  # Rate limiting
            except Exception as e:
                logger.error(f"Error processing alert queue: {e}")
