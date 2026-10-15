"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 04.03.2026.
    File        : benchmark_latency.py
    Project     : Project-103
    Description : Latency benchmark for the trading-bot pipeline (data load, indicators, signal, sizing, order build).

    SPDX-License-Identifier: AGPL-3.0

"""

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _load_strategies():
    """Load strategies from strategies.yaml (same as main.py)."""
    from src.strategies import create_strategy
    from src.strategies.strategy_manager import StrategyManager

    sm = StrategyManager()

    config_path = Path("config/strategies.yaml")
    if not config_path.exists():
        return sm

    with open(config_path) as f:
        strategies_config = yaml.safe_load(f) or {}

    active = strategies_config.get("active_strategies", [])
    for name in active:
        config = strategies_config.get(name, {})
        strategy = create_strategy(name, config)
        if strategy:
            sm.register_strategy(strategy)

    return sm


def _rates_to_candles(rates):
    """Convert MT5 rates array to Candle objects."""
    from src.indicators.base_indicator import Candle

    return [
        Candle(
            timestamp=int(r[0]) * 1000,
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[4]),
            volume=float(r[5]),
        )
        for r in rates
    ]


class LatencyBenchmark:
    """Measures latency at each stage of the trading pipeline."""

    def __init__(self, symbol: str, iterations: int, live_order: bool = False):
        self.symbol = symbol
        self.iterations = iterations
        self.live_order = live_order
        self.results: dict = {}

    async def run(self):
        """Run all benchmarks."""
        print(f"\n{'=' * 70}")
        print(f"  LATENCY BENCHMARK — {self.symbol} x {self.iterations} iterations")
        print(f"{'=' * 70}\n")

        import MetaTrader5 as mt5

        if not mt5.initialize():
            print(f"MT5 initialization failed: {mt5.last_error()}")
            return

        account = mt5.account_info()
        if account:
            print(f"  MT5 Account: {account.login}")
            print(f"  Server: {account.server}")
            print(f"  Balance: ${account.balance:.2f}")
        print()

        # Load strategies
        sm = _load_strategies()
        strategy_names = [s.name for s in sm._strategies.values() if s.enabled]
        print(f"  Strategies: {', '.join(strategy_names) if strategy_names else 'none'}")
        print()

        await self._bench_mt5_tick(mt5)
        await self._bench_mt5_candles(mt5)
        await self._bench_indicators()
        await self._bench_strategy_analyze(sm)
        await self._bench_position_sizing()
        await self._bench_order_check(mt5)
        if self.live_order:
            await self._bench_order_send(mt5)
        else:
            print("  6b. MT5 order_send (SKIPPED — use --live-order)")
            print("    This is where 250ms+ broker latency shows up")
            print()
        await self._bench_full_pipeline(sm)

        self._print_summary()
        mt5.shutdown()

    async def _bench_mt5_tick(self, mt5):
        """Benchmark: MT5 tick fetch."""
        timings = []
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            tick = mt5.symbol_info_tick(self.symbol)
            elapsed = (time.perf_counter() - t0) * 1000
            if tick:
                timings.append(elapsed)
        self._record("1. MT5 tick fetch", timings)

    async def _bench_mt5_candles(self, mt5):
        """Benchmark: MT5 candle fetch."""
        timings = []
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 500)
            elapsed = (time.perf_counter() - t0) * 1000
            if rates is not None:
                timings.append(elapsed)
        self._record("2. MT5 candles (500x1m)", timings)

    async def _bench_indicators(self):
        """Benchmark: indicator calculation."""
        import MetaTrader5 as mt5

        from src.indicators.factory import get_indicator_factory

        factory = get_indicator_factory()
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 500)
        if rates is None:
            print("  [SKIP] Cannot fetch candles for indicator benchmark\n")
            return

        candles = _rates_to_candles(rates)
        indicators_config = ["ema{100}", "atr{14}"]

        timings = []
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            factory.calculate_multiple(candles=candles, indicators_config=indicators_config)
            elapsed = (time.perf_counter() - t0) * 1000
            timings.append(elapsed)
        self._record("3. Indicators (EMA+ATR)", timings)

    async def _bench_strategy_analyze(self, sm):
        """Benchmark: strategy analysis."""
        import MetaTrader5 as mt5

        from src.indicators.factory import get_indicator_factory

        if not sm._strategies:
            print("  [SKIP] No strategies loaded\n")
            return

        factory = get_indicator_factory()
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 500)
        if rates is None:
            print("  [SKIP] Cannot fetch candles\n")
            return

        candles = _rates_to_candles(rates)

        # Collect all indicator configs
        indicators_config = []
        for s in sm._strategies.values():
            cfg = s.get_indicators_config()
            if isinstance(cfg, list):
                indicators_config.extend(cfg)
        indicators_config = list(set(indicators_config))

        indicators = factory.calculate_multiple(
            candles=candles, indicators_config=indicators_config
        )

        timings = []
        signal_count = 0
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            for strategy in sm._strategies.values():
                if strategy.enabled:
                    result = await strategy.analyze(self.symbol, candles, indicators)
                    if result:
                        signal_count += len(result) if isinstance(result, list) else 1
            elapsed = (time.perf_counter() - t0) * 1000
            timings.append(elapsed)

        self._record(f"4. Strategy analyze() [{signal_count} sigs]", timings)

    async def _bench_position_sizing(self):
        """Benchmark: position sizing calculation."""
        from src.core.trading_pairs import trading_pairs

        # Warm up (first call loads YAML)
        trading_pairs.get_contract_size(self.symbol)

        timings = []
        for _ in range(self.iterations):
            t0 = time.perf_counter()
            risk_amount = 1000.0 * 0.02
            entry_price = 5200.0
            sl_pct = abs(5200.0 - 5170.0) / 5200.0
            pos_size = risk_amount / sl_pct
            cs = trading_pairs.get_contract_size(self.symbol)
            _ = pos_size / (entry_price * cs)
            elapsed = (time.perf_counter() - t0) * 1000
            timings.append(elapsed)
        self._record("5. Position sizing", timings)

    async def _bench_order_check(self, mt5):
        """Benchmark: MT5 order_check (local validation, no broker roundtrip)."""
        tick = mt5.symbol_info_tick(self.symbol)
        symbol_info = mt5.symbol_info(self.symbol)
        if not tick or not symbol_info:
            print("  [SKIP] Cannot get tick/symbol info\n")
            return

        timings = []
        for _ in range(self.iterations):
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": symbol_info.volume_min,
                "type": mt5.ORDER_TYPE_BUY,
                "price": tick.ask,
                "sl": round(tick.ask - 30.0, 2),
                "tp": round(tick.ask + 30.0, 2),
                "deviation": 20,
                "magic": 999999,
                "comment": "benchmark",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            t0 = time.perf_counter()
            mt5.order_check(request)
            elapsed = (time.perf_counter() - t0) * 1000
            timings.append(elapsed)
        self._record("6a. MT5 order_check (local)", timings)

    async def _bench_order_send(self, mt5):
        """Benchmark: real order_send roundtrip (BUY + immediate CLOSE)."""
        tick = mt5.symbol_info_tick(self.symbol)
        symbol_info = mt5.symbol_info(self.symbol)
        if not tick or not symbol_info:
            print("  [SKIP] Cannot get tick/symbol info\n")
            return

        vol = symbol_info.volume_min
        # Determine filling mode
        filling = mt5.ORDER_FILLING_IOC
        if symbol_info.filling_mode & 1:
            filling = mt5.ORDER_FILLING_FOK

        open_timings = []
        close_timings = []
        roundtrip_timings = []
        errors = 0

        # Fewer iterations for real orders (costs spread each time)
        n = min(self.iterations, 5)
        print(f"  6b. MT5 order_send (LIVE x{n} — min volume {vol})")

        for i in range(n):
            tick = mt5.symbol_info_tick(self.symbol)
            if not tick:
                errors += 1
                continue

            # --- OPEN ---
            open_req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": vol,
                "type": mt5.ORDER_TYPE_BUY,
                "price": tick.ask,
                "deviation": 50,
                "magic": 999999,
                "comment": "bench_open",
                "type_filling": filling,
            }

            t_total = time.perf_counter()
            t0 = time.perf_counter()
            result = mt5.order_send(open_req)
            open_ms = (time.perf_counter() - t0) * 1000

            if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
                rc = result.retcode if result else "None"
                comment = result.comment if result else "N/A"
                print(f"    [{i+1}] OPEN failed: rc={rc} ({comment})")
                errors += 1
                continue

            open_timings.append(open_ms)
            ticket = result.order

            # Brief pause to let position register
            await asyncio.sleep(0.05)

            # --- CLOSE ---
            tick = mt5.symbol_info_tick(self.symbol)
            close_req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": vol,
                "type": mt5.ORDER_TYPE_SELL,
                "price": tick.bid if tick else 0,
                "deviation": 50,
                "magic": 999999,
                "comment": "bench_close",
                "type_filling": filling,
                "position": ticket,
            }

            t0 = time.perf_counter()
            close_result = mt5.order_send(close_req)
            close_ms = (time.perf_counter() - t0) * 1000

            total_ms = (time.perf_counter() - t_total) * 1000

            if close_result and close_result.retcode == mt5.TRADE_RETCODE_DONE:
                close_timings.append(close_ms)
                roundtrip_timings.append(total_ms)
                print(
                    f"    [{i+1}] open={open_ms:.0f}ms  close={close_ms:.0f}ms  "
                    f"total={total_ms:.0f}ms"
                )
            else:
                rc = close_result.retcode if close_result else "None"
                print(f"    [{i+1}] open={open_ms:.0f}ms  CLOSE failed: rc={rc}")
                errors += 1

        print()
        if open_timings:
            self._record("6b. order_send OPEN", open_timings)
        if close_timings:
            self._record("6c. order_send CLOSE", close_timings)
        if roundtrip_timings:
            self._record("6d. OPEN+CLOSE roundtrip", roundtrip_timings)
        if errors:
            print(f"    Errors: {errors}/{n}\n")

    async def _bench_full_pipeline(self, sm):
        """Benchmark: full pipeline tick → candles → indicators → strategy → sizing."""
        import MetaTrader5 as mt5

        from src.core.trading_pairs import trading_pairs
        from src.indicators.factory import get_indicator_factory

        if not sm._strategies:
            print("  [SKIP] No strategies for full pipeline\n")
            return

        factory = get_indicator_factory()

        indicators_config = []
        for s in sm._strategies.values():
            cfg = s.get_indicators_config()
            if isinstance(cfg, list):
                indicators_config.extend(cfg)
        indicators_config = list(set(indicators_config))

        # Warm up
        trading_pairs.get_contract_size(self.symbol)

        timings = []
        stages = {
            "tick": [],
            "candles": [],
            "convert": [],
            "indicators": [],
            "strategy": [],
            "sizing": [],
        }

        for _ in range(self.iterations):
            t_total = time.perf_counter()

            t0 = time.perf_counter()
            tick = mt5.symbol_info_tick(self.symbol)
            stages["tick"].append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M1, 0, 500)
            stages["candles"].append((time.perf_counter() - t0) * 1000)

            if rates is None:
                continue

            t0 = time.perf_counter()
            candles = _rates_to_candles(rates)
            stages["convert"].append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            indicators = factory.calculate_multiple(
                candles=candles, indicators_config=indicators_config
            )
            stages["indicators"].append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            for strategy in sm._strategies.values():
                if strategy.enabled:
                    await strategy.analyze(self.symbol, candles, indicators)
            stages["strategy"].append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            if tick:
                entry = tick.ask
                sl_pct = 30.0 / entry
                pos_size = 20.0 / sl_pct
                cs = trading_pairs.get_contract_size(self.symbol)
                _ = pos_size / (entry * cs)
            stages["sizing"].append((time.perf_counter() - t0) * 1000)

            timings.append((time.perf_counter() - t_total) * 1000)

        self._record("7. FULL PIPELINE", timings)

        # Breakdown
        print("  Stage breakdown (avg):")
        for stage, times in stages.items():
            if times:
                avg = statistics.mean(times)
                pct = (avg / statistics.mean(timings) * 100) if timings else 0
                bar = "#" * max(1, int(pct / 2))
                print(f"    {stage:<14} {avg:>8.3f} ms  ({pct:>5.1f}%)  {bar}")
        print()

    def _record(self, name: str, timings: list):
        """Record and print benchmark results."""
        if not timings:
            print(f"  {name:<45} NO DATA\n")
            return

        avg = statistics.mean(timings)
        med = statistics.median(timings)
        mn = min(timings)
        mx = max(timings)
        p95 = sorted(timings)[int(len(timings) * 0.95)] if len(timings) >= 5 else mx
        std = statistics.stdev(timings) if len(timings) > 1 else 0

        self.results[name] = {
            "avg": avg,
            "median": med,
            "min": mn,
            "max": mx,
            "p95": p95,
            "std": std,
            "count": len(timings),
        }

        print(f"  {name}")
        print(f"    avg    {avg:>8.2f} ms  |  med    {med:>8.2f} ms")
        print(f"    min    {mn:>8.2f} ms  |  max    {mx:>8.2f} ms")
        print(f"    p95    {p95:>8.2f} ms  |  std    {std:>8.2f} ms")
        print(f"    n      {len(timings):>8}")
        print()

    def _print_summary(self):
        """Print final summary table."""
        print(f"{'=' * 70}")
        print("  SUMMARY")
        print(f"{'=' * 70}")
        print(f"  {'Stage':<35} {'Avg':>8} {'P95':>8} {'Max':>8}")
        print(f"  {'-' * 35} {'-' * 8} {'-' * 8} {'-' * 8}")

        for name, r in self.results.items():
            print(f"  {name:<35} {r['avg']:>7.1f}ms {r['p95']:>7.1f}ms {r['max']:>7.1f}ms")

        print(f"  {'-' * 35} {'-' * 8} {'-' * 8} {'-' * 8}")

        # Total pipeline
        pipeline = self.results.get("7. FULL PIPELINE")
        order_send = self.results.get("6b. order_send OPEN")
        if pipeline:
            total = pipeline["avg"]
            if order_send:
                total += order_send["avg"]
            print(f"  {'ESTIMATED TOTAL':>35} {total:>7.1f}ms")
        print()

        # Bottleneck
        non_total = {
            k: v
            for k, v in self.results.items()
            if not k.startswith("7.") and not k.startswith("6d.")
        }
        if non_total:
            slowest = max(non_total.items(), key=lambda x: x[1]["avg"])
            print(f"  Bottleneck: {slowest[0]} ({slowest[1]['avg']:.1f}ms avg)")
            print()


def main():
    parser = argparse.ArgumentParser(description="Trading bot latency benchmark")
    parser.add_argument("--symbol", default="XAUUSD.s", help="Symbol to test")
    parser.add_argument("--iterations", "-n", type=int, default=20, help="Iterations")
    parser.add_argument(
        "--live-order",
        action="store_true",
        help="Test real order_send (opens+closes min volume position)",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    benchmark = LatencyBenchmark(args.symbol, args.iterations, args.live_order)
    asyncio.run(benchmark.run())


if __name__ == "__main__":
    main()
