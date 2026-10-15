"""

    -*- coding: utf-8 -*-
    Log Tags Module

    Provides short, readable tags for log messages to reduce verbosity
    while maintaining clarity. Use these instead of long descriptive prefixes.

    Format: [MODULE:ACTION] message

    SPDX-License-Identifier: AGPL-3.0

"""


class GridTags:
    """Tags for Grid-Hedge module"""

    OPEN = "[GRID:OPEN]"  # Position opened
    CLOSE = "[GRID:CLOSE]"  # Position closed (full)
    TP = "[GRID:TP]"  # Take profit hit
    SL = "[GRID:SL]"  # Stop loss hit
    PARTIAL = "[GRID:PART]"  # Partial close
    DUST = "[GRID:DUST]"  # Dust cleanup
    HEDGE = "[GRID:HEDGE]"  # Hedge position
    REV = "[GRID:REV]"  # Reversal detection
    CENTER = "[GRID:CTR]"  # Center update
    CREATE = "[GRID:NEW]"  # Grid created
    SKIP = "[GRID:SKIP]"  # Level skipped (filter)
    ERR = "[GRID:ERR]"  # Error
    WARN = "[GRID:WARN]"  # Warning
    CONFIRM = "[GRID:CONF]"  # Entry confirmation
    SESSION = "[GRID:SESS]"  # Session filter
    PERF = "[GRID:PERF]"  # Performance/symbol pause


class SignalTags:
    """Tags for Signal module"""

    NEW = "[SIG:NEW]"  # New signal generated
    INV = "[SIG:INV]"  # Signal inverted
    BLOCK = "[SIG:BLOCK]"  # Signal blocked
    EXEC = "[SIG:EXEC]"  # Signal executed
    EXPIRE = "[SIG:EXP]"  # Signal expired
    ERR = "[SIG:ERR]"  # Error


class PositionTags:
    """Tags for Position management"""

    OPEN = "[POS:OPEN]"  # Position opened
    CLOSE = "[POS:CLOSE]"  # Position closed
    UPDATE = "[POS:UPD]"  # Position updated
    BE = "[POS:BE]"  # Moved to breakeven
    TRAIL = "[POS:TRAIL]"  # Trailing stop updated
    EXTERN = "[POS:EXT]"  # External close detected
    DCA = "[POS:DCA]"  # DCA (Dollar Cost Averaging) applied
    ERR = "[POS:ERR]"  # Error


class OrderTags:
    """Tags for Order execution"""

    SEND = "[ORD:SEND]"  # Order sent
    FILL = "[ORD:FILL]"  # Order filled
    CANCEL = "[ORD:CANC]"  # Order cancelled
    REJECT = "[ORD:REJ]"  # Order rejected
    ERR = "[ORD:ERR]"  # Error


class SystemTags:
    """Tags for System events"""

    START = "[SYS:START]"  # System started
    STOP = "[SYS:STOP]"  # System stopped
    CONN = "[SYS:CONN]"  # Connection event
    RECONN = "[SYS:RECONN]"  # Reconnection
    HEALTH = "[SYS:HEALTH]"  # Health check
    ERR = "[SYS:ERR]"  # Error


class BTCTags:
    """Tags for BTC analysis"""

    TREND = "[BTC:TREND]"  # Trend update
    REV = "[BTC:REV]"  # Reversal detected
    DIV = "[BTC:DIV]"  # Divergence detected


class StrategyTags:
    """Tags for Strategy module"""

    GEN = "[STRAT:GEN]"  # Signal generated
    EVAL = "[STRAT:EVAL]"  # Strategy evaluated
    SKIP = "[STRAT:SKIP]"  # Signal skipped
    ERR = "[STRAT:ERR]"  # Error


class DataTags:
    """Tags for Data collection"""

    REC = "[DATA:REC]"  # Data recorded
    SYNC = "[DATA:SYNC]"  # Data synced
    ERR = "[DATA:ERR]"  # Error


# Shorthand aliases for common use
G = GridTags
S = SignalTags
P = PositionTags
O = OrderTags  # noqa: E741 - public shorthand alias, parallel to G/S/P
SYS = SystemTags
BTC = BTCTags
STRAT = StrategyTags
DATA = DataTags
