"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : example_usage
    Project     : Project-103
    Description : Example usage of the monitoring module. Demonstrates integration with trading bot components.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import threading
import time

from src.models.position import Position, PositionSide, PositionStatus
from src.models.trade import Trade
from src.monitoring import (
    HealthChecker,
    MetricsCalculator,
    PerformanceEvent,
    PerformanceTracker,
    TradingLogger,
)
from src.monitoring.alerts import AlertManager, TelegramAlert, TelegramNotifier
from src.monitoring.dashboard import TradingDashboard


class MonitoringExample:
    """Example implementation of monitoring system"""

    def __init__(self):
        """Initialize monitoring components"""
        # Core monitoring
        self.performance_tracker = PerformanceTracker(initial_balance=10000)
        self.logger = TradingLogger(log_level="INFO", log_file="logs/trading_bot.log")
        self.health_checker = HealthChecker(check_interval=30)

        # Alert system
        self.alert_manager = AlertManager()

        # Optional: Setup Telegram notifications
        # self.setup_telegram_notifications()

        # Optional: Setup dashboard
        # self.setup_dashboard()

        # Register health check components
        self.register_health_checks()

    def setup_telegram_notifications(self):
        """Setup Telegram notifications (requires bot token and chat ID)"""
        try:
            telegram = TelegramNotifier(bot_token="YOUR_BOT_TOKEN", chat_id="YOUR_CHAT_ID")
            self.alert_manager.register_notifier(telegram)
            self.logger.log_system("Telegram notifications enabled")
        except Exception as e:
            self.logger.log_error(e, context="telegram_setup")

    def setup_dashboard(self):
        """Setup web dashboard"""
        try:
            dashboard = TradingDashboard(self.performance_tracker, self.health_checker)

            # Run dashboard in separate thread
            dashboard_thread = threading.Thread(
                target=dashboard.run, kwargs={"host": "0.0.0.0", "port": 8080}, daemon=True
            )
            dashboard_thread.start()

            self.logger.log_system("Dashboard started", {"port": 8080})
        except Exception as e:
            self.logger.log_error(e, context="dashboard_setup")

    def register_health_checks(self):
        """Register components for health checking"""

        # Example health check functions
        async def check_system():
            """Check basic system health"""
            return {"status": "healthy", "details": {"system": "operational"}}

        self.health_checker.register_component("system", check_system)

    async def simulate_trade_lifecycle(self):
        """Simulate a complete trade lifecycle"""
        print("\n=== Simulating Trade Lifecycle ===\n")

        # Create position
        position = Position(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            status=PositionStatus.OPEN,
            quantity=0.1,
            entry_price=50000.0,
            mark_price=50000.0,
            stop_loss=49000.0,
            take_profit=[51000.0, 52000.0],
            leverage=5,
            strategy_name="momentum_strategy",
        )
        position.opened_at = int(time.time() * 1000)

        # Track position opened
        event = PerformanceEvent(
            event_type="trade_opened",
            timestamp=position.opened_at,
            position=position,
            symbol=position.symbol,
            side=position.side.value,
            quantity=position.quantity,
            price=position.entry_price,
            strategy_name=position.strategy_name,
        )
        self.performance_tracker.track_event(event)

        self.logger.log_system(
            "Position opened",
            {
                "symbol": position.symbol,
                "side": position.side.value,
                "entry": position.entry_price,
                "quantity": position.quantity,
            },
        )

        print(f"Position opened: {position.symbol} {position.side.value}")
        print(f"Entry: ${position.entry_price}, Quantity: {position.quantity}")

        # Simulate price movement
        await asyncio.sleep(2)

        # Update position price
        new_price = 51500.0
        position.update_price(new_price)

        print(f"\nPrice update: ${new_price}")
        print(f"Unrealized P&L: ${position.unrealized_pnl:.2f}")

        # Close position
        await asyncio.sleep(1)

        exit_price = 51200.0
        position.close(exit_price, reason="take_profit")

        # Create trade from closed position
        trade = Trade.from_position(position, exit_price=exit_price, exit_time=position.closed_at)
        trade.calculate_metrics()

        # Track trade closed
        event = PerformanceEvent(
            event_type="trade_closed",
            timestamp=trade.exit_time,
            trade=trade,
            symbol=trade.symbol,
            side=trade.side,
            pnl=trade.pnl,
            strategy_name=trade.strategy_name,
        )
        self.performance_tracker.track_event(event)

        self.logger.log_trade(trade, action="closed")

        print(f"\nPosition closed: {trade.symbol}")
        print(f"Exit: ${trade.exit_price}")
        print(f"P&L: ${trade.pnl:.2f} ({trade.pnl_percentage:.2f}%)")
        print(f"Holding time: {trade.hold_time / 3600:.1f} hours")

    async def display_performance_metrics(self):
        """Display performance metrics"""
        print("\n=== Performance Metrics ===\n")

        # Get 24h snapshot
        snapshot = self.performance_tracker.get_performance_snapshot("24h")

        print(f"Period: {snapshot.period}")
        print(f"Total Trades: {snapshot.total_trades}")
        print(f"Winning Trades: {snapshot.winning_trades}")
        print(f"Losing Trades: {snapshot.losing_trades}")
        print(f"Win Rate: {snapshot.win_rate:.1f}%")
        print(f"\nGross P&L: ${snapshot.gross_pnl:.2f}")
        print(f"Net P&L: ${snapshot.net_pnl:.2f}")
        print(f"ROI: {snapshot.roi:.2f}%")
        print(f"\nMax Drawdown: {snapshot.max_drawdown:.2f}%")
        print(f"Current Drawdown: {snapshot.current_drawdown:.2f}%")
        print(f"Sharpe Ratio: {snapshot.sharpe_ratio:.2f}")
        print(f"Sortino Ratio: {snapshot.sortino_ratio:.2f}")

        if snapshot.strategy_breakdown:
            print("\n Strategy Breakdown:")
            for strategy, metrics in snapshot.strategy_breakdown.items():
                print(f"  {strategy}:")
                print(f"    Trades: {metrics['trades']}")
                print(f"    Win Rate: {metrics['win_rate']:.1f}%")
                print(f"    P&L: ${metrics['pnl']:.2f}")

    async def display_advanced_metrics(self):
        """Display advanced calculated metrics"""
        print("\n=== Advanced Metrics ===\n")

        trades = self.performance_tracker.trades
        if not trades:
            print("No trades available for metrics calculation")
            return

        # Calculate various metrics
        win_rate = MetricsCalculator.calculate_win_rate(trades)
        expectancy = MetricsCalculator.calculate_expectancy(trades)
        profit_factor = MetricsCalculator.calculate_profit_factor(trades)

        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Expectancy: ${expectancy:.2f}")
        print(f"Profit Factor: {profit_factor:.2f}")

        # Risk metrics
        returns = [t.pnl_percentage for t in trades]
        if returns:
            sharpe = MetricsCalculator.calculate_sharpe_ratio(returns)
            sortino = MetricsCalculator.calculate_sortino_ratio(returns)
            var_95 = MetricsCalculator.calculate_var(returns, 0.95)
            cvar_95 = MetricsCalculator.calculate_cvar(returns, 0.95)

            print(f"\nSharpe Ratio: {sharpe:.2f}")
            print(f"Sortino Ratio: {sortino:.2f}")
            print(f"VaR (95%): {var_95:.2f}%")
            print(f"CVaR (95%): {cvar_95:.2f}%")

        # Position sizing recommendation
        if win_rate > 0 and profit_factor > 1:
            payoff_ratio = profit_factor
            kelly = MetricsCalculator.calculate_kelly_criterion(win_rate / 100, payoff_ratio)
            print(f"\nKelly Criterion: {kelly * 100:.2f}% of capital")

    async def check_system_health(self):
        """Check and display system health"""
        print("\n=== System Health ===\n")

        health = await self.health_checker.check_all()

        print(f"Overall Status: {health.status.upper()}")
        print(f"CPU Usage: {health.cpu_usage:.1f}%")
        print(f"Memory Usage: {health.memory_usage:.1f}%")
        print(f"Disk Usage: {health.disk_usage:.1f}%")
        print(f"Uptime: {health.uptime // 3600}h {(health.uptime % 3600) // 60}m")

        print("\nComponents:")
        for component in health.components:
            status_icon = "✓" if component.status == "healthy" else "✗"
            print(f"  {status_icon} {component.name}: {component.status}")
            if component.latency_ms:
                print(f"    Latency: {component.latency_ms:.1f}ms")

    async def send_test_alert(self):
        """Send test alert"""
        print("\n=== Sending Test Alert ===\n")

        alert = TelegramAlert(
            type="system",
            priority="medium",
            title="Monitoring System Test",
            message="This is a test alert from the monitoring system",
            data={
                "Test Parameter 1": "Value 1",
                "Test Parameter 2": 123.45,
                "Test Parameter 3": "Success",
            },
        )

        success = await self.alert_manager.send_alert(alert)
        print(f"Alert sent: {success}")

    async def run_example(self):
        """Run complete example"""
        print("\n" + "=" * 50)
        print("Trading Bot Monitoring Module - Example Usage")
        print("=" * 50)

        try:
            # Simulate trades
            await self.simulate_trade_lifecycle()
            await asyncio.sleep(1)

            await self.simulate_trade_lifecycle()
            await asyncio.sleep(1)

            # Display metrics
            await self.display_performance_metrics()
            await self.display_advanced_metrics()

            # Check health
            await self.check_system_health()

            # Send test alert (if notifications configured)
            # await self.send_test_alert()

            print("\n" + "=" * 50)
            print("Example completed successfully!")
            print("=" * 50 + "\n")

        except Exception as e:
            self.logger.log_error(e, context="example_execution")
            print(f"\nError during example execution: {e}")


async def main():
    """Main entry point"""
    example = MonitoringExample()
    await example.run_example()


if __name__ == "__main__":
    # Run example
    asyncio.run(main())
