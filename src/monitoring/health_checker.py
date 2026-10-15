"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : health_checker
    Project     : Project-103
    Description : Health checking module for trading bot. Monitors system components health and availability.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import psutil

logger = logging.getLogger(__name__)


@dataclass
class ComponentHealth:
    """Health status of a system component"""

    name: str
    status: str  # "healthy", "degraded", "unhealthy"
    latency_ms: Optional[float] = None
    last_check: Optional[int] = None
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "name": self.name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "last_check": self.last_check,
            "error_message": self.error_message,
            "details": self.details,
        }


@dataclass
class SystemHealth:
    """Overall system health status"""

    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: int
    components: List[ComponentHealth]

    # System metrics
    cpu_usage: float
    memory_usage: float
    disk_usage: float

    # Statistics
    uptime: int  # seconds
    total_checks: int
    failed_checks: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "status": self.status,
            "timestamp": self.timestamp,
            "components": [c.to_dict() for c in self.components],
            "cpu_usage": self.cpu_usage,
            "memory_usage": self.memory_usage,
            "disk_usage": self.disk_usage,
            "uptime": self.uptime,
            "total_checks": self.total_checks,
            "failed_checks": self.failed_checks,
        }


class HealthChecker:
    """System health checker"""

    def __init__(self, check_interval: int = 30):
        """
        Initialize health checker

        Args:
            check_interval: Check interval in seconds
        """
        self.check_interval = check_interval
        self.components: Dict[str, Callable[[], Awaitable[Any]]] = {}
        self.health_history: List[SystemHealth] = []
        self.start_time = time.time()

        # Statistics
        self.total_checks = 0
        self.failed_checks = 0

    def register_component(self, name: str, check_func: Callable[[], Awaitable[Any]]) -> None:
        """
        Register component for health checking

        Args:
            name: Component name
            check_func: Async function to check component health
        """
        self.components[name] = check_func
        logger.debug(f"Registered component for health check: {name}")

    async def check_all(self) -> SystemHealth:
        """
        Check all registered components

        Returns:
            SystemHealth: System health status
        """
        self.total_checks += 1
        component_healths = []

        # Check all components
        for name, check_func in self.components.items():
            health = await self._check_component(name, check_func)
            component_healths.append(health)

        # Get system metrics
        try:
            cpu_usage = psutil.cpu_percent(interval=0.1)
            memory_usage = psutil.virtual_memory().percent
            disk_usage = psutil.disk_usage("/").percent
        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            cpu_usage = 0
            memory_usage = 0
            disk_usage = 0

        # Determine overall status
        unhealthy_count = sum(1 for h in component_healths if h.status == "unhealthy")
        degraded_count = sum(1 for h in component_healths if h.status == "degraded")

        if unhealthy_count > 0:
            status = "unhealthy"
            self.failed_checks += 1
        elif degraded_count > 0:
            status = "degraded"
        else:
            status = "healthy"

        system_health = SystemHealth(
            status=status,
            timestamp=int(time.time() * 1000),
            components=component_healths,
            cpu_usage=cpu_usage,
            memory_usage=memory_usage,
            disk_usage=disk_usage,
            uptime=int(time.time() - self.start_time),
            total_checks=self.total_checks,
            failed_checks=self.failed_checks,
        )

        # Store in history
        self.health_history.append(system_health)

        # Limit history size
        if len(self.health_history) > 1000:
            self.health_history = self.health_history[-1000:]

        return system_health

    async def _check_component(self, name: str, check_func: Callable) -> ComponentHealth:
        """Check individual component"""
        start_time = time.time()

        try:
            result = await check_func()
            latency = (time.time() - start_time) * 1000

            # Parse result
            if isinstance(result, bool):
                status = "healthy" if result else "unhealthy"
                details = {}
            elif isinstance(result, dict):
                status = result.get("status", "healthy")
                details = result.get("details", {})
            else:
                status = "healthy"
                details = {"result": str(result)}

            return ComponentHealth(
                name=name,
                status=status,
                latency_ms=latency,
                last_check=int(time.time() * 1000),
                details=details,
            )

        except Exception as e:
            logger.error(f"Health check failed for {name}: {e}")
            return ComponentHealth(
                name=name,
                status="unhealthy",
                latency_ms=(time.time() - start_time) * 1000,
                last_check=int(time.time() * 1000),
                error_message=str(e),
            )

    def get_metrics(self) -> Dict[str, Any]:
        """Get health checker metrics"""
        return {
            "uptime": int(time.time() - self.start_time),
            "total_checks": self.total_checks,
            "failed_checks": self.failed_checks,
            "success_rate": (self.total_checks - self.failed_checks)
            / max(self.total_checks, 1)
            * 100,
            "components_count": len(self.components),
        }

    async def check_database(self) -> Dict[str, Any]:
        """Check database connectivity"""
        try:
            # Placeholder for actual database check
            # Should be implemented based on actual database setup
            return {"status": "healthy", "details": {"connected": True}}
        except Exception as e:
            return {"status": "unhealthy", "details": {"error": str(e)}}

    async def check_redis(self) -> Dict[str, Any]:
        """Check Redis connectivity"""
        try:
            # Placeholder for actual Redis check
            # Should be implemented based on actual Redis setup
            return {"status": "healthy", "details": {"connected": True}}
        except Exception as e:
            return {"status": "unhealthy", "details": {"error": str(e)}}

    async def check_exchange_connection(self) -> Dict[str, Any]:
        """Check exchange connectivity"""
        try:
            # Placeholder for actual exchange check
            # Should be implemented based on actual exchange connector
            return {"status": "healthy", "details": {"connected": True}}
        except Exception as e:
            return {"status": "unhealthy", "details": {"error": str(e)}}
