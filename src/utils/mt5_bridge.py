"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 04.03.2026.
    File        : mt5_bridge.py
    Project     : Project-103
    Description : Unified `mt5` object working on both Windows (native MetaTrader5) and Linux (mt5linux RPyC proxy).

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import os
import platform

logger = logging.getLogger(__name__)

mt5 = None
MT5_AVAILABLE = False

_system = platform.system()

if _system == "Windows":
    try:
        import MetaTrader5 as mt5  # noqa: F811

        MT5_AVAILABLE = True
        logger.info("MT5 bridge: using native MetaTrader5 (Windows)")
    except ImportError:
        logger.warning("MT5 bridge: MetaTrader5 package not installed")

else:
    # Linux / macOS / Docker — use mt5linux RPyC proxy
    try:
        from mt5linux import MetaTrader5

        _host = os.environ.get("MT5_HOST", "mt5")
        _port = int(os.environ.get("MT5_PORT", "8001"))

        mt5 = MetaTrader5(host=_host, port=_port)  # noqa: F811
        MT5_AVAILABLE = True
        logger.info(f"MT5 bridge: using mt5linux RPyC proxy ({_host}:{_port})")
    except ImportError:
        logger.warning("MT5 bridge: mt5linux package not installed")
    except Exception as e:
        logger.error(f"MT5 bridge: failed to initialize mt5linux: {e}")
