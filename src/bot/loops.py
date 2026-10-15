"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 19.06.2026.
    File        : loops.py
    Project     : Project-103
    Description : Main run() entry and background loops for TradingBot: health-check, heartbeat, strategy warmup, and the per-minute strategy analysis loop.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class LoopsMixin:
    async def run(self) -> None:
        """Main run loop"""
        if not self.state.is_initialized:
            await self.initialize()

        logger.info("=" * 60)
        logger.info("STARTING TRADING BOT")
        logger.info("=" * 60)

        self.state.is_running = True
        self.state.start_time = time.time()  # Use UNIX timestamp for consistency

        # Warmup runs AFTER initialize() (so position_recovery is done) and
        # BEFORE strategy_loop, so session ranges / counters are already hot.
        try:
            await self._warmup_strategies()
        except Exception as e:
            logger.error(f"Warmup failed (continuing without): {e}", exc_info=True)

        try:
            # Create background tasks
            self.tasks = [
                asyncio.create_task(self._health_check_loop(), name="health_check"),
                asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
                asyncio.create_task(self._strategy_loop(), name="strategy_loop"),
            ]

            # Detect unexpected task death (uncaught exceptions) and trigger shutdown.
            for t in self.tasks:
                t.add_done_callback(self._on_background_task_done)

            # Send startup notification
            if self.alert_manager:
                from src.monitoring.alerts.alert_manager import TelegramAlert

                await self.alert_manager.send_alert(
                    TelegramAlert(
                        type="system",
                        priority="medium",
                        title="Trading Bot Started",
                        message=f"Bot started in {self.state.mode} mode",
                        data={
                            "Mode": self.state.mode,
                            "Exchange": self.args.exchange,
                            "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        },
                    )
                )

            logger.info("Bot is running. Press Ctrl+C to stop.")

            # Wait for shutdown signal
            await self.shutdown_event.wait()

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")

        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)

        finally:
            await self.shutdown()

    async def _health_check_loop(self) -> None:
        """Background health checking"""
        while self.state.is_running:
            try:
                health = await self.health_checker.check_all()

                if health.status == "unhealthy":
                    logger.warning("System health degraded!")

                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(30)

    async def _heartbeat_loop(self) -> None:
        """Heartbeat logger - logs only when state changes"""
        last_state = None  # Track previous state to detect changes
        last_log_time = 0  # Force log every 10 minutes even if no changes

        while self.state.is_running:
            try:
                uptime = time.time() - self.state.start_time
                hours = int(uptime // 3600)
                minutes = int((uptime % 3600) // 60)

                # Get signal lifecycle stats
                current_state = {}
                lifecycle_stats = ""

                if self.signal_lifecycle:
                    stats = self.signal_lifecycle.get_stats()
                    balance = stats.get("current_balance", 0)
                    initial = stats.get("initial_balance", 10000)
                    balance_change = ((balance - initial) / initial * 100) if initial > 0 else 0

                    # Get unrealized PnL from exchange
                    unrealized_pnl = 0.0
                    try:
                        if self.connector:
                            positions = await self.connector.get_positions()
                            for pos in positions:
                                unrealized_pnl += float(pos.get("unrealized_pnl", 0))
                    except Exception as e:
                        logger.debug(f"Failed to get unrealized PnL: {e}")

                    # State for change detection (key metrics only)
                    current_state = {
                        "open": stats["active_signals"],
                        "executed": stats["signals_executed"],
                        "closed": stats["signals_closed"],
                        "balance": round(balance, 2),  # Round to avoid float noise
                        "unrealized": round(unrealized_pnl, 2),
                    }

                    # Format unrealized PnL
                    upnl_str = f", uPnL: ${unrealized_pnl:+.2f}" if unrealized_pnl != 0 else ""

                    lifecycle_stats = (
                        f", Signals: {stats['signals_executed']}/{stats['signals_received']}, "
                        f"Closed: {stats['signals_closed']} "
                        f"(Win: {stats['win_rate']:.0%}, Balance: ${balance:.2f} ({balance_change:+.1f}%){upnl_str})"
                    )

                # Check if state changed or 10 minutes passed
                now = time.time()
                state_changed = current_state != last_state
                force_log = (now - last_log_time) >= 600  # 10 minutes

                if state_changed or force_log:
                    signal_open = current_state.get("open", 0)
                    open_str = f"Open: {signal_open}"

                    logger.info(
                        f"Heartbeat - Uptime: {hours}h {minutes}m, "
                        f"{open_str}"
                        f"{lifecycle_stats}"
                    )
                    last_state = current_state.copy()
                    last_log_time = now

                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                await asyncio.sleep(60)

    async def _warmup_strategies(self) -> None:
        """Replay recent historical candles so strategies start with warm state.

        Order inside run(): initialize() (incl. position_recovery + restore_batch_state)
        → _warmup_strategies() → _strategy_loop(). Warmup replays N candles through
        StrategyManager.replay_warmup(), then calls reset_warmup_state() on every
        active strategy to drop transient batches, and finally re-runs
        _restore_strategy_batch_state() so any open positions recovered earlier
        stay pinned to their grid levels.
        """
        if not self.strategy_manager or not self.data_provider:
            logger.info("warmup: strategy_manager or data_provider unavailable, skipping")
            return

        warmup_cfg = getattr(self.config, "warmup", None)

        def _cfg(key, default):
            if warmup_cfg is None:
                return default
            if isinstance(warmup_cfg, dict):
                return warmup_cfg.get(key, default)
            return getattr(warmup_cfg, key, default)

        if not bool(_cfg("enabled", True)):
            logger.info("warmup: disabled in config")
            return

        lookback = int(_cfg("lookback_candles", 2000))
        min_warmup = int(_cfg("min_indicator_warmup", 300))

        signal_pairs_cfg = getattr(self.config, "signal_pairs", []) or []
        grid_pairs_cfg = getattr(self.config, "grid_pairs", []) or []
        strategy_pairs = getattr(self, "strategy_pairs", None) or []
        confluence_pairs = getattr(self, "confluence_pairs", None) or []
        all_pairs = list(set(signal_pairs_cfg + grid_pairs_cfg + strategy_pairs + confluence_pairs))

        if not all_pairs:
            logger.info("warmup: no pairs configured, skipping")
            return

        # Timeframe — take the first enabled strategy's first timeframe (matches _strategy_loop)
        tf = "1m"
        for s in self.strategy_manager._strategies.values():
            if s.enabled and hasattr(s, "timeframes") and s.timeframes:
                tf = s.timeframes[0]
                break

        logger.info("=" * 60)
        logger.info(f"WARMUP: replaying {lookback} candles × {len(all_pairs)} pair(s) on {tf}")
        logger.info("=" * 60)

        t0 = time.time()

        for symbol in all_pairs:
            try:
                candles = await self.data_provider.get_candles(
                    symbol=symbol,
                    timeframe=tf,
                    limit=lookback,
                )
                if not candles or len(candles) < min_warmup + 10:
                    logger.warning(
                        f"warmup: not enough candles for {symbol} "
                        f"({len(candles) if candles else 0}/{min_warmup + 10}), skipping"
                    )
                    continue

                # Normalise to indicator Candle (same shape _strategy_loop feeds)
                from src.indicators.base_indicator import Candle as IndicatorCandle

                indicator_candles = []
                for c in candles:
                    if hasattr(c, "to_dict"):
                        indicator_candles.append(
                            IndicatorCandle(
                                timestamp=c.timestamp,
                                open=c.open,
                                high=c.high,
                                low=c.low,
                                close=c.close,
                                volume=c.volume,
                                spread=getattr(c, "spread", 0.0) or 0.0,
                            )
                        )
                    else:
                        indicator_candles.append(c)

                await self.strategy_manager.replay_warmup(
                    symbol=symbol,
                    candles=indicator_candles,
                    min_indicator_warmup=min_warmup,
                )
            except Exception as e:
                logger.error(f"warmup error for {symbol}: {e}", exc_info=True)

        # Drop state accumulated from replayed signals (they're never closed via
        # notify_trade_closed, so batch_id / active_levels would leak otherwise).
        for name in self.strategy_manager.list_strategies(active_only=True):
            strategy = self.strategy_manager.get_strategy(name)
            if strategy is None:
                continue
            try:
                strategy.reset_warmup_state()
            except Exception as e:
                logger.error(f"warmup: reset_warmup_state({name}) failed: {e}", exc_info=True)

        # Reattach batch state for any open positions recovered in initialize().
        if self.signal_lifecycle:
            try:
                await self._restore_strategy_batch_state()
            except Exception as e:
                logger.error(f"warmup: restore_batch_state failed: {e}", exc_info=True)

        elapsed = time.time() - t0
        logger.info("=" * 60)
        logger.info(f"WARMUP COMPLETE in {elapsed:.1f}s")
        logger.info("=" * 60)

    async def _strategy_loop(self) -> None:
        """Strategy analysis loop - runs every minute to check for signals"""
        if not self.strategy_manager:
            logger.warning("No strategy manager, strategy loop disabled")
            return

        # Wait for initial data to be collected
        await asyncio.sleep(10)

        # Get pair groups from config (separated to avoid conflicts)
        signal_pairs = getattr(self.config, "signal_pairs", [])
        grid_pairs = getattr(self.config, "grid_pairs", [])

        # Fallback to active_pairs if new config not set
        if not signal_pairs and not grid_pairs:
            active_pairs = getattr(self.config, "active_pairs", [])
            signal_pairs = active_pairs
            grid_pairs = []
            if active_pairs:
                logger.warning(
                    "Using legacy active_pairs config. Consider using signal_pairs/grid_pairs"
                )

        # Determine signal pairs priority:
        # 1. confluence_pairs (for confluence_reversal strategy)
        # 2. strategy_pairs (from strategies.yaml for mean_reversion, low_vol_scalper, etc.)
        # 3. signal_pairs (from config.yaml)
        if self.confluence_pairs:
            strategy_signal_pairs = self.confluence_pairs
            logger.info(
                f"Using confluence pairs for signal generation: {len(strategy_signal_pairs)} pairs"
            )
        elif self.strategy_pairs:
            strategy_signal_pairs = self.strategy_pairs
            logger.info(
                f"Using strategy pairs for signal generation: {len(strategy_signal_pairs)} pairs"
            )
        else:
            strategy_signal_pairs = signal_pairs
            if strategy_signal_pairs:
                logger.info(f"Using signal_pairs from config: {len(strategy_signal_pairs)} pairs")

        # All pairs for price updates (union of all groups)
        all_pairs = list(set(strategy_signal_pairs + grid_pairs))

        logger.info(
            f"Strategy loop started - Signal pairs: {len(strategy_signal_pairs)}, "
            f"Grid pairs: {len(grid_pairs)}, Total: {len(all_pairs)}"
        )

        while self.state.is_running:
            try:
                # Process all pairs for price updates and candle fetching
                for symbol in all_pairs:
                    try:
                        # Get candles from data provider
                        if not self.data_provider:
                            continue

                        # Get timeframe from active strategy config (default 15m)
                        strategy_tf = "15m"
                        if self.strategy_manager and self.strategy_manager._strategies:
                            for s in self.strategy_manager._strategies.values():
                                if s.enabled and hasattr(s, "timeframes") and s.timeframes:
                                    strategy_tf = s.timeframes[0]
                                    break

                        # 60000 1m candles ≈ 30 working days — a 4h EMA50 is fully
                        # converged (seed bias <1%). At 15000 (~10 days) the 4h
                        # EMA50 had ~67% seed bias, which caused a live vs backtest
                        # parity bug in direction_filter.
                        candles = await self.data_provider.get_candles(
                            symbol=symbol, timeframe=strategy_tf, limit=60000
                        )

                        if not candles or len(candles) < 60:
                            logger.debug(
                                f"Not enough candles for {symbol}: {len(candles) if candles else 0}"
                            )
                            continue

                        # Convert to Candle objects if needed
                        from src.indicators.base_indicator import Candle as IndicatorCandle

                        indicator_candles = []
                        for c in candles:
                            if hasattr(c, "to_dict"):
                                indicator_candles.append(
                                    IndicatorCandle(
                                        timestamp=c.timestamp,
                                        open=c.open,
                                        high=c.high,
                                        low=c.low,
                                        close=c.close,
                                        volume=c.volume,
                                        spread=getattr(c, "spread", 0.0) or 0.0,
                                    )
                                )
                            else:
                                indicator_candles.append(c)

                        # Update current price from live tick (not from candle close)
                        # Candles are used only for indicator calculation (closed candles)
                        # but position monitoring needs real-time price
                        current_price = 0
                        if self.data_provider:
                            try:
                                ticker = await self.data_provider.get_ticker(symbol)
                                if ticker:
                                    current_price = ticker.last_price
                            except Exception:
                                pass
                        # Fallback to last closed candle if tick unavailable
                        if current_price <= 0 and indicator_candles:
                            current_price = indicator_candles[-1].close
                        if self.signal_lifecycle and current_price > 0:
                            self.signal_lifecycle.update_price(symbol, current_price)

                        # Generate and process signals ONLY for strategy_signal_pairs
                        if symbol in strategy_signal_pairs:
                            # At candle close: run trailing check (between candles
                            # only max_loss/profit_target are checked)
                            if self.signal_lifecycle:
                                try:
                                    self.signal_lifecycle._is_candle_close_check = True
                                    await self.signal_lifecycle._check_positions()
                                finally:
                                    self.signal_lifecycle._is_candle_close_check = False

                            signals = await self.strategy_manager.generate_signals(
                                symbol=symbol, candles=indicator_candles
                            )
                            for signal in signals:
                                await self._process_signal(signal)

                    except Exception as e:
                        logger.error(f"Error analyzing {symbol}: {e}")

                # Sync to candle boundary: wait until ~2s after next minute
                # so the previous 1m candle is fully closed on the exchange
                now = datetime.now(timezone.utc) if hasattr(datetime, "now") else datetime.utcnow()
                seconds_into_minute = now.second + now.microsecond / 1_000_000
                wait = 62 - seconds_into_minute  # 2s after next minute boundary
                if wait <= 0:
                    wait += 60
                await asyncio.sleep(wait)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Strategy loop error: {e}")
                await asyncio.sleep(30)

        logger.info("Strategy loop stopped")
