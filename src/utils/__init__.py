"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : __init__
    Project     : Project-103
    Description : Utility modules for the trading bot system. Provides helper functions for datetime, math, cryptography, validation, and formatting.

    SPDX-License-Identifier: AGPL-3.0

"""

from src.utils.crypto import decrypt_api_key, encrypt_api_key, generate_signature, hash_string
from src.utils.datetime_utils import (
    align_timestamp_to_timeframe,
    datetime_to_timestamp,
    format_timestamp,
    get_current_timestamp,
    get_timeframe_milliseconds,
    timestamp_to_datetime,
)

# Environment variables loader
from src.utils.env_loader import (
    REQUIRED_VARS_BYBIT,
    REQUIRED_VARS_DATABASE,
    REQUIRED_VARS_MINIMAL,
    REQUIRED_VARS_MT5,
    REQUIRED_VARS_TELEGRAM,
    EnvLoader,
    load_env,
    load_env_for_trading,
    load_env_with_monitoring,
)
from src.utils.formatters import (
    format_percentage,
    format_pnl,
    format_position_message,
    format_price,
    format_quantity,
    format_signal_message,
)
from src.utils.helpers import (
    chunks,
    get_nested_value,
    measure_execution_time,
    retry_on_exception,
    safe_divide,
)
from src.utils.math_utils import (
    calculate_ema,
    calculate_percentage_change,
    calculate_rsi,
    calculate_sma,
    normalize_value,
    round_to_precision,
)
from src.utils.validators import (
    validate_order_side,
    validate_order_type,
    validate_price,
    validate_quantity,
    validate_symbol,
    validate_timeframe,
)

__all__ = [
    # DateTime utils
    "timestamp_to_datetime",
    "datetime_to_timestamp",
    "get_current_timestamp",
    "format_timestamp",
    "get_timeframe_milliseconds",
    "align_timestamp_to_timeframe",
    # Math utils
    "calculate_percentage_change",
    "calculate_rsi",
    "calculate_ema",
    "calculate_sma",
    "round_to_precision",
    "normalize_value",
    # Crypto utils
    "encrypt_api_key",
    "decrypt_api_key",
    "hash_string",
    "generate_signature",
    # Validators
    "validate_symbol",
    "validate_timeframe",
    "validate_price",
    "validate_quantity",
    "validate_order_side",
    "validate_order_type",
    # Formatters
    "format_price",
    "format_quantity",
    "format_percentage",
    "format_pnl",
    "format_signal_message",
    "format_position_message",
    # Helpers
    "retry_on_exception",
    "measure_execution_time",
    "safe_divide",
    "chunks",
    "get_nested_value",
    # Environment loader
    "EnvLoader",
    "load_env",
    "load_env_for_trading",
    "load_env_with_monitoring",
    "REQUIRED_VARS_BYBIT",
    "REQUIRED_VARS_MT5",
    "REQUIRED_VARS_TELEGRAM",
    "REQUIRED_VARS_DATABASE",
    "REQUIRED_VARS_MINIMAL",
]
