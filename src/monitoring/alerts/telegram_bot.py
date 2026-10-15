"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 04.12.2025.
    File        : telegram_bot
    Project     : Project-103
    Description : Telegram bot for trading system control.
                  Provides commands to manage trading, strategies, and monitor status.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Optional

from src.utils.datetime_utils import utcnow

logger = logging.getLogger(__name__)


# ==================== Retry Decorator for Telegram API ====================


def telegram_retry(max_retries: int = 3, base_delay: float = 1.0):
    """
    Decorator for retrying Telegram API calls with exponential backoff.
    Handles common transient errors like BadGateway, NetworkError, TimedOut.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)

                except Exception as e:
                    error_name = type(e).__name__
                    error_str = str(e).lower()

                    # Check if it's a retryable error
                    retryable_errors = [
                        "bad gateway",
                        "badgateway",
                        "502",
                        "networkerror",
                        "timedout",
                        "timeout",
                        "connection",
                        "temporarily unavailable",
                        "service unavailable",
                        "503",
                        "504",
                        "retry after",
                        "flood",
                    ]

                    is_retryable = any(err in error_str for err in retryable_errors)

                    if not is_retryable:
                        # Non-retryable error - raise immediately
                        raise

                    last_exception = e
                    delay = base_delay * (2**attempt)  # Exponential backoff

                    # Check for Retry-After header in flood control
                    if "retry after" in error_str:
                        try:
                            # Extract retry_after seconds from error message
                            import re

                            match = re.search(r"retry after (\d+)", error_str)
                            if match:
                                delay = max(delay, int(match.group(1)) + 1)
                        except Exception:
                            pass

                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Telegram API error ({error_name}): {e}. "
                            f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"Telegram API error ({error_name}): {e}. "
                            f"All {max_retries} retries exhausted."
                        )

            # If we've exhausted all retries, raise the last exception
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    # Characters that need escaping in MarkdownV2
    special_chars = [
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ]
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text


class TelegramControlBot:
    """
    Telegram bot for controlling the trading system.

    Commands:
        /start      - Welcome message and command list
        /help       - Show all available commands
        /status     - Current bot status (trading state, positions, P&L)
        /enable     - Enable trading
        /disable    - Disable trading
        /stop       - Emergency stop (close all positions)
        /strategies - List all strategies and their status
        /balance    - Show current balance and P&L
        /positions  - Show open positions
        /trades     - Show recent trades
        /reload     - Reload configuration
    """

    def __init__(
        self,
        bot_token: str,
        allowed_users: List[int],
        trading_controller=None,
        performance_tracker=None,
    ):
        """
        Initialize Telegram control bot.

        Args:
            bot_token: Telegram bot token from @BotFather
            allowed_users: List of Telegram user IDs allowed to use the bot
            trading_controller: TradingController instance
            performance_tracker: PerformanceTracker instance
        """
        self.bot_token = bot_token
        self.allowed_users = allowed_users
        self.trading_controller = trading_controller
        self.performance_tracker = performance_tracker
        self.signal_lifecycle = None  # Set externally for leverage control
        self.database = None  # Set externally for stats

        self.application = None
        self._running = False
        self.notifications_muted = False  # Mute signal/trade notifications

        # Signal message tracking (for editing messages)
        # {signal_id: {'message_id': int, 'chat_id': int, 'data': dict}}
        self.signal_messages: Dict[str, Dict[str, Any]] = {}

        # Default chat for notifications (first allowed user)
        self.notification_chat_id: Optional[int] = allowed_users[0] if allowed_users else None

        self.account_manager = None

    async def start(self):
        """Start the Telegram bot."""
        try:
            from telegram import BotCommand
            from telegram.ext import Application, CallbackQueryHandler, CommandHandler

            # Reduce noise from telegram/httpx libraries
            logging.getLogger("telegram").setLevel(logging.WARNING)
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)

            # Build application
            self.application = Application.builder().token(self.bot_token).build()

            # Register command handlers
            self.application.add_handler(CommandHandler("start", self._cmd_start))
            self.application.add_handler(CommandHandler("help", self._cmd_help))
            self.application.add_handler(CommandHandler("status", self._cmd_status))
            self.application.add_handler(
                CommandHandler("trading", self._cmd_trading)
            )  # Toggle trading
            self.application.add_handler(CommandHandler("stop", self._cmd_emergency_stop))
            self.application.add_handler(CommandHandler("strategies", self._cmd_strategies))
            self.application.add_handler(CommandHandler("balance", self._cmd_balance))
            self.application.add_handler(CommandHandler("positions", self._cmd_positions))
            self.application.add_handler(CommandHandler("trades", self._cmd_trades))
            self.application.add_handler(CommandHandler("reload", self._cmd_reload))
            self.application.add_handler(CommandHandler("mute", self._cmd_mute))  # Toggle mute
            self.application.add_handler(CommandHandler("leverage", self._cmd_leverage))
            self.application.add_handler(CommandHandler("accounts", self._cmd_accounts))
            self.application.add_handler(CommandHandler("account", self._cmd_account))
            self.application.add_handler(CommandHandler("hedge", self._cmd_hedge))
            self.application.add_handler(CommandHandler("stats", self._cmd_stats))

            # Inline button callbacks
            self.application.add_handler(CallbackQueryHandler(self._handle_button_callback))

            # Set bot commands for menu
            commands = [
                BotCommand("status", "System status"),
                BotCommand("balance", "Balance and P&L"),
                BotCommand("positions", "Open positions"),
                BotCommand("trades", "Recent trades"),
                BotCommand("strategies", "Strategies and results"),
                BotCommand("stats", "Statistics from the database"),
                BotCommand("trading", "Enable/disable new entries"),
                BotCommand("stop", "Emergency stop"),
                BotCommand("mute", "Notifications"),
                BotCommand("leverage", "Leverage used for sizing"),
                BotCommand("hedge", "Bot hedge logic"),
                BotCommand("accounts", "All accounts"),
                BotCommand("account", "One account"),
                BotCommand("reload", "Compare config with disk"),
                BotCommand("help", "Help"),
            ]

            await self.application.initialize()
            await self.application.bot.set_my_commands(commands)
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)

            self._running = True
            logger.info("Telegram control bot started")

        except ImportError:
            logger.error(
                "python-telegram-bot not installed. Install with: pip install python-telegram-bot"
            )
            raise
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {e}")
            raise

    async def stop(self):
        """Stop the Telegram bot."""
        if self.application and self._running:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            self._running = False
            logger.info("Telegram control bot stopped")

    @staticmethod
    def _esc(text: str) -> str:
        """Escape characters that break Telegram Markdown.

        Strategy names are full of underscores (session_breakout), symbols may
        contain dots and pluses (XAUUSD+). One unescaped character turns the whole
        message into a send error, and the command silently does not reply — it
        used to be patched with a one-off replace in one place out of ten.

        Escapes the set for legacy Markdown, which the bot uses; the module-level
        escape_markdown() targets MarkdownV2 and would break the markup here by
        adding backslashes to dots and minus signs.
        """
        out = str(text)
        for ch in ("_", "*", "`", "["):
            out = out.replace(ch, "\\" + ch)
        return out

    @staticmethod
    def _fmt_price(price: float) -> str:
        """Price with a precision that suits the instrument.

        A fixed two decimals would break EURUSD (1.08), a fixed four gives gold
        "4017.8989" next to "4023.35" — mixed precision in one message looks like
        a typo. A magnitude threshold covers metals, indices and currency pairs
        alike.
        """
        return f"{price:.2f}" if abs(price) >= 100 else f"{price:.5f}"

    @staticmethod
    def _fmt_lots(qty: float) -> str:
        """0.43 lots, 1 lot, 2 lots."""
        num = f"{qty:g}"
        word = "lot" if qty == 1 else "lots"
        return f"{num} {word}"

    @staticmethod
    def _pick_balance(balances: dict) -> tuple:
        """Get the balance without knowing the currency code in advance.

        The MT5 connector returns {account_currency: amount, '<cur>_free': ..., ...},
        so the key depends on the account: USD, EUR, UST. A hardcoded 'USDT'
        (a Bybit legacy) gave $0.00 on any MT5 account.
        """
        if not isinstance(balances, dict) or not balances:
            return 0.0, ""
        for key in ("USDT", "USD"):
            if isinstance(balances.get(key), (int, float)):
                return float(balances[key]), key
        for key, val in balances.items():
            if isinstance(val, (int, float)) and not str(key).endswith(("_free", "_margin")):
                return float(val), str(key)
        return 0.0, ""

    def _is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized."""
        return user_id in self.allowed_users

    async def _unauthorized_response(self, update, context):
        """Send unauthorized response."""
        await update.message.reply_text(
            "⛔ Access denied.\n"
            "Your user ID is not in the allowed list.\n"
            f"Your ID: `{update.effective_user.id}`",
            parse_mode="Markdown",
        )

    # ==================== Command Handlers ====================

    async def _cmd_start(self, update, context):
        """Handle /start command."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        welcome_text = """
