"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 19.06.2026.
    File        : signals.py
    Project     : Project-103
    Description : Signal handling for TradingBot.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging

logger = logging.getLogger(__name__)


class SignalsMixin:
    """Route a generated signal to execution; release strategy state on reject."""

    async def _process_signal(self, signal) -> None:
        try:
            if self.trading_controller and not self.trading_controller.is_trading_allowed():
                logger.debug(f"Signal ignored (trading disabled): {signal.side} {signal.symbol}")
                self._notify_strategy_rejected(signal)
                return

            result = None
            if self.signal_router:
                result = await self.signal_router.route_signal(signal)
            elif self.signal_lifecycle:
                result = await self.signal_lifecycle.process_signal(signal)
            else:
                logger.info(
                    f"SIGNAL (no tracking): {signal.side} {signal.symbol} @ {signal.entry_price:.4f}",
                    extra={"log_type": "signal"},
                )
                return

            # If signal was rejected, notify strategy to release level
            if not result:
                self._notify_strategy_rejected(signal)

        except Exception as e:
            logger.error(f"Error processing signal: {e}")
            self._notify_strategy_rejected(signal)

    def _notify_strategy_rejected(self, signal) -> None:
        """Notify strategy that its signal was rejected so it can release level state."""
        try:
            strategy_name = getattr(signal, "strategy_name", None)
            if strategy_name and self.strategy_manager:
                strategy = self.strategy_manager.get_strategy(strategy_name)
                if strategy and hasattr(strategy, "notify_signal_rejected"):
                    metadata = getattr(signal, "metadata", None)
                    strategy.notify_signal_rejected(signal.symbol, metadata)
        except Exception:
            pass
