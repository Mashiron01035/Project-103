"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 19.06.2026.
    File        : resilience.py
    Project     : Project-103
    Description : Position recovery and background-task resilience for TradingBot — restores positions/orders after restart and triggers a graceful shutdown on unexpected task death.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from src.execution.position_recovery import PositionRecoveryManager

logger = logging.getLogger(__name__)


class ResilienceMixin:
    async def _recover_positions(self) -> None:
        """Recover open positions from previous session after restart."""
        try:
            logger.info("Checking for positions to recover...")

            recovery_manager = PositionRecoveryManager(
                connector=self.connector,
                database=self.database,
                signal_lifecycle_manager=self.signal_lifecycle,
            )

            # recover_orphans=True ensures positions that exist on exchange
            # but not in database are still tracked (prevents duplicates)
            result = await recovery_manager.recover_positions(recover_orphans=True)

            if result.recovered:
                orphan_count = sum(1 for r in result.recovered if r.get("is_orphan"))
                db_count = len(result.recovered) - orphan_count

                if orphan_count > 0:
                    logger.info(
                        f"✓ Recovered {len(result.recovered)} positions "
                        f"({db_count} from DB, {orphan_count} orphans from exchange)"
                    )
                else:
                    logger.info(
                        f"✓ Recovered {len(result.recovered)} positions from previous session"
                    )
            else:
                logger.info("  No positions to recover")

            # Restore strategy batch state from recovered positions
            if self.signal_lifecycle and self.strategy_manager and result.recovered:
                await self._restore_strategy_batch_state()

            # Invalidate position cache after recovery
            if self.signal_lifecycle:
                self.signal_lifecycle.invalidate_position_cache()

            # Clean up orphan orders (orders without matching positions)
            cleanup_result = await recovery_manager.clean_orphan_orders()
            if cleanup_result.get("cancelled", 0) > 0:
                logger.info(f"✓ Cleaned up {cleanup_result['cancelled']} orphan orders")

            # Clean up duplicate orders (multiple SL/TP for same position)
            duplicates_result = await recovery_manager.clean_duplicate_orders()
            if duplicates_result.get("cancelled", 0) > 0:
                logger.info(f"✓ Cleaned up {duplicates_result['cancelled']} duplicate orders")

        except Exception as e:
            logger.warning(f"Position recovery failed: {e}")
            logger.info("  Continuing without recovery...")

    async def _restore_strategy_batch_state(self) -> None:
        """
        After position recovery, rebuild strategy batch state from tracked signals.
        Prevents grid (fan) strategies from opening duplicate grid positions.
        """
        try:
            # Extract batch info from all recovered tracked signals
            batch_info = []
            for signal_id, tracked in self.signal_lifecycle.tracked_signals.items():
                metadata = None
                if tracked.signal:
                    # tracked.signal can be a Signal dataclass or a dict (from recovery)
                    if hasattr(tracked.signal, "metadata"):
                        metadata = tracked.signal.metadata
                    elif isinstance(tracked.signal, dict):
                        metadata = tracked.signal.get("metadata", {})

                if not metadata or not isinstance(metadata, dict):
                    continue

                batch_info.append(
                    {
                        "symbol": tracked.symbol,
                        "strategy_name": tracked.strategy_name,
                        "fan_batch_id": metadata.get("fan_batch_id"),
                        "level_key": metadata.get("level_key"),
                        "status": tracked.status,
                    }
                )

            if not batch_info:
                return

            # Call restore_batch_state on each strategy that supports it
            for strategy_name in self.strategy_manager.list_strategies(active_only=True):
                strategy = self.strategy_manager.get_strategy(strategy_name)
                if strategy and hasattr(strategy, "restore_batch_state"):
                    strategy.restore_batch_state(batch_info)

            logger.info(f"✓ Strategy batch state restored from {len(batch_info)} tracked signals")

        except Exception as e:
            logger.warning(f"Failed to restore strategy batch state: {e}")

    def _on_background_task_done(self, task: asyncio.Task) -> None:
        """Callback fired when any background task from run() finishes.

        Normal path: task was cancelled during shutdown — ignore.
        Abnormal paths:
          - Task raised an uncaught exception → log critical, trigger shutdown,
            send Telegram alert.
          - Task returned cleanly while bot is still running → loops are not
            supposed to exit, so treat as degraded state.
        """
        if task.cancelled():
            return

        name = task.get_name()
        exc = task.exception()

        if exc is not None:
            logger.critical(
                f"Background task '{name}' died with uncaught exception: {exc}",
                exc_info=exc,
            )
        elif self.state.is_running:
            logger.critical(f"Background task '{name}' exited unexpectedly (bot still running)")
        else:
            return  # Clean exit during shutdown — normal.

        self.shutdown_event.set()

        if self.alert_manager:
            asyncio.create_task(
                self._send_task_death_alert(name, exc),
                name=f"task_death_alert_{name}",
            )

    async def _send_task_death_alert(self, task_name: str, exc: Optional[BaseException]) -> None:
        """Send Telegram alert about background task death. Fire-and-forget."""
        try:
            from src.monitoring.alerts.alert_manager import TelegramAlert

            if exc is not None:
                exc_text = f"{type(exc).__name__}: {exc}"
                message = (
                    f"Task '{task_name}' crashed with uncaught exception. "
                    f"Bot is shutting down gracefully."
                )
            else:
                exc_text = "Task exited without exception"
                message = (
                    f"Task '{task_name}' exited unexpectedly while bot was running. "
                    f"Bot is shutting down gracefully."
                )

            await self.alert_manager.send_alert(
                TelegramAlert(
                    type="system",
                    priority="critical",
                    title="🚨 Background Task Died",
                    message=message,
                    data={
                        "Task": task_name,
                        "Exception": exc_text,
                        "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
            )
        except Exception as alert_err:
            logger.error(f"Failed to send task-death Telegram alert: {alert_err}")