🤖 *Trading Bot Control Panel*

Welcome! Use this bot to control your trading system.

*Quick Commands:*
• /status - Check current status
• /trading - Toggle trading on/off
• /balance - View balance & P&L
• /stop - Emergency stop

Type /help for full command list.
"""
        await update.message.reply_text(welcome_text, parse_mode="Markdown")

    async def _cmd_help(self, update, context):
        """Handle /help command."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        # The help lists exactly what is registered in start(), and honestly
        # marks commands whose effect does not survive a restart.
        help_text = """
📚 *Commands*

*Monitoring*
/status — system status
/balance — balance and P&L
/positions — open positions
/trades \\[N] — recent trades (default 10)
/strategies — strategies and their results
/stats — statistics from the database

*Trading control*
/trading \\[on|off] — enable/disable entering new trades
/stop \\[reason] — emergency stop
/mute \\[on|off] — notifications

*Runtime settings* (until restart)
/leverage \\[N] — leverage used for position sizing
/hedge \\[on|off] — hedge logic inside the bot

*Accounts*
/accounts — all accounts
/account <id> — one account

*Other*
/reload — compare the config on disk with the current state
/start, /help

_Changes to strategies.yaml take effect only after a bot restart._
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")

    async def _cmd_status(self, update, context):
        """Handle /status command."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.trading_controller:
            await update.message.reply_text("❌ Trading controller not connected")
            return

        status = self.trading_controller.get_status()

        # Trading state emoji
        state = status.get("trading_state", "unknown")
        state_emoji = {"enabled": "🟢", "disabled": "🔴", "paused": "⏸️", "emergency": "🚨"}.get(
            state, "❓"
        )

        # Get leverage and hedge mode
        leverage = 1
        hedge_mode = False
        if self.signal_lifecycle:
            leverage = self.signal_lifecycle.config.leverage
            hedge_mode = self.signal_lifecycle.config.hedge_mode

        hedge_indicator = "🔀 ON" if hedge_mode else "OFF"

        # Escape underscores for Markdown (strategy names like smart_money_confluence)
        strategies_list = status.get("active_strategies", [])
        strategies_str = ", ".join(s.replace("_", "\\_") for s in strategies_list) or "None"

        pairs_list = status.get("active_pairs", [])
        pairs_str = ", ".join(pairs_list) or "None"

        # Format status message
        status_text = f"""
{state_emoji} *Trading Status: {state.upper()}*

*Active Strategies:* {len(strategies_list)}
{strategies_str}

*Active Pairs:* {len(pairs_list)}
{pairs_str}

*Mode:* {status.get('mode', 'unknown')}
*Exchange:* {status.get('exchange', 'unknown')}
*Leverage:* {leverage}x | *Hedge:* {hedge_indicator}

*Stats:*
• Signals: {status.get('total_signals_generated', 0)}
• Trades: {status.get('total_trades_executed', 0)}
• Emergency stops: {status.get('emergency_stops', 0)}
"""
        await update.message.reply_text(status_text, parse_mode="Markdown")

    async def _cmd_trading(self, update, context):
        """Handle /trading command - toggle trading on/off."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.trading_controller:
            await update.message.reply_text("❌ Trading controller not connected")
            return

        # Get current state
        status = self.trading_controller.get_status()
        current_state = status.get("trading_state", "disabled")
        is_enabled = current_state == "enabled"

        # If argument provided, use it
        if context.args:
            arg = context.args[0].lower()
            if arg in ("on", "true", "1", "enable"):
                should_enable = True
            elif arg in ("off", "false", "0", "disable"):
                should_enable = False
            else:
                await update.message.reply_text(
                    "❌ Invalid argument. Use: /trading on or /trading off"
                )
                return
        else:
            # Toggle
            should_enable = not is_enabled

        if should_enable:
            result = await self.trading_controller.enable_trading()
            if result.get("success"):
                await update.message.reply_text(
                    "🟢 *Trading ENABLED*\n\n" "Bot will now process signals and open positions.",
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    f"❌ Failed to enable trading\n{result.get('error', 'Unknown error')}"
                )
        else:
            reason = (
                " ".join(context.args[1:]) if len(context.args) > 1 else "Disabled via Telegram"
            )
            result = await self.trading_controller.disable_trading(reason)
            if result.get("success"):
                await update.message.reply_text(
                    "🔴 *Trading DISABLED*\n\n" f"Reason: {reason}", parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    f"❌ Failed to disable trading\n{result.get('error', 'Unknown error')}"
                )

    async def _cmd_emergency_stop(self, update, context):
        """Handle /stop command (emergency stop)."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.trading_controller:
            await update.message.reply_text("❌ Trading controller not connected")
            return

        reason = " ".join(context.args) if context.args else "Emergency stop via Telegram"
        result = await self.trading_controller.emergency_stop(reason)

        if not result.get("success"):
            await update.message.reply_text(
                f"❌ Emergency stop NOT executed\n"
                f"{result.get('error', 'unknown error')}\n\n"
                "Check positions at the broker manually."
            )
            return

        actions = result.get("actions_taken", [])
        # A partial failure must stand out: a cheerful "EXECUTED" used to arrive
        # here regardless of whether anything was closed.
        problems = [a for a in actions if "NOT CLOSED" in a or "⚠️" in a]
        head = (
            "🚨 *EMERGENCY STOP*" if not problems else "🚨 *STOP WITH ERRORS — INTERVENTION NEEDED*"
        )
        body = "\n".join(f"• {self._esc(a)}" for a in actions) or "• no action was required"
        text = f"{head}\n\nReason: {self._esc(reason)}\n\n{body}\n\n" "New entries are blocked."
        if problems:
            text += "\n\n*Some positions are still open — close them at the broker.*"
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_strategies(self, update, context):
        """Handle /strategies command."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.trading_controller:
            await update.message.reply_text("❌ Trading controller not connected")
            return

        result = self.trading_controller.list_strategies()
        strategies = result.get("strategies", [])

        if not strategies:
            await update.message.reply_text("No strategies registered")
            return

        # StrategyManager's Win% and P&L are filled by record_signal_execution,
        # which is NEVER called in the project — they are always zero. The real
        # numbers are kept by SignalLifecycleManager in strategy_balances.
        live = {}
        if self.signal_lifecycle:
            try:
                live = self.signal_lifecycle.get_strategy_stats()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"/strategies: no lifecycle statistics: {exc}")

        text = f"📊 *Strategies* " f"({result.get('active', 0)}/{result.get('total', 0)} active)\n\n"

        for s in strategies:
            name = s.get("name", "?")
            emoji = "✅" if s.get("enabled") else "⛔"
            st = live.get(name) or {}
            trades = st.get("trades", 0)
            text += f"{emoji} *{self._esc(name)}*\n   signals {s.get('total_signals', 0)}"
            if trades:
                text += (
                    f" | trades {trades}"
                    f" | WR {st.get('win_rate', 0) * 100:.0f}%"
                    f" | {st.get('pnl', 0):+,.2f}$"
                )
            elif live:
                text += " | no trades"
            text += "\n"

        if not live:
            text += "\n_trade statistics unavailable: lifecycle not connected_"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_balance(self, update, context):
        """Handle /balance command."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.signal_lifecycle:
            await update.message.reply_text("❌ Signal lifecycle not connected")
            return

        try:
            stats = self.signal_lifecycle.get_stats()

            initial_balance = stats.get("initial_balance", 10000)
            current_balance = stats.get("current_balance", 10000)
            total_pnl_usdt = stats.get("total_pnl_usdt", 0)
            max_balance = stats.get("max_balance", initial_balance)
            max_drawdown_pct = stats.get("max_drawdown_percent", 0)

            # Calculate ROI
            roi = (
                ((current_balance - initial_balance) / initial_balance * 100)
                if initial_balance > 0
                else 0
            )

            # P&L emoji
            pnl_emoji = "📈" if total_pnl_usdt >= 0 else "📉"

            # Get trade stats
            signals_closed = stats.get("signals_closed", 0)
            winners = stats.get("winners", 0)
            losers = stats.get("losers", 0)
            win_rate = stats.get("win_rate", 0) * 100
            active_signals = stats.get("active_signals", 0)

            text = f"""
💰 *Balance & P&L*

*Initial:* ${initial_balance:,.2f}
*Current:* ${current_balance:,.2f}
*Peak:* ${max_balance:,.2f}

{pnl_emoji} *Total P&L:* ${total_pnl_usdt:+,.2f} ({roi:+.2f}%)

*Statistics:*
• Open positions: {active_signals}
• Closed trades: {signals_closed}
• Winners: {winners} | Losers: {losers}
• Win Rate: {win_rate:.1f}%
• Max Drawdown: {max_drawdown_pct:.2f}%
"""
            await update.message.reply_text(text, parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ Error getting balance: {e}")

    async def _cmd_positions(self, update, context):
        """Handle /positions command.

        Source: SignalLifecycleManager, not PerformanceTracker.
        PerformanceTracker is only filled via track_event(), which is never called
        on the live path (the only calls are in monitoring/example_usage.py).
        So the command used to always answer "no open positions", even when
        there were positions.
        """
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.signal_lifecycle:
            await update.message.reply_text("❌ Signal lifecycle not connected")
            return

        try:
            open_signals = [
                s for s in self.signal_lifecycle.tracked_signals.values() if s.status == "EXECUTED"
            ]
            if not open_signals:
                await update.message.reply_text("📭 No open positions")
                return

            open_signals.sort(key=lambda s: s.executed_at or s.created_at)
            text = f"📊 *Open positions* ({len(open_signals)})\n\n"

            for s in open_signals:
                side_emoji = "🟢" if s.side.upper() == "BUY" else "🔴"
                entry = s.executed_price or s.entry_price
                tp = s.take_profit[0] if s.take_profit else None
                held = ""
                if s.executed_at:
                    mins = (
                        datetime.now(timezone.utc) - s.executed_at.replace(tzinfo=timezone.utc)
                    ).total_seconds() / 60
                    held = f" | {mins / 60:.1f}h" if mins >= 60 else f" | {mins:.0f}m"

                text += (
                    f"{side_emoji} *{self._esc(s.symbol)}* {s.side}"
                    f"{' · ' + self._esc(s.strategy_name) if s.strategy_name else ''}\n"
                    f"   entry {entry:,.2f}"
                    f" | size {s.position_size_usdt:,.2f}$"
                    f"{held}\n"
                    f"   SL {s.stop_loss:,.2f}"
                    f" | TP {f'{tp:,.2f}' if tp else '—'}"
                    f"{f' | remaining {s.remaining_percent:.0f}%' if s.remaining_percent < 100 else ''}\n"
                )
                if s.realized_pnl_usdt:
                    text += f"   partially realized {s.realized_pnl_usdt:+,.2f}$\n"
                text += "\n"

            await update.message.reply_text(text, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"/positions failed: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_trades(self, update, context):
        """Handle /trades command. Same source — see _cmd_positions."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.signal_lifecycle:
            await update.message.reply_text("❌ Signal lifecycle not connected")
            return

        try:
            limit = 10
            if context.args:
                try:
                    limit = max(1, min(30, int(context.args[0])))
                except ValueError:
                    pass

            closed = self.signal_lifecycle.get_closed_positions(limit)
            if not closed:
                await update.message.reply_text("📭 No closed trades yet")
                return

            text = f"📜 *Recent trades* ({len(closed)})\n\n"
            total = 0.0
            for s in closed:
                # net_pnl includes fees, exchange_pnl does not; take the best available
                # so the number matches the broker statement
                pnl = (
                    s.net_pnl
                    if s.net_pnl is not None
                    else (s.exchange_pnl if s.exchange_pnl is not None else s.pnl)
                )
                pnl = pnl or 0.0
                total += pnl
                emoji = "✅" if pnl > 0 else ("❌" if pnl < 0 else "➖")
                when = s.closed_at.strftime("%d.%m %H:%M") if s.closed_at else "—"
                exit_px = s.exit_price or 0.0
                entry_px = s.executed_price or s.entry_price
                text += (
                    f"{emoji} *{self._esc(s.symbol)}* {s.side} · {when}\n"
                    f"   {pnl:+,.2f}$"
                    f"{f' ({s.pnl_percent:+.2f}%)' if s.pnl_percent is not None else ''}"
                    f" | {entry_px:,.2f} → {exit_px:,.2f}\n"
                    f"   {self._esc(str(s.exit_reason or '—'))}"
                    f"{' · ' + self._esc(s.strategy_name) if s.strategy_name else ''}\n\n"
                )

            text += f"━━━━━━━━━━\n*Total for shown:* {total:+,.2f}$"
            await update.message.reply_text(text, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"/trades failed: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_reload(self, update, context):
        """Handle /reload command."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.trading_controller:
            await update.message.reply_text("❌ Trading controller not connected")
            return

        # HONESTLY ABOUT WHAT THIS DOES.
        # TradingController.reload_config() reads config.yaml and COMPARES it with
        # the current state, but applies nothing: strategies are not recreated and
        # their strategies.yaml parameters are not re-read (the code says so —
        # "Full implementation would update subscriptions"). Trading is paused and
        # resumed in the process. The old reply "✅ Configuration Reloaded" made
        # people believe their changes were applied — the most harmful thing it
        # could say.
        await update.message.reply_text("🔄 Comparing the config with the current state…")

        result = await self.trading_controller.reload_config()

        if not result.get("success"):
            await update.message.reply_text(
                f"❌ Could not read the config\n{result.get('error', 'unknown error')}"
            )
            return

        changes = result.get("changes", [])
        if changes:
            body = "\n".join(f"• {self._esc(c)}" for c in changes)
            await update.message.reply_text(
                "⚠️ *Differences from the config on disk*\n\n"
                f"{body}\n\n"
                "_Changes were NOT applied._ The command only compares: strategies "
                "and their parameters are re-read only on a bot restart.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "✅ The config on disk matches the current state\n\n"
                "_Reminder: changes to strategies.yaml take effect only after a "
                "restart._",
                parse_mode="Markdown",
            )

    async def _cmd_mute(self, update, context):
        """Handle /mute command - toggle signal notifications."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        # If argument provided, use it
        if context.args:
            arg = context.args[0].lower()
            if arg in ("on", "true", "1"):
                self.notifications_muted = True
            elif arg in ("off", "false", "0"):
                self.notifications_muted = False
            else:
                await update.message.reply_text("❌ Invalid argument. Use: /mute on or /mute off")
                return
        else:
            # Toggle
            self.notifications_muted = not self.notifications_muted

        if self.notifications_muted:
            await update.message.reply_text(
                "🔇 *Notifications MUTED*\n\n"
                "Signal and trade notifications are disabled.\n"
                "Use /mute to toggle back on.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "🔔 *Notifications ENABLED*\n\n" "You will receive signal and trade notifications.",
                parse_mode="Markdown",
            )

    async def _cmd_leverage(self, update, context):
        """Handle /leverage command - view or set leverage."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.signal_lifecycle:
            await update.message.reply_text("❌ Signal lifecycle not connected")
            return

        current_leverage = self.signal_lifecycle.config.leverage

        # The upper bound comes from the config instead of a hardcoded number: the
        # old 1-100 range contradicted the working leverage of 500 — the command
        # showed "Current: 500x, valid range: 1-100" and refused to set 500 back.
        max_lev = int(
            getattr(self.signal_lifecycle.config, "max_leverage", 0) or max(current_leverage, 500)
        )

        if not context.args:
            await update.message.reply_text(
                f"⚡ *Leverage: {current_leverage}x*\n\n"
                f"Change: /leverage 100   (allowed 1-{max_lev})\n\n"
                "_Affects the bot's position sizing. The broker leverage is not "
                "changed, and the value does not survive a restart._",
                parse_mode="Markdown",
            )
            return

        try:
            new_leverage = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ A number is required, e.g. /leverage 100")
            return

        if new_leverage < 1 or new_leverage > max_lev:
            await update.message.reply_text(f"❌ Leverage must be between 1 and {max_lev}")
            return

        self.signal_lifecycle.config.leverage = new_leverage
        await update.message.reply_text(
            f"✅ *Leverage: {current_leverage}x → {new_leverage}x*\n\n"
            "Changes the size of NEW positions. Open ones are not affected; "
            "after a restart the value from config.yaml is restored.",
            parse_mode="Markdown",
        )

    async def _cmd_hedge(self, update, context):
        """Handle /hedge command - toggle hedge mode."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.signal_lifecycle:
            await update.message.reply_text("❌ Signal lifecycle not connected")
            return

        current_mode = self.signal_lifecycle.config.hedge_mode

        # If argument provided, set to on/off
        if context.args:
            arg = context.args[0].lower()
            if arg in ("on", "true", "1", "enable"):
                new_mode = True
            elif arg in ("off", "false", "0", "disable"):
                new_mode = False
            else:
                await update.message.reply_text("❌ Invalid argument. Use: /hedge on or /hedge off")
                return
        else:
            # Toggle
            new_mode = not current_mode

        self.signal_lifecycle.config.hedge_mode = new_mode

        # This is a BOT LOGIC flag, not the account mode: hedging/netting is set by
        # the broker when the account is opened and cannot be switched by a command.
        tail = (
            "\n\n_Internal bot logic. The broker account mode does not change; "
            "after a restart the value from config.yaml is restored._"
        )
        if new_mode:
            await update.message.reply_text(
                "🔀 *Hedge mode: ON*\n\n"
                "• LONG and SHORT on the same pair are allowed\n"
                "• positions are not closed by time\n"
                "• exit only via TP/SL" + tail,
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "➡️ *Hedge mode: OFF*\n\n"
                "• one position per pair\n"
                "• normal expiry rules apply" + tail,
                parse_mode="Markdown",
            )

    async def _cmd_stats(self, update, context):
        """Handle /stats command - show trading statistics."""
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.database:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            # Get stats from database
            stats = self.database.get_signal_stats()

            if not stats or stats.get("total_signals", 0) == 0:
                await update.message.reply_text("📊 No trading data yet")
                return

            # Format stats
            total = stats.get("total_signals", 0)
            closed = stats.get("closed_signals", 0)
            winners = stats.get("winners", 0)
            losers = stats.get("losers", 0)
            win_rate = stats.get("win_rate", 0) * 100
            total_pnl = stats.get("total_pnl", 0)
            avg_winner = stats.get("avg_winner", 0)
            avg_loser = stats.get("avg_loser", 0)
            profit_factor = stats.get("profit_factor", 0)
            avg_hold = stats.get("avg_holding_time_seconds", 0)

            # Format holding time
            if avg_hold > 3600:
                hold_str = f"{avg_hold/3600:.1f}h"
            elif avg_hold > 60:
                hold_str = f"{avg_hold/60:.1f}m"
            else:
                hold_str = f"{avg_hold:.0f}s"

            # P&L emoji
            pnl_emoji = "📈" if total_pnl >= 0 else "📉"

            # Status counts
            by_status = stats.get("by_status", {})
            pending = by_status.get("PENDING", 0)
            executed = by_status.get("EXECUTED", 0)

            text = f"""
📊 *Trading Statistics*

*Signals:*
• Total: {total}
• Open: {executed}
• Closed: {closed}
• Pending: {pending}

*Performance:*
• Win Rate: {win_rate:.1f}%
• Winners: {winners} | Losers: {losers}
{pnl_emoji} Total P&L: {total_pnl:+.2f}%

*Averages:*
• Avg Winner: +{avg_winner:.2f}%
• Avg Loser: {avg_loser:.2f}%
• Profit Factor: {profit_factor:.2f}
• Avg Hold Time: {hold_str}
"""
            await update.message.reply_text(text, parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ Error getting stats: {e}")

    # ==================== Inline Button Callbacks ====================

    async def _handle_button_callback(self, update, context):
        """Handle inline button callbacks for signal actions."""
        query = update.callback_query
        await query.answer()  # Acknowledge the callback

        try:
            data = query.data
            user_id = query.from_user.id

            # Check authorization
            if self.allowed_users and user_id not in self.allowed_users:
                await query.answer("Not authorized", show_alert=True)
                return

            # Parse callback data
            if data.startswith("close_"):
                signal_id = data[6:]  # Remove "close_" prefix
                await self._handle_close_signal(query, signal_id)

            elif data.startswith("be_"):
                signal_id = data[3:]  # Remove "be_" prefix
                await self._handle_move_to_be(query, signal_id)

            elif data.startswith("info_"):
                signal_id = data[5:]  # Remove "info_" prefix
                await self._handle_signal_info(query, signal_id)

            elif data.startswith("add_"):
                signal_id = data[4:]  # Remove "add_" prefix
                await self._handle_add_position(query, signal_id)

            elif data.startswith("partial_"):
                signal_id = data[8:]  # Remove "partial_" prefix
                await self._handle_partial_close(query, signal_id)

            elif data.startswith("movetp_"):
                signal_id = data[7:]  # Remove "movetp_" prefix
                await self._handle_move_tp(query, signal_id)

            else:
                await query.answer("Unknown action", show_alert=True)

        except Exception as e:
            logger.error(f"Error handling button callback: {e}")
            await query.answer(f"Error: {str(e)[:50]}", show_alert=True)

    async def _handle_close_signal(self, query, signal_id: str):
        """Handle close signal button."""
        if not self.signal_lifecycle:
            await query.answer("Lifecycle manager not available", show_alert=True)
            return

        try:
            # Request close through lifecycle manager
            success = await self.signal_lifecycle.close_signal(
                signal_id=signal_id, exit_reason="MANUAL_CLOSE"
            )

            if success:
                await query.answer("Signal closed!")
                # Update message to reflect closed status
                await query.edit_message_reply_markup(reply_markup=None)
            else:
                await query.answer("Signal not found or already closed", show_alert=True)

        except Exception as e:
            logger.error(f"Error closing signal: {e}")
            await query.answer(f"Error: {str(e)[:50]}", show_alert=True)

    async def _handle_move_to_be(self, query, signal_id: str):
        """Handle move SL to break-even button."""
        if not self.signal_lifecycle or not self.signal_lifecycle.break_even_manager:
            await query.answer("Break-even manager not available", show_alert=True)
            return

        try:
            be_manager = self.signal_lifecycle.break_even_manager
            status = be_manager.get_status(signal_id)

            if not status:
                await query.answer("Position not found", show_alert=True)
                return

            if status.state.value in ("ACTIVE", "TRIGGERED", "REENTRY_PENDING"):
                await query.answer(f"BE already active at {status.stop_price:.4f}", show_alert=True)
                return

            # Force activate BE
            be_manager.force_activate_be(signal_id)
            status = be_manager.get_status(signal_id)

            if status and status.state.value in ("ACTIVE", "TRIGGERED"):
                await query.answer(f"SL moved to BE: {status.stop_price:.4f}")
            else:
                await query.answer("Could not activate BE", show_alert=True)

        except Exception as e:
            logger.error(f"Error moving to BE: {e}")
            await query.answer(f"Error: {str(e)[:50]}", show_alert=True)

    async def _handle_signal_info(self, query, signal_id: str):
        """Handle signal info button."""
        if not self.signal_lifecycle:
            await query.answer("Lifecycle manager not available", show_alert=True)
            return

        try:
            tracked = self.signal_lifecycle.tracked_signals.get(signal_id)

            if not tracked:
                await query.answer("Signal not found", show_alert=True)
                return

            # Get current price
            current_price = self.signal_lifecycle.current_prices.get(tracked.symbol, 0)

            # Calculate current P&L
            if tracked.executed_price and current_price:
                if tracked.side == "BUY":
                    pnl_pct = (
                        (current_price - tracked.executed_price) / tracked.executed_price
                    ) * 100
                else:
                    pnl_pct = (
                        (tracked.executed_price - current_price) / tracked.executed_price
                    ) * 100
            else:
                pnl_pct = 0

            # Get BE status
            be_status = "N/A"
            if self.signal_lifecycle.break_even_manager:
                status = self.signal_lifecycle.break_even_manager.get_status(signal_id)
                if status:
                    be_status = status.state.name
                    if status.max_profit_percent > 0:
                        be_status += f" (max: {status.max_profit_percent:.2f}%)"

            # Format holding time
            if tracked.executed_at:
                hold_secs = (utcnow() - tracked.executed_at).total_seconds()
                hours = int(hold_secs // 3600)
                mins = int((hold_secs % 3600) // 60)
                hold_str = f"{hours}h {mins}m"
            else:
                hold_str = "N/A"

            # Trailing TP status
            trailing_str = ""
            if tracked.trailing_tp_active:
                trailing_str = f"\n🎯 Trailing: {tracked.trailing_tp_stop:.4f}"
                if tracked.trailing_tp_max_price:
                    trailing_str += f" (max: {tracked.trailing_tp_max_price:.4f})"

            info = (
                f"📊 {tracked.symbol} {tracked.side}\n"
                f"Entry: {tracked.executed_price:.4f}\n"
                f"Current: {current_price:.4f}\n"
                f"P&L: {pnl_pct:+.2f}%\n"
                f"Max Profit: {tracked.max_profit_percent:.2f}%\n"
                f"BE Status: {be_status}\n"
                f"Remaining: {tracked.remaining_percent:.0f}%\n"
                f"Hold Time: {hold_str}"
                f"{trailing_str}"
            )

            await query.answer(info, show_alert=True)

        except Exception as e:
            logger.error(f"Error getting signal info: {e}")
            await query.answer(f"Error: {str(e)[:50]}", show_alert=True)

    async def _handle_add_position(self, query, signal_id: str):
        """Handle add to position button."""
        if not self.signal_lifecycle:
            await query.answer("Lifecycle manager not available", show_alert=True)
            return

        try:
            tracked = self.signal_lifecycle.tracked_signals.get(signal_id)
            if not tracked:
                await query.answer("Signal not found", show_alert=True)
                return

            success = await self.signal_lifecycle.add_to_position(signal_id, add_percent=50.0)
            if success:
                await query.answer(f"✅ Added 50% to {tracked.symbol}")
            else:
                await query.answer("Could not add to position", show_alert=True)

        except Exception as e:
            logger.error(f"Error adding to position: {e}")
            await query.answer(f"Error: {str(e)[:50]}", show_alert=True)

    async def _handle_partial_close(self, query, signal_id: str):
        """Handle partial close button."""
        if not self.signal_lifecycle:
            await query.answer("Lifecycle manager not available", show_alert=True)
            return

        try:
            tracked = self.signal_lifecycle.tracked_signals.get(signal_id)
            if not tracked:
                await query.answer("Signal not found", show_alert=True)
                return

            # Close 50% of remaining position
            close_percent = 50.0
            remaining = tracked.remaining_percent

            if remaining <= 0:
                await query.answer("Position already fully closed", show_alert=True)
                return

            # Request partial close through lifecycle manager
            success = await self.signal_lifecycle.partial_close_signal(
                signal_id=signal_id, close_percent=close_percent, reason="MANUAL_PARTIAL"
            )

            if success:
                new_remaining = remaining - (remaining * close_percent / 100)
                await query.answer(
                    f"✅ Closed {close_percent:.0f}% | Remaining: {new_remaining:.0f}%"
                )
            else:
                await query.answer("Could not close partial position", show_alert=True)

        except Exception as e:
            logger.error(f"Error partial closing: {e}")
            await query.answer(f"Error: {str(e)[:50]}", show_alert=True)

    async def _handle_move_tp(self, query, signal_id: str):
        """Handle move take profit button."""
        if not self.signal_lifecycle:
            await query.answer("Lifecycle manager not available", show_alert=True)
            return

        try:
            tracked = self.signal_lifecycle.tracked_signals.get(signal_id)
            if not tracked:
                await query.answer("Signal not found", show_alert=True)
                return
            current_price = self.signal_lifecycle.current_prices.get(tracked.symbol, 0)
            if not current_price:
                await query.answer("Current price not available", show_alert=True)
                return

            next_tp = tracked.current_tp_index
            if next_tp < len(tracked.take_profit):
                old_tp = tracked.take_profit[next_tp]
                new_tp = old_tp * 1.02 if tracked.side == "BUY" else old_tp * 0.98

                success = await self.signal_lifecycle.move_take_profit(signal_id, next_tp, new_tp)
                if success:
                    await query.answer(f"✅ TP{next_tp+1}: {old_tp:.4f} → {new_tp:.4f}")
                else:
                    await query.answer("Could not move TP", show_alert=True)
            else:
                await query.answer("All TPs already hit", show_alert=True)

        except Exception as e:
            logger.error(f"Error moving TP: {e}")
            await query.answer(f"Error: {str(e)[:50]}", show_alert=True)

    def is_muted(self) -> bool:
        """Check if notifications are muted."""
        return self.notifications_muted

    # ==================== Signal Notifications (Editable Messages) ====================

    async def notify_signal_opened(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profits: List[float],
        position_size: float,
        risk_amount: float,
        partial_percents: List[float],
        quantity: float = 0.0,
        strategy_name: str = "",
        leverage: int = 1,
        indicators: Dict[str, Any] = None,
        signal_reason: str = "",
    ) -> Optional[int]:
        """
        Send notification when a new signal is opened.
        Returns message_id for future edits.
        """
        if self.notifications_muted or not self.application or not self.notification_chat_id:
            return None

        try:
            # Side emoji
            side_emoji = "🟢 LONG" if side == "BUY" else "🔴 SHORT"

            # Format take profits with partial percentages
            tp_lines = []
            for i, tp in enumerate(take_profits[:3]):
                pct = partial_percents[i] if i < len(partial_percents) else 0
                tp_lines.append(f"  TP{i+1}: {tp:.4f} ({pct:.0f}%)")
            # Calculate potential P&L
            if side == "BUY":
                sl_pnl = position_size * ((stop_loss - entry_price) / entry_price)
            else:
                sl_pnl = position_size * ((entry_price - stop_loss) / entry_price)

            # Format take profits for HTML
            tp_html_lines = []
            for i, tp in enumerate(take_profits[:3]):
                pct = partial_percents[i] if i < len(partial_percents) else 0
                tp_html_lines.append(f"  TP{i+1}: <code>{tp:.4f}</code> ({pct:.0f}%)")
            tp_html_text = "\n".join(tp_html_lines)

            indicators_text = ""
            if indicators:
                ind_lines = []
                for name, value in indicators.items():
                    if isinstance(value, float):
                        ind_lines.append(f"  • {name}: {value:.2f}")
                    else:
                        ind_lines.append(f"  • {name}: {value}")
                if ind_lines:
                    indicators_text = "\n📉 <b>Indicators:</b>\n" + "\n".join(ind_lines)
            reason_text = ""
            if signal_reason:
                reason_text = f"\n💡 <b>Reason:</b> {signal_reason}"

            message = f"""
{side_emoji} <b>{symbol}</b>
━━━━━━━━━━━━━━━━━━━━

📍 <b>Entry:</b> <code>{entry_price:.4f}</code>
💰 <b>Size:</b> ${position_size:.2f}
⚡ <b>Leverage:</b> {leverage}x
🎯 <b>Risk:</b> ${risk_amount:.2f}

🛑 <b>Stop Loss:</b> <code>{stop_loss:.4f}</code> (${sl_pnl:.2f})

🎯 <b>Take Profits:</b>
{tp_html_text}

📊 <b>Strategy:</b> {strategy_name or 'N/A'}{indicators_text}{reason_text}

⏱ <b>Status:</b> 🔵 OPEN
"""

            # Create inline keyboard with action buttons
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = [
                [
                    InlineKeyboardButton("❌ Close", callback_data=f"close_{signal_id}"),
                    InlineKeyboardButton("🔒 SL→BE", callback_data=f"be_{signal_id}"),
                ],
                [
                    InlineKeyboardButton("➕ Add", callback_data=f"add_{signal_id}"),
                    InlineKeyboardButton("½ Partial", callback_data=f"partial_{signal_id}"),
                    InlineKeyboardButton("📈 Move TP", callback_data=f"movetp_{signal_id}"),
                ],
                [
                    InlineKeyboardButton("📊 Info", callback_data=f"info_{signal_id}"),
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Send message with retry (HTML mode is more forgiving)
            sent_message = await self._send_message_with_retry(
                chat_id=self.notification_chat_id,
                text=message,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )

            # Store message info for future edits
            self.signal_messages[signal_id] = {
                "message_id": sent_message.message_id,
                "chat_id": self.notification_chat_id,
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profits": take_profits,
                "position_size": position_size,
                "quantity": quantity,
                "risk_amount": risk_amount,
                "partial_percents": partial_percents,
                "opened_at": datetime.now(timezone.utc),
                "strategy_name": strategy_name,
                "leverage": leverage,
                "partial_closes": [],
                "current_sl": stop_loss,
                "indicators": indicators or {},
                "signal_reason": signal_reason,
            }

            logger.info(
                f"Signal notification sent: {signal_id} -> message_id={sent_message.message_id}"
            )

            # Save telegram_message_id to database
            if self.database:
                try:
                    self.database.update_signal_telegram_message_id(
                        signal_id, sent_message.message_id
                    )
                except Exception as db_error:
                    logger.warning(f"Failed to save telegram_message_id to DB: {db_error}")

            return sent_message.message_id

        except Exception as e:
            logger.error(f"Failed to send signal notification: {e}")
            return None

    async def notify_signal_partial_close(
        self,
        signal_id: str,
        tp_level: int,
        exit_price: float,
        close_percent: float,
        pnl_percent: float,
        pnl_usdt: float,
        remaining_percent: float,
        new_stop_loss: Optional[float] = None,
    ) -> bool:
        """
        Update signal message when partial close happens.
        """
        if self.notifications_muted or signal_id not in self.signal_messages:
            return False

        try:
            msg_data = self.signal_messages[signal_id]

            # Add partial close to history
            msg_data["partial_closes"].append(
                {
                    "tp_level": tp_level,
                    "exit_price": exit_price,
                    "close_percent": close_percent,
                    "pnl_percent": pnl_percent,
                    "pnl_usdt": pnl_usdt,
                }
            )

            # Update stop loss if moved
            if new_stop_loss is not None:
                msg_data["current_sl"] = new_stop_loss

            # Rebuild message
            message = self._build_signal_message(msg_data, remaining_percent)

            # Edit message with retry
            await self._edit_message_with_retry(
                chat_id=msg_data["chat_id"], message_id=msg_data["message_id"], text=message
            )

            return True

        except Exception as e:
            logger.error(f"Failed to update signal notification: {e}")
            return False

    async def notify_signal_closed(
        self,
        signal_id: str,
        exit_price: float,
        exit_reason: str,
        total_pnl_usdt: float,
        roi_percent: float,
        final_partial: Optional[Dict] = None,
    ) -> bool:
        """
        Final update when signal is fully closed.
        """
        if self.notifications_muted:
            logger.debug(f"Notifications muted, skipping signal close notification for {signal_id}")
            return False

        if signal_id not in self.signal_messages:
            # Re-entry signals don't have their own Telegram message - skip silently
            if "_reentry_" in signal_id:
                logger.debug(f"Re-entry signal {signal_id} closed - no Telegram message to update")
                return False
            logger.warning(
                f"Signal {signal_id} not found in signal_messages. "
                f"Available signals: {list(self.signal_messages.keys())}"
            )
            return False

        try:
            msg_data = self.signal_messages[signal_id]
            logger.info(
                f"Closing signal notification: {signal_id} -> message_id={msg_data['message_id']}"
            )
            # the exit price came in as an argument and was never stored — it was
            # missing entirely from the closed-trade message
            if exit_price:
                msg_data["exit_price"] = exit_price
            msg_data["closed_at"] = datetime.now(timezone.utc)

            # Add final partial if provided (avoid duplicates)
            if final_partial:
                tp_level = final_partial.get("tp_level")
                # Check if this TP level was already added
                existing_tps = [pc.get("tp_level") for pc in msg_data["partial_closes"]]
                if tp_level not in existing_tps:
                    msg_data["partial_closes"].append(final_partial)

            # Rebuild message with final status
            message = self._build_signal_message(
                msg_data,
                remaining_percent=0,
                is_closed=True,
                exit_reason=exit_reason,
                total_pnl_usdt=total_pnl_usdt,
                roi_percent=roi_percent,
            )

            # Edit message with retry
            await self._edit_message_with_retry(
                chat_id=msg_data["chat_id"], message_id=msg_data["message_id"], text=message
            )

            # Cleanup after some time (keep for history but remove from active tracking)
            # Don't delete immediately in case we need to reference it
            # del self.signal_messages[signal_id]

            return True

        except Exception as e:
            logger.error(f"Failed to finalize signal notification: {e}")
            return False

    @telegram_retry(max_retries=5, base_delay=1.0)
    async def _edit_message_with_retry(self, chat_id: int, message_id: int, text: str) -> bool:
        """
        Edit message with automatic retry on transient errors.
        Uses exponential backoff for BadGateway, NetworkError, etc.
        """
        await self.application.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML"
        )
        return True

    @telegram_retry(max_retries=3, base_delay=0.5)
    async def _send_message_with_retry(
        self, chat_id: int, text: str, parse_mode: str = "HTML", reply_markup=None
    ):
        """
        Send message with automatic retry on transient errors.
        """
        return await self.application.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup
        )

    async def send_session_summary(
        self,
        duration_hours: float,
        start_balance: float,
        end_balance: float,
        total_pnl: float,
        total_pnl_percent: float,
        signals_closed: int,
        winners: int,
        losers: int,
        best_trade: Dict = None,
        worst_trade: Dict = None,
    ) -> bool:
        """
        Send session summary when bot is stopping.

        Args:
            duration_hours: Session duration in hours
            start_balance: Starting balance
            end_balance: Ending balance
            total_pnl: Total P&L in USDT
            total_pnl_percent: Total P&L percentage
            signals_closed: Number of closed signals
            winners: Number of winning trades
            losers: Number of losing trades
            best_trade: Best trade info (symbol, pnl)
            worst_trade: Worst trade info (symbol, pnl)

        Returns:
            True if sent successfully
        """
        if not self.application or not self.notification_chat_id:
            return False

        try:
            # Format duration
            hours = int(duration_hours)
            mins = int((duration_hours % 1) * 60)
            duration_str = f"{hours}h {mins}m"

            # Win rate
            total_trades = winners + losers
            win_rate = (winners / total_trades * 100) if total_trades > 0 else 0

            # P&L emoji
            pnl_emoji = "📈" if total_pnl >= 0 else "📉"

            # Best/worst trades
            best_str = ""
            if best_trade:
                best_str = f"\n🏆 <b>Best:</b> {best_trade.get('symbol', 'N/A')} +${best_trade.get('pnl', 0):.2f}"

            worst_str = ""
            if worst_trade:
                worst_str = f"\n💔 <b>Worst:</b> {worst_trade.get('symbol', 'N/A')} ${worst_trade.get('pnl', 0):.2f}"

            message = f"""
📊 <b>Session Summary</b>
━━━━━━━━━━━━━━━━━━━━

⏱ <b>Duration:</b> {duration_str}
💰 <b>Start Balance:</b> ${start_balance:.2f}
💰 <b>End Balance:</b> ${end_balance:.2f}

{pnl_emoji} <b>P&L:</b> ${total_pnl:+.2f} ({total_pnl_percent:+.2f}%)

📈 <b>Trades:</b> {signals_closed} closed
✅ <b>Win Rate:</b> {win_rate:.1f}% ({winners}W / {losers}L)
{best_str}{worst_str}

🛑 <b>Bot Stopped</b>
"""

            await self._send_message_with_retry(
                chat_id=self.notification_chat_id, text=message, parse_mode="HTML"
            )

            logger.info("Session summary sent to Telegram")
            return True

        except Exception as e:
            logger.error(f"Failed to send session summary: {e}")
            return False

    def _build_signal_message(
        self,
        msg_data: Dict[str, Any],
        remaining_percent: float,
        is_closed: bool = False,
        exit_reason: str = "",
        total_pnl_usdt: float = 0,
        roi_percent: float = 0,
    ) -> str:
        """Build the signal message with current state."""
        symbol = msg_data["symbol"]
        side = msg_data["side"]
        entry_price = msg_data["entry_price"]
        position_size = msg_data["position_size"]
        take_profits = msg_data["take_profits"]
        partial_percents = msg_data["partial_percents"]
        strategy_name = msg_data["strategy_name"]
        partial_closes = msg_data["partial_closes"]
        current_sl = msg_data["current_sl"]

        # Side emoji
        side_emoji = "🟢 LONG" if side == "BUY" else "🔴 SHORT"

        # Status
        if is_closed:
            if total_pnl_usdt >= 0:
                status = "✅ CLOSED (PROFIT)"
            else:
                status = "❌ CLOSED (LOSS)"
        elif remaining_percent < 100 and partial_closes:
            status = "🟡 PARTIALLY CLOSED"
        else:
            status = "🔵 OPEN"

        # Format take profits with checkmarks for hit TPs
        tp_lines = []
        hit_tps = {pc["tp_level"] for pc in partial_closes if pc.get("tp_level")}

        for i, tp in enumerate(take_profits[:3]):
            pct = partial_percents[i] if i < len(partial_percents) else 0
            tp_num = i + 1
            if tp_num in hit_tps:
                # Find the partial close for this TP
                pc = next((p for p in partial_closes if p.get("tp_level") == tp_num), None)
                if pc:
                    tp_lines.append(
                        f"  ✅ TP{tp_num}: {self._fmt_price(tp)} | +{pc['pnl_percent']:.2f}% (${pc['pnl_usdt']:+.2f})"
                    )
                else:
                    tp_lines.append(f"  ✅ TP{tp_num}: {self._fmt_price(tp)} ({pct:.0f}%)")
            else:
                tp_lines.append(f"  ⬜ TP{tp_num}: {self._fmt_price(tp)} ({pct:.0f}%)")

        tp_text = "\n".join(tp_lines)

        # Partial closes history
        # Hit take-profits are already shown with a tick above together with the
        # result, so only what those lines lack ends up here: the remainder and
        # closes not tied to a level. Otherwise the same trade was printed twice.
        history_lines = []
        for pc in partial_closes:
            tp_level = pc.get("tp_level", "?")
            if isinstance(tp_level, int) and tp_level in hit_tps:
                continue
            if tp_level == "REMAINDER":
                history_lines.append(
                    f"  📊 Remainder: {pc['pnl_percent']:+.2f}% (${pc['pnl_usdt']:+.2f})"
                )
            else:
                history_lines.append(
                    f"  📊 {pc.get('close_percent', 0):.0f}%: "
                    f"{pc['pnl_percent']:+.2f}% (${pc['pnl_usdt']:+.2f})"
                )

        history_text = "\n".join(history_lines) if history_lines else "  No closes"

        # SL indicator
        original_sl = msg_data["stop_loss"]
        if current_sl != original_sl:
            sl_text = (
                f"<code>{self._fmt_price(current_sl)}</code> "
                f"(moved from {self._fmt_price(original_sl)})"
            )
        else:
            sl_text = f"<code>{self._fmt_price(current_sl)}</code>"

        # WHAT IS SHOWN HERE AND WHY.
        #
        # The old layout came from the crypto days and was misleading for CFDs:
        #   "Size: $173001.47"  — position notional. At 500x leverage it is huge and
        #                         says nothing: next to it an $82 profit looks
        #                         like an error. Lots and risk are what matter.
        #   "(+0.05%)"          — percentage OF NOTIONAL. The same $82 on a $2000
        #                         account is +4%, not +0.05%. The least useful
        #                         denominator of all was shown.
        #   "Leverage: 500x"    — a config constant, the same in every
        #                         trade.
        #   "Take Profits:" and "Close history: No closes" — on MT5 the bot places
        #                         no TP orders, so both blocks were an empty
        #                         frame in every message.
        #
        # Instead: size in lots, risk in money, exit price, time in position and
        # the result as a percentage OF THE ACCOUNT. Empty blocks are not printed.
        quantity = msg_data.get("quantity") or 0.0
        risk_amount = msg_data.get("risk_amount") or 0.0
        exit_price = msg_data.get("exit_price")

        size_line = (
            f"📦 <b>Size:</b> {self._fmt_lots(quantity)}"
            if quantity
            else f"📦 <b>Notional:</b> ${position_size:,.0f}"
        )
        if risk_amount:
            size_line += f"   <b>risk</b> ${risk_amount:,.2f}"

        price_line = f"📍 <b>Entry:</b> <code>{self._fmt_price(entry_price)}</code>"
        if is_closed and exit_price:
            price_line += f" → <b>exit</b> <code>{self._fmt_price(exit_price)}</code>"

        held = ""
        opened_at, closed_at = msg_data.get("opened_at"), msg_data.get("closed_at")
        if opened_at:
            end = closed_at or datetime.now(timezone.utc)
            mins = (end - opened_at).total_seconds() / 60
            held = f"{mins / 60:.1f}h" if mins >= 60 else f"{mins:.0f}m"

        parts = [
            f"{side_emoji} <b>{symbol}</b>" + (f"  ·  {strategy_name}" if strategy_name else ""),
            "━━━━━━━━━━━━━━━━━━━━",
            price_line,
            size_line,
            f"🛑 <b>Stop:</b> {sl_text}",
        ]
        # TP and history blocks are printed only when they have content:
        # on MT5 they never do, and the empty frame took half the message.
        if tp_text.strip():
            parts.append(f"🎯 <b>Take-profits:</b>\n{tp_text}")
        if history_lines:
            parts.append(f"📜 <b>Partial closes:</b>\n{history_text}")

        message = "\n".join(parts) + "\n"

        if is_closed:
            pnl_emoji = "💰" if total_pnl_usdt >= 0 else "💸"
            # percentage of the ACCOUNT, not of the position notional
            acct_pct = None
            if self.signal_lifecycle:
                try:
                    bal = self.signal_lifecycle.get_stats().get("current_balance", 0)
                    if bal:
                        acct_pct = total_pnl_usdt / bal * 100
                except Exception:  # noqa: BLE001
                    pass
            pct_str = f" ({acct_pct:+.2f}% of account)" if acct_pct is not None else ""
            message += (
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"{pnl_emoji} <b>RESULT:</b> ${total_pnl_usdt:+,.2f}{pct_str}\n"
                f"📋 <b>Reason:</b> {exit_reason}" + (f"   ⏱ {held}" if held else "")
            )
        else:
            if remaining_percent < 100:
                message += f"📊 <b>Remaining:</b> {remaining_percent:.0f}%\n"
            if held:
                message += f"⏱ <b>In position:</b> {held}\n"

        message += f"\n{status}"
        return message

    async def _cmd_accounts(self, update, context):
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.account_manager:
            await update.message.reply_text("Single-account mode (no AccountManager)")
            return

        balances = await self.account_manager.get_all_balances()

        text = "💰 *All Accounts*\n\n"
        total = 0.0

        for acc_id, acc in self.account_manager.accounts.items():
            status = "🟢" if acc.enabled else "🔴"
            connected = "✓" if acc_id in self.account_manager.connectors else "✗"
            bal, cur = self._pick_balance(balances.get(acc_id, {}))
            if cur:
                total += bal
                bal_str = f"{bal:,.2f} {cur}"
            else:
                bal_str = "n/a"

            text += f"{status} *{acc.name}* [{connected}]\n"
            text += f"   Mode: {acc.mode} | Balance: {bal_str}\n\n"

        text += f"━━━━━━━━━━━━━━━━━━\n*Total:* ${total:.2f}"
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_account(self, update, context):
        if not self._is_authorized(update.effective_user.id):
            return await self._unauthorized_response(update, context)

        if not self.account_manager:
            await update.message.reply_text("Single-account mode")
            return

        if not context.args:
            accounts = ", ".join(self.account_manager.accounts.keys())
            await update.message.reply_text(f"Usage: /account <id>\nAvailable: {accounts}")
            return

        acc_id = context.args[0]
        acc = self.account_manager.accounts.get(acc_id)

        if not acc:
            await update.message.reply_text(f"Account not found: {acc_id}")
            return

        connector = self.account_manager.connectors.get(acc_id)
        balance = await connector.get_balance() if connector else {}
        positions = await connector.get_positions() if connector else []

        strategies_str = ", ".join(acc.strategies) if acc.strategies else "ALL"
        _bal, _cur = self._pick_balance(balance)
        _bal_str = f"{_bal:,.2f} {_cur}" if _cur else "n/a"

        text = f"""
📊 *Account: {acc.name}*

*ID:* `{acc_id}`
*Mode:* {acc.mode}
*Enabled:* {'✅' if acc.enabled else '❌'}
*Connected:* {'✅' if connector else '❌'}
*Strategies:* {strategies_str}

*Balance:* {_bal_str}
*Open positions:* {len(positions)}
"""
        await update.message.reply_text(text, parse_mode="Markdown")


async def run_telegram_bot(
    bot_token: str, allowed_users: List[int], trading_controller=None, performance_tracker=None
):
    """
    Run Telegram bot as standalone coroutine.

    Args:
        bot_token: Telegram bot token
        allowed_users: List of allowed user IDs
        trading_controller: TradingController instance
        performance_tracker: PerformanceTracker instance
    """
    bot = TelegramControlBot(
        bot_token=bot_token,
        allowed_users=allowed_users,
        trading_controller=trading_controller,
        performance_tracker=performance_tracker,
    )

    await bot.start()

    try:
        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        await bot.stop()
