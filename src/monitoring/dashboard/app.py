"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : app
    Project     : Project-103
    Description : Trading dashboard FastAPI application. Provides web interface for monitoring and management.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import logging
from typing import Any, Dict, List

from src.monitoring.health_checker import HealthChecker
from src.monitoring.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)


class TradingDashboard:
    """Web dashboard for trading bot"""

    def __init__(
        self,
        performance_tracker: PerformanceTracker,
        health_checker: HealthChecker,
        trading_controller=None,
    ):
        """
        Initialize trading dashboard

        Args:
            performance_tracker: Performance tracker instance
            health_checker: Health checker instance
            trading_controller: TradingController instance for runtime control
        """
        self.performance_tracker = performance_tracker
        self.health_checker = health_checker
        self.trading_controller = trading_controller
        self.signal_lifecycle = None  # Set externally for leverage/hedge control
        self.database = None  # Set externally for stats
        self.app = None
        self.websocket_connections: List = []

        # Initialize FastAPI app
        self._initialize_app()

    def _initialize_app(self):
        """Initialize FastAPI application"""
        try:
            from pathlib import Path

            from fastapi import FastAPI
            from fastapi.staticfiles import StaticFiles
            from fastapi.templating import Jinja2Templates

            self.app = FastAPI(title="Trading Bot Dashboard")

            # Setup static files and templates
            dashboard_dir = Path(__file__).parent
            static_dir = dashboard_dir / "static"
            templates_dir = dashboard_dir / "templates"

            # Create directories if they don't exist
            static_dir.mkdir(exist_ok=True)
            templates_dir.mkdir(exist_ok=True)

            # Mount static files if directory exists
            if static_dir.exists():
                self.app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

            # Setup templates if directory exists
            if templates_dir.exists():
                self.templates = Jinja2Templates(directory=str(templates_dir))

            # Setup routes
            self._setup_routes()

            logger.info("FastAPI dashboard initialized successfully")

        except ImportError:
            logger.warning("FastAPI not installed. Install with: pip install fastapi uvicorn")
            self.app = None

    def _setup_routes(self):
        """Setup API routes"""
        if not self.app:
            return

        from fastapi import Request, WebSocket
        from fastapi.responses import HTMLResponse, JSONResponse

        @self.app.get("/")
        async def index(request: Request):
            """Dashboard home page"""
            try:
                snapshot = self.performance_tracker.get_performance_snapshot("24h")
                health = await self.health_checker.check_all()

                return self.templates.TemplateResponse(
                    "index.html", {"request": request, "snapshot": snapshot, "health": health}
                )
            except Exception as e:
                logger.error(f"Error rendering dashboard: {e}")
                return HTMLResponse(
                    content="<h1>Trading Bot Dashboard</h1><p>Performance: Active</p>",
                    status_code=200,
                )

        @self.app.get("/api/performance/{period}")
        async def get_performance(period: str):
            """Get performance metrics for period"""
            try:
                snapshot = self.performance_tracker.get_performance_snapshot(period)
                return JSONResponse(content=snapshot.to_dict())
            except Exception as e:
                logger.error(f"Error getting performance: {e}")
                return JSONResponse(content={"error": str(e)}, status_code=500)

        @self.app.get("/api/trades")
        async def get_trades(limit: int = 50):
            """Get recent trades"""
            try:
                trades = self.performance_tracker.trades[-limit:]
                return JSONResponse(content=[t.to_dict() for t in trades])
            except Exception as e:
                logger.error(f"Error getting trades: {e}")
                return JSONResponse(content={"error": str(e)}, status_code=500)

        @self.app.get("/api/positions")
        async def get_positions():
            """Get open positions"""
            try:
                positions = self.performance_tracker.open_positions
                return JSONResponse(content={id: pos.to_dict() for id, pos in positions.items()})
            except Exception as e:
                logger.error(f"Error getting positions: {e}")
                return JSONResponse(content={"error": str(e)}, status_code=500)

        @self.app.get("/api/equity-curve")
        async def get_equity_curve():
            """Get equity curve data"""
            try:
                df = self.performance_tracker.get_equity_curve()
                return JSONResponse(content=df.to_dict(orient="records"))
            except Exception as e:
                logger.error(f"Error getting equity curve: {e}")
                return JSONResponse(content={"error": str(e)}, status_code=500)

        @self.app.get("/api/health")
        async def get_health():
            """Get system health status"""
            try:
                health = await self.health_checker.check_all()
                return JSONResponse(content=health.to_dict())
            except Exception as e:
                logger.error(f"Error getting health: {e}")
                return JSONResponse(content={"error": str(e)}, status_code=500)

        # ==================== Trading Control API ====================

        @self.app.get("/api/control/status")
        async def get_control_status():
            """Get trading controller status"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            return JSONResponse(content=self.trading_controller.get_status())

        @self.app.post("/api/control/trading/enable")
        async def enable_trading():
            """Enable trading"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            result = await self.trading_controller.enable_trading()
            return JSONResponse(content=result)

        @self.app.post("/api/control/trading/disable")
        async def disable_trading(reason: str = "Manual disable via API"):
            """Disable trading"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            result = await self.trading_controller.disable_trading(reason)
            return JSONResponse(content=result)

        @self.app.post("/api/control/trading/emergency-stop")
        async def emergency_stop(reason: str = "Emergency stop via API"):
            """Emergency stop - disable trading and close positions"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            result = await self.trading_controller.emergency_stop(reason)
            return JSONResponse(content=result)

        @self.app.get("/api/control/strategies")
        async def list_strategies():
            """List all strategies"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            return JSONResponse(content=self.trading_controller.list_strategies())

        @self.app.post("/api/control/strategy/{strategy_name}/enable")
        async def enable_strategy(strategy_name: str):
            """Enable a specific strategy"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            result = await self.trading_controller.enable_strategy(strategy_name)
            return JSONResponse(content=result)

        @self.app.post("/api/control/strategy/{strategy_name}/disable")
        async def disable_strategy(strategy_name: str):
            """Disable a specific strategy"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            result = await self.trading_controller.disable_strategy(strategy_name)
            return JSONResponse(content=result)

        @self.app.post("/api/control/pairs/add")
        async def add_pair(symbol: str):
            """Add a trading pair"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            result = await self.trading_controller.add_pair(symbol.upper())
            return JSONResponse(content=result)

        @self.app.post("/api/control/pairs/remove")
        async def remove_pair(symbol: str):
            """Remove a trading pair"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            result = await self.trading_controller.remove_pair(symbol.upper())
            return JSONResponse(content=result)

        @self.app.post("/api/control/config/reload")
        async def reload_config():
            """Reload configuration from files"""
            if not self.trading_controller:
                return JSONResponse(
                    content={"error": "Controller not initialized"}, status_code=503
                )
            result = await self.trading_controller.reload_config()
            return JSONResponse(content=result)

        # ==================== Extended Control API ====================

        @self.app.get("/api/control/balance")
        async def get_balance():
            """Get balance and P&L stats"""
            if not self.signal_lifecycle:
                return JSONResponse(
                    content={"error": "Signal lifecycle not initialized"}, status_code=503
                )
            try:
                stats = self.signal_lifecycle.get_stats()
                return JSONResponse(
                    content={
                        "success": True,
                        "initial_balance": stats.get("initial_balance", 10000),
                        "current_balance": stats.get("current_balance", 10000),
                        "total_pnl_usdt": stats.get("total_pnl_usdt", 0),
                        "max_balance": stats.get("max_balance", 10000),
                        "max_drawdown_percent": stats.get("max_drawdown_percent", 0),
                        "active_signals": stats.get("active_signals", 0),
                        "signals_closed": stats.get("signals_closed", 0),
                        "winners": stats.get("winners", 0),
                        "losers": stats.get("losers", 0),
                        "win_rate": stats.get("win_rate", 0),
                    }
                )
            except Exception as e:
                return JSONResponse(content={"error": str(e)}, status_code=500)

        @self.app.get("/api/control/leverage")
        async def get_leverage():
            """Get current leverage"""
            if not self.signal_lifecycle:
                return JSONResponse(
                    content={"error": "Signal lifecycle not initialized"}, status_code=503
                )
            return JSONResponse(
                content={"success": True, "leverage": self.signal_lifecycle.config.leverage}
            )

        @self.app.post("/api/control/leverage")
        async def set_leverage(leverage: int):
            """Set leverage (1-100)"""
            if not self.signal_lifecycle:
                return JSONResponse(
                    content={"error": "Signal lifecycle not initialized"}, status_code=503
                )
            if leverage < 1 or leverage > 100:
                return JSONResponse(
                    content={"error": "Leverage must be between 1 and 100"}, status_code=400
                )
            old_leverage = self.signal_lifecycle.config.leverage
            self.signal_lifecycle.config.leverage = leverage
            return JSONResponse(
                content={"success": True, "old_leverage": old_leverage, "new_leverage": leverage}
            )

        @self.app.get("/api/control/hedge")
        async def get_hedge():
            """Get hedge mode status"""
            if not self.signal_lifecycle:
                return JSONResponse(
                    content={"error": "Signal lifecycle not initialized"}, status_code=503
                )
            return JSONResponse(
                content={"success": True, "hedge_mode": self.signal_lifecycle.config.hedge_mode}
            )

        @self.app.post("/api/control/hedge")
        async def set_hedge(enabled: bool):
            """Set hedge mode on/off"""
            if not self.signal_lifecycle:
                return JSONResponse(
                    content={"error": "Signal lifecycle not initialized"}, status_code=503
                )
            old_mode = self.signal_lifecycle.config.hedge_mode
            self.signal_lifecycle.config.hedge_mode = enabled
            return JSONResponse(
                content={"success": True, "old_mode": old_mode, "new_mode": enabled}
            )

        @self.app.get("/api/control/stats")
        async def get_stats():
            """Get trading statistics from database"""
            if not self.database:
                return JSONResponse(content={"error": "Database not initialized"}, status_code=503)
            try:
                stats = self.database.get_signal_stats()
                if not stats:
                    return JSONResponse(content={"success": True, "message": "No trading data yet"})
                return JSONResponse(content={"success": True, **stats})
            except Exception as e:
                return JSONResponse(content={"error": str(e)}, status_code=500)

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time updates"""
            await websocket.accept()
            self.websocket_connections.append(websocket)

            try:
                while True:
                    data = await self._get_realtime_data()
                    await websocket.send_json(data)
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
            finally:
                if websocket in self.websocket_connections:
                    self.websocket_connections.remove(websocket)

    async def _get_realtime_data(self) -> Dict[str, Any]:
        """Get real-time data for WebSocket"""
        from datetime import datetime

        return {
            "timestamp": int(datetime.now().timestamp() * 1000),
            "positions": len(self.performance_tracker.open_positions),
            "balance": self.performance_tracker.current_balance,
            "daily_pnl": self.performance_tracker.daily_stats.get(
                datetime.now().strftime("%Y-%m-%d"), {}
            ).get("net_pnl", 0),
        }

    async def broadcast_update(self, data: Dict[str, Any]):
        """Broadcast update to all WebSocket clients"""
        for connection in self.websocket_connections[:]:
            try:
                await connection.send_json(data)
            except Exception as e:
                logger.error(f"Failed to send WebSocket update: {e}")
                self.websocket_connections.remove(connection)

    def run(self, host: str = "0.0.0.0", port: int = 8080):
        """
        Run dashboard server

        Args:
            host: Host address
            port: Port number
        """
        if not self.app:
            logger.error("Dashboard not initialized. Install FastAPI and uvicorn.")
            return

        try:
            import uvicorn

            logger.info(f"Starting dashboard on http://{host}:{port}")
            # Disable uvicorn's default logging config to prevent duplicate handlers
            uvicorn.run(self.app, host=host, port=port, log_config=None)
        except ImportError:
            logger.error("uvicorn not installed. Install with: pip install uvicorn")
        except Exception as e:
            logger.error(f"Failed to start dashboard: {e}")
