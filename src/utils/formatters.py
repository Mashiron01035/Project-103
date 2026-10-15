"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : formatters
    Project     : Project-103
    Description : Data formatting utilities. Formats prices, quantities, timestamps, and trading messages for display.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import Any, Dict


def format_price(price: float, decimals: int = 2) -> str:
    """Format a price for display"""
    return f"{price:.{decimals}f}"


def format_quantity(quantity: float, decimals: int = 4) -> str:
    """Format a quantity for display"""
    return f"{quantity:.{decimals}f}"


def format_percentage(value: float, decimals: int = 2, show_sign: bool = True) -> str:
    """Format a percentage value"""
    sign = "+" if value > 0 and show_sign else ""
    return f"{sign}{value:.{decimals}f}%"


def format_pnl(pnl: float, decimals: int = 2, currency: str = "USDT") -> str:
    """Format P&L for display"""
    sign = "+" if pnl > 0 else ""
    emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
    return f"{emoji} {sign}{pnl:.{decimals}f} {currency}"


def format_timestamp_short(timestamp: int) -> str:
    """Format a timestamp in short form"""
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
    return dt.strftime("%H:%M:%S")


def format_timestamp_long(timestamp: int) -> str:
    """Format a timestamp in long form"""
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_signal_message(signal: Dict[str, Any]) -> str:
    """Format a signal for a notification"""
    symbol = signal.get("symbol", "UNKNOWN")
    side = signal.get("side", "UNKNOWN")
    entry = signal.get("entry_price", 0)
    stop_loss = signal.get("stop_loss", 0)
    take_profit = signal.get("take_profit", [])
    confidence = signal.get("confidence", 0)
    strategy = signal.get("strategy_name", "UNKNOWN")

    emoji = "🟢" if side == "BUY" else "🔴"

    tp_str = ", ".join([format_price(tp) for tp in take_profit]) if take_profit else "N/A"

    message = f"""
{emoji} NEW SIGNAL {emoji}

Symbol: {symbol}
Strategy: {strategy}
Side: {side}
Entry: {format_price(entry)}
Stop Loss: {format_price(stop_loss)}
Take Profit: {tp_str}
Confidence: {format_percentage(confidence * 100, show_sign=False)}
"""
    return message.strip()


def format_position_message(position: Dict[str, Any]) -> str:
    """Format a position for a notification"""
    symbol = position.get("symbol", "UNKNOWN")
    side = position.get("side", "UNKNOWN")
    entry = position.get("entry_price", 0)
    current = position.get("current_price", 0)
    quantity = position.get("quantity", 0)
    pnl = position.get("unrealized_pnl", 0)
    pnl_percent = position.get("pnl_percent", 0)

    emoji = "🟢" if side == "BUY" else "🔴"

    message = f"""
{emoji} POSITION UPDATE {emoji}

Symbol: {symbol}
Side: {side}
Entry: {format_price(entry)}
Current: {format_price(current)}
Quantity: {format_quantity(quantity)}
P&L: {format_pnl(pnl)} ({format_percentage(pnl_percent)})
"""
    return message.strip()


def format_order_message(order: Dict[str, Any]) -> str:
    """Format an order for a notification"""
    symbol = order.get("symbol", "UNKNOWN")
    side = order.get("side", "UNKNOWN")
    order_type = order.get("type", "UNKNOWN")
    quantity = order.get("quantity", 0)
    price = order.get("price", 0)
    status = order.get("status", "UNKNOWN")

    emoji = "✅" if status == "FILLED" else "⏳" if status == "NEW" else "❌"

    message = f"""
{emoji} ORDER {status} {emoji}

Symbol: {symbol}
Side: {side}
Type: {order_type}
Quantity: {format_quantity(quantity)}
Price: {format_price(price)}
"""
    return message.strip()


def format_daily_report(metrics: Dict[str, Any]) -> str:
    """Format a daily report"""
    total_trades = metrics.get("total_trades", 0)
    winning_trades = metrics.get("winning_trades", 0)
    losing_trades = metrics.get("losing_trades", 0)
    win_rate = metrics.get("win_rate", 0)
    total_pnl = metrics.get("total_pnl", 0)
    profit_factor = metrics.get("profit_factor", 0)

    message = f"""
📊 DAILY TRADING REPORT 📊

Total Trades: {total_trades}
Winning: {winning_trades} | Losing: {losing_trades}
Win Rate: {format_percentage(win_rate, show_sign=False)}
Total P&L: {format_pnl(total_pnl)}
Profit Factor: {profit_factor:.2f}
"""
    return message.strip()


def format_large_number(number: float) -> str:
    """Format large numbers with abbreviations"""
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    elif abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    elif abs(number) >= 1_000:
        return f"{number / 1_000:.2f}K"
    else:
        return f"{number:.2f}"


def format_duration(seconds: int) -> str:
    """Format a duration in a readable form"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"
