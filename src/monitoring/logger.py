"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : logger
    Project     : Project-103
    Description : Centralized logging module for trading bot. Provides structured logging for all system events.

    SPDX-License-Identifier: AGPL-3.0

"""

import json
import logging
import logging.handlers
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.models.order import Order
from src.models.signal import Signal
from src.models.trade import Trade

# Force all %(asctime)s in every logger across the process to UTC,
# regardless of the host machine's timezone (Windows/Linux/docker).
logging.Formatter.converter = time.gmtime


def generate_session_id() -> str:
    """Generate a unique session ID for this bot run (UTC)."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = str(uuid.uuid4())[:6]
    return f"{timestamp}_{short_uuid}"


class RepeatFilter(logging.Filter):
    """Collapses consecutive identical messages.

    The bot runs for weeks, and a state checked on a timer keeps writing the same
    line while it is broken. In a real log it looked like this: `Redis ping
    failed` — 91 lines in a row, `System health degraded!` — 92. One line of
    information taking the space of a whole incident and hiding everything else.

    The first occurrence passes as is. Repeats within `window` seconds are
    suppressed and counted; when the window expires, the next occurrence goes out
    with the number of suppressed ones appended. So "it is still broken" is not
    lost, but repeats once a minute rather than every second.

    The key is (logger, level, text), so different errors do not silence each
    other, and lines with changing numbers (prices, tickets) never count as repeats.
    """

    def __init__(self, window: float = 60.0, max_keys: int = 512):
        super().__init__()
        self.window = window
        self.max_keys = max_keys
        self._seen: Dict[tuple, list] = {}  # key -> [first_ts, suppressed]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            key = (record.name, record.levelno, record.getMessage())
        except Exception:  # noqa: BLE001
            return True
        now = time.time()
        entry = self._seen.get(key)

        if entry is not None and now - entry[0] < self.window:
            entry[1] += 1
            return False

        suppressed = entry[1] if entry is not None else 0
        if suppressed:
            # append to an args-independent field so %-formatting is not broken
            record.msg = f"{record.getMessage()}  (+{suppressed} identical)"
            record.args = ()
        self._seen[key] = [now, 0]

        if len(self._seen) > self.max_keys:
            cutoff = now - self.window
            self._seen = {k: v for k, v in self._seen.items() if v[0] >= cutoff}
        return True


class TradeLogFilter(logging.Filter):
    """Filter for trade-specific logs"""

    def filter(self, record):
        return hasattr(record, "log_type") and record.log_type == "trade"


