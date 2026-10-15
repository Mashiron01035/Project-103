"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : validators
    Project     : Project-103
    Description : Data validation utilities. Validates trading parameters, symbols, prices, and order configurations.

    SPDX-License-Identifier: AGPL-3.0

"""

import re
from typing import Any, Optional

VALID_TIMEFRAMES = ["1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"]
VALID_ORDER_SIDES = ["BUY", "SELL"]
VALID_ORDER_TYPES = ["MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT", "TRAILING_STOP"]
VALID_ORDER_STATUSES = ["NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"]


def validate_symbol(symbol: str) -> bool:
    """Check that a trading pair symbol is valid"""
    if not symbol or not isinstance(symbol, str):
        return False
    if symbol != symbol.upper():
        return False
    if not re.match(r"^[A-Z0-9]+$", symbol):
        return False
    if len(symbol) < 6 or len(symbol) > 20:
        return False
    return True


def validate_timeframe(timeframe: str) -> bool:
    """Check that a timeframe is valid"""
    return timeframe in VALID_TIMEFRAMES


def validate_price(price: float) -> bool:
    """Check that a price is valid"""
    if not isinstance(price, (int, float)):
        return False
    if price <= 0:
        return False
    if price == float("inf") or price == float("-inf"):
        return False
    if price != price:
        return False
    return True


def validate_quantity(quantity: float, min_qty: Optional[float] = None) -> bool:
    """Check that a quantity is valid"""
    if not isinstance(quantity, (int, float)):
        return False
    if quantity <= 0:
        return False
    if min_qty is not None and quantity < min_qty:
        return False
    if quantity == float("inf") or quantity == float("-inf"):
        return False
    if quantity != quantity:
        return False
    return True


def validate_order_side(side: str) -> bool:
    """Check that an order side is valid"""
    return side in VALID_ORDER_SIDES


def validate_order_type(order_type: str) -> bool:
    """Check that an order type is valid"""
    return order_type in VALID_ORDER_TYPES


def validate_order_status(status: str) -> bool:
    """Check that an order status is valid"""
    return status in VALID_ORDER_STATUSES


def validate_percentage(value: float, min_val: float = 0.0, max_val: float = 100.0) -> bool:
    """Check that a percentage value is valid"""
    if not isinstance(value, (int, float)):
        return False
    if value < min_val or value > max_val:
        return False
    return True


def validate_risk_reward_ratio(ratio: float) -> bool:
    """Check that a risk/reward ratio is valid"""
    if not isinstance(ratio, (int, float)):
        return False
    if ratio < 1.0 or ratio > 100.0:
        return False
    return True


def validate_confidence(confidence: float) -> bool:
    """Check that a signal confidence is valid"""
    return validate_percentage(confidence, 0.0, 1.0)


def validate_timestamp(timestamp: Any) -> bool:
    """Check that a timestamp is valid"""
    if not isinstance(timestamp, int):
        return False
    if timestamp < 0:
        return False
    if timestamp < 946684800000 or timestamp > 4102444800000:
        return False
    return True


def validate_leverage(leverage: int, max_leverage: int = 125) -> bool:
    """Check that a leverage is valid"""
    if not isinstance(leverage, int):
        return False
    if leverage < 1 or leverage > max_leverage:
        return False
    return True


def validate_stop_loss(entry_price: float, stop_loss: float, side: str) -> bool:
    """Check that a stop-loss is valid relative to the entry price"""
    if not validate_price(entry_price) or not validate_price(stop_loss):
        return False
    if not validate_order_side(side):
        return False
    if side == "BUY":
        return stop_loss < entry_price
    else:
        return stop_loss > entry_price


def validate_take_profit(entry_price: float, take_profit: float, side: str) -> bool:
    """Check that a take-profit is valid relative to the entry price"""
    if not validate_price(entry_price) or not validate_price(take_profit):
        return False
    if not validate_order_side(side):
        return False
    if side == "BUY":
        return take_profit > entry_price
    else:
        return take_profit < entry_price
