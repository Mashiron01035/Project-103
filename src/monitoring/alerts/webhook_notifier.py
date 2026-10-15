"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : webhook_notifier
    Project     : Project-103
    Description : Webhook notification module. Sends notifications via HTTP webhooks.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from typing import Any, Dict

import aiohttp

from src.monitoring.alerts.alert_manager import TelegramAlert
from src.utils.network import create_robust_session

logger = logging.getLogger(__name__)


class WebhookNotifier:
    """Webhook notification handler"""

    def __init__(self, webhook_url: str, headers: Dict[str, str] = None):
        """
        Initialize webhook notifier

        Args:
            webhook_url: Webhook URL
            headers: Optional HTTP headers
        """
        self.webhook_url = webhook_url
        self.headers = headers or {}

    async def send_alert(self, alert: TelegramAlert) -> bool:
        """
        Send alert via webhook

        Args:
            alert: Alert to send

        Returns:
            bool: Success status
        """
        try:
            payload = self._create_payload(alert)

            async with create_robust_session() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 200:
                        logger.info(f"Webhook alert sent: {alert.title}")
                        return True
                    else:
                        logger.error(f"Webhook failed with status {response.status}")
                        return False

        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            return False

    def _create_payload(self, alert: TelegramAlert) -> Dict[str, Any]:
        """Create webhook payload"""
        payload = {
            "type": alert.type,
            "priority": alert.priority,
            "title": alert.title,
            "message": alert.message,
            "data": alert.data or {},
            "timestamp": None,  # Could add timestamp
        }

        return payload
