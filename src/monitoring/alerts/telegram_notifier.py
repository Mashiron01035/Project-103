"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : telegram_notifier
    Project     : Project-103
    Description : Telegram notification module. Sends notifications via Telegram bot.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from typing import Any, Dict, List

from src.models.position import Position
from src.models.trade import Trade
from src.monitoring.alerts.alert_manager import TelegramAlert
from src.monitoring.performance_tracker import PerformanceSnapshot

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram notification handler"""

    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize Telegram notifier

        Args:
            bot_token: Telegram bot token
            chat_id: Chat ID to send messages to
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot = None
        self.message_queue = asyncio.Queue()

        # Emoji mappings
        self.emojis = {
            "trade_open": "📈",
            "trade_close": "💰",
            "signal": "🔔",
            "error": "❌",
            "warning": "⚠️",
            "info": "ℹ️",
            "report": "📊",
            "system": "🤖",
        }

        self._initialize_bot()

    def _initialize_bot(self):
        """Initialize Telegram bot"""
        try:
            from telegram import Bot

            self.bot = Bot(token=self.bot_token)
            logger.info("Telegram bot initialized successfully")
        except ImportError:
            logger.warning(
                "python-telegram-bot not installed. Install with: pip install python-telegram-bot"
            )
        except Exception as e:
            logger.error(f"Failed to initialize Telegram bot: {e}")

    async def send_alert(self, alert: TelegramAlert) -> bool:
        """
        Send alert via Telegram

        Args:
            alert: Alert to send

        Returns:
            bool: Success status
        """
        if not self.bot:
            logger.warning("Telegram bot not available")
            return False

        try:
            # Format message
            formatted_message = self._format_message(alert)

            # Create keyboard if buttons provided
            reply_markup = None
            if alert.buttons:
                reply_markup = self._create_keyboard(alert.buttons)

            # Send message
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=formatted_message,
                parse_mode=alert.parse_mode,
                disable_notification=alert.disable_notification,
                reply_markup=reply_markup,
            )

            return True

        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False

    def _format_message(self, alert: TelegramAlert) -> str:
        """Format alert message"""
        # Select emoji
        emoji = self.emojis.get(alert.type, "📌")

        # Format title
        if alert.priority == "critical":
            title = f"🚨 *{alert.title.upper()}* 🚨"
        elif alert.priority == "high":
            title = f"⚠️ *{alert.title}*"
        else:
            title = f"{emoji} *{alert.title}*"

        # Build message
        message = f"{title}\n\n{alert.message}"

        # Add data if present
        if alert.data:
            message += "\n\n" + self._format_data(alert.data)

        return message

    def _format_data(self, data: Dict[str, Any]) -> str:
        """Format additional data"""
        lines = []

        for key, value in data.items():
            # Format key
            formatted_key = key.replace("_", " ").title()

            # Format value
            if isinstance(value, float):
                if "percent" in key.lower():
                    formatted_value = f"{value:.2f}%"
                elif "price" in key.lower():
                    formatted_value = f"${value:.2f}"
                else:
                    formatted_value = f"{value:.2f}"
            else:
                formatted_value = str(value)

            lines.append(f"*{formatted_key}:* {formatted_value}")

        return "\n".join(lines)

    def _create_keyboard(self, buttons: List[List[Dict[str, str]]]):
        """Create inline keyboard"""
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = []
            for row in buttons:
                keyboard_row = []
                for button in row:
                    keyboard_row.append(
                        InlineKeyboardButton(
                            text=button.get("text", "Button"),
                            callback_data=button.get("callback_data"),
                            url=button.get("url"),
                        )
                    )
                keyboard.append(keyboard_row)

            return InlineKeyboardMarkup(keyboard)
        except ImportError:
            logger.warning("Cannot create keyboard: python-telegram-bot not installed")
            return None

    async def send_trade_opened(self, position: Position):
        """Send trade opened notification"""
        alert = TelegramAlert(
            type="trade_open",
            priority="medium",
            title=f"Position Opened: {position.symbol}",
            message=f"Opened {position.side.value} position",
            data={
                "Entry Price": position.entry_price,
                "Quantity": position.quantity,
                "Stop Loss": position.stop_loss,
                "Take Profit": position.take_profit[0] if position.take_profit else None,
                "Strategy": position.strategy_name,
            },
        )

        await self.send_alert(alert)

    async def send_trade_closed(self, trade: Trade):
        """Send trade closed notification"""
        # Determine priority by P&L
        if abs(trade.pnl) > 100:
            priority = "high"
        else:
            priority = "medium"

        # Result emoji
        result_emoji = "✅" if trade.is_profitable else "❌"

        alert = TelegramAlert(
            type="trade_close",
            priority=priority,
            title=f"{result_emoji} Position Closed: {trade.symbol}",
            message=f"Closed {trade.side} position",
            data={
                "Entry": trade.entry_price,
                "Exit": trade.exit_price,
                "P&L": trade.pnl,
                "P&L Percent": trade.pnl_percentage,
                "Holding Time": f"{trade.hold_time / 3600:.1f} hours" if trade.hold_time else "N/A",
                "Exit Reason": trade.exit_reason,
            },
        )

        await self.send_alert(alert)

    async def send_daily_report(self, snapshot: PerformanceSnapshot):
        """Send daily performance report"""
        # Determine emoji by result
        if snapshot.net_pnl > 0:
            result_emoji = "📈"
            result_text = "Profitable Day"
        elif snapshot.net_pnl < 0:
            result_emoji = "📉"
            result_text = "Loss Day"
        else:
            result_emoji = "➖"
            result_text = "Break Even"

        alert = TelegramAlert(
            type="report",
            priority="low",
            title=f"{result_emoji} Daily Report",
            message=result_text,
            data={
                "Total Trades": snapshot.total_trades,
                "Win Rate": f"{snapshot.win_rate:.1f}%",
                "Net P&L": snapshot.net_pnl,
                "ROI": f"{snapshot.roi:.2f}%",
                "Max Drawdown": f"{snapshot.max_drawdown:.2f}%",
                "Sharpe Ratio": f"{snapshot.sharpe_ratio:.2f}",
            },
            disable_notification=True,
        )

        await self.send_alert(alert)

    async def send_error_alert(self, error: Exception, context: str = ""):
        """Send error notification"""
        alert = TelegramAlert(
            type="error",
            priority="high",
            title="System Error",
            message=f"Error in {context}" if context else "System error occurred",
            data={"Error Type": type(error).__name__, "Message": str(error)[:200]},
        )

        await self.send_alert(alert)

    async def process_queue(self):
        """Process message queue"""
        while True:
            try:
                alert = await self.message_queue.get()
                await self.send_alert(alert)
                await asyncio.sleep(0.5)  # Rate limiting
            except Exception as e:
                logger.error(f"Error processing Telegram queue: {e}")