class SignalLogFilter(logging.Filter):
    """Filter for signal-specific logs (signals + errors)"""

    def filter(self, record):
        # Include signal logs
        if hasattr(record, "log_type") and record.log_type == "signal":
            return True
        # Include errors
        if record.levelno >= logging.ERROR:
            return True
        # Include messages containing signal keywords
        msg = record.getMessage().lower()
        if "signal" in msg or "SIGNAL" in record.getMessage():
            return True
        return False


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging"""

    def format(self, record):
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields
        if hasattr(record, "log_type"):
            log_data["log_type"] = record.log_type

        for key in ["trade_id", "order_id", "signal_id", "symbol", "side", "strategy"]:
            if hasattr(record, key):
                log_data[key] = getattr(record, key)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class TradingLogger:
    """
    Centralized logger for trading bot.

    Configures the ROOT logger so all module loggers (logging.getLogger(__name__))
    automatically use these handlers for errors.log, signals.log, trades.log.

    Logs are organized by session: logs/session_{session_id}/
    """

    _initialized = False
    _session_id: Optional[str] = None

    def __init__(
        self,
        name: str = "trading_bot",
        log_level: str = "INFO",
        session_id: Optional[str] = None,
        enable_console: bool = True,
        enable_file: bool = True,
        enable_json: bool = False,
        max_bytes: int = 50 * 1024 * 1024,
        backup_count: int = 3,
        repeat_window: float = 60.0,
    ):
        """
        Initialize trading logger on ROOT logger.

        Args:
            name: Logger name (kept for compatibility, but handlers go to root)
            log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            session_id: Unique session ID for this bot run (auto-generated if None)
            enable_console: Enable console output
            enable_file: Enable file output
            enable_json: Use JSON format
            max_bytes: file size after which it is rotated
            backup_count: how many rotated files to keep
            repeat_window: window for collapsing identical messages, seconds (0 = off)
        """
        # Prevent double initialization
        if TradingLogger._initialized:
            self.logger = logging.getLogger(name)
            self.session_id = TradingLogger._session_id
            return

        # Generate or use provided session_id
        self.session_id = session_id or generate_session_id()
        TradingLogger._session_id = self.session_id

        # Create session-based logs directory
        session_log_dir = Path(f"logs/session_{self.session_id}")
        session_log_dir.mkdir(parents=True, exist_ok=True)

        # Also create a symlink to latest session for convenience
        latest_link = Path("logs/latest")
        try:
            if latest_link.exists() or latest_link.is_symlink():
                latest_link.unlink()
            latest_link.symlink_to(session_log_dir.name, target_is_directory=True)
        except (OSError, NotImplementedError):
            # Symlinks may not work on all systems (Windows without admin)
            pass

        # Get ROOT logger to capture all module logs
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, log_level))

        # IMPORTANT: Clear ALL existing handlers to prevent duplicates
        # This handles cases where handlers were added by basicConfig, uvicorn, or other libraries
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()

        # Also keep a named logger for direct use
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, log_level))
        # Clear handlers on named logger too to prevent duplicates
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()

        # Formatter
        if enable_json:
            formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )

        def _with_repeat(handler):
            """Repeat collapsing — on each handler separately.

            It cannot be attached to the logger: that filters a record before any
            handler, and a suppressed line would also disappear from errors.log,
            where it is needed. A separate instance per handler keeps their
            counters independent.
            """
            if repeat_window > 0:
                handler.addFilter(RepeatFilter(window=repeat_window))
            return handler

        # Console handler - add to ROOT
        if enable_console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            root_logger.addHandler(_with_repeat(console_handler))

        # Main file handler - add to ROOT (in session directory).
        # Rotation is mandatory: the bot runs for weeks without a restart, and a
        # plain FileHandler grows without limit — on a VPS it is a matter of time.
        if enable_file:
            file_handler = logging.handlers.RotatingFileHandler(
                session_log_dir / "trading_bot.log",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(_with_repeat(file_handler))

        # Error log handler - add to ROOT (captures all ERROR+ logs)
        error_handler = logging.handlers.RotatingFileHandler(
            session_log_dir / "errors.log",
            maxBytes=max_bytes // 10,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        root_logger.addHandler(_with_repeat(error_handler))

        # Trade log handler - add to ROOT with filter
        trade_handler = logging.handlers.RotatingFileHandler(
            session_log_dir / "trades.log",
            maxBytes=max_bytes // 5,
            backupCount=backup_count,
            encoding="utf-8",
        )
        trade_handler.addFilter(TradeLogFilter())
        trade_handler.setFormatter(formatter)
        root_logger.addHandler(_with_repeat(trade_handler))

        # Signal log handler - add to ROOT with filter
        signal_handler = logging.handlers.RotatingFileHandler(
            session_log_dir / "signals.log",
            maxBytes=max_bytes // 2,
            backupCount=backup_count,
            encoding="utf-8",
        )
        signal_handler.addFilter(SignalLogFilter())
        signal_handler.setFormatter(formatter)
        root_logger.addHandler(_with_repeat(signal_handler))

        # debug.log was removed: it was a byte-for-byte duplicate of trading_bot.log.
        # A DEBUG level on the HANDLER does nothing — the logger filters records
        # before they reach the handler, so the file got exactly the same content.
        # Every line was written to disk twice.
        # For a real DEBUG file, raise the root logger level, not the handler's,
        # and give it a separate rotation at the same time.

        TradingLogger._initialized = True

        # Log session start
        self.logger.info(f"{'='*60}")
        self.logger.info(f"SESSION STARTED: {self.session_id}")
        self.logger.info(f"Log directory: {session_log_dir.absolute()}")
        self.logger.info(f"{'='*60}")

    def log_trade(self, trade: Trade, action: str = "closed"):
        """Log trade event"""
        self.logger.info(
            f"Trade {action}: {trade.symbol} {trade.side} P&L: {trade.pnl:.2f}",
            extra={
                "trade_id": trade.id,
                "symbol": trade.symbol,
                "side": trade.side,
                "entry": trade.entry_price,
                "exit": trade.exit_price,
                "pnl": trade.pnl,
                "action": action,
                "log_type": "trade",
            },
        )

    def log_order(self, order: Order, action: str = "created"):
        """Log order event"""
        self.logger.info(
            f"Order {action}: {order.symbol} {order.side.value} {order.type.value}",
            extra={
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side.value,
                "type": order.type.value,
                "quantity": order.quantity,
                "price": order.price,
                "status": order.status.value,
                "action": action,
                "log_type": "order",
            },
        )

    def log_signal(self, signal: Signal):
        """Log signal event"""
        self.logger.info(
            f"Signal generated: {signal.symbol} {signal.type.value}",
            extra={
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": signal.type.value,
                "confidence": signal.confidence,
                "strategy": signal.strategy_name,
                "log_type": "signal",
            },
        )

    def log_error(self, error: Exception, context: Dict[str, Any] = None):
        """Log error"""
        self.logger.error(
            f"Error: {str(error)}",
            extra={
                "error_type": type(error).__name__,
                "error_message": str(error),
                "context": context or {},
                "log_type": "error",
            },
            exc_info=True,
        )

    def log_performance(self, metrics: Dict[str, Any]):
        """Log performance metrics"""
        self.logger.info("Performance update", extra={**metrics, "log_type": "performance"})

    def log_system(self, event: str, details: Dict[str, Any] = None):
        """Log system event"""
        self.logger.info(
            f"System: {event}",
            extra={"event": event, "details": details or {}, "log_type": "system"},
        )


class StructuredLogger:
    """Structured logger with context"""

    def __init__(self, logger: TradingLogger):
        self.logger = logger
        self.context: Dict[str, Any] = {}

    def set_context(self, **kwargs):
        """Set context for subsequent logs"""
        self.context.update(kwargs)

    def clear_context(self):
        """Clear context"""
        self.context = {}

    def log(self, level: str, message: str, **kwargs):
        """Log with context"""
        extra = {**self.context, **kwargs}
        getattr(self.logger.logger, level.lower())(message, extra=extra)
