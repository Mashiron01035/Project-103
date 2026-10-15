"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : conftest.py
    Project     : Project-103
    Description : Global test configuration, fixtures for database, redis,
                  mock data, and common test utilities.

    SPDX-License-Identifier: AGPL-3.0

"""

import asyncio
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# =============================================================================
# PYTEST CONFIGURATION
# =============================================================================


def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
    config.addinivalue_line("markers", "database: mark test as requiring database")
    config.addinivalue_line("markers", "redis: mark test as requiring redis")


def pytest_collection_modifyitems(config, items):
    """Add markers to tests based on their location."""
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)


# =============================================================================
# PATH FIXTURES
# =============================================================================


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return project root path."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def fixtures_path(project_root) -> Path:
    """Return path to test fixtures."""
    return project_root / "tests" / "fixtures"


# =============================================================================
# EVENT LOOP FIXTURE
# =============================================================================


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# CANDLE DATA FIXTURES
# =============================================================================


@pytest.fixture
def sample_candles() -> List[Dict[str, Any]]:
    """Generate sample candle data for testing."""
    from src.models.candle import Candle

    candles = []
    base_timestamp = int(datetime(2024, 1, 1).timestamp() * 1000)
    base_price = 100.0

    for i in range(100):
        # Create realistic price movement
        open_price = base_price + (i * 0.1)
        high_price = open_price + 0.5
        low_price = open_price - 0.3
        close_price = open_price + 0.2

        candle = Candle(
            symbol="BTCUSDT",
            timeframe="15m",
            timestamp=base_timestamp + (i * 15 * 60 * 1000),
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=1000.0 + (i * 10),
            quote_volume=100000.0 + (i * 1000),
            trades_count=500 + i,
            is_closed=True,
        )
        candles.append(candle)

    return candles


@pytest.fixture
def uptrend_candles() -> List[Dict[str, Any]]:
    """Generate candles with uptrend pattern."""
    from src.models.candle import Candle

    candles = []
    base_timestamp = int(datetime(2024, 1, 1).timestamp() * 1000)
    base_price = 100.0

    for i in range(50):
        price = base_price + (i * 0.5)  # Strong uptrend
        candle = Candle(
            symbol="BTCUSDT",
            timeframe="15m",
            timestamp=base_timestamp + (i * 15 * 60 * 1000),
            open=price,
            high=price + 0.8,
            low=price - 0.2,
            close=price + 0.5,
            volume=1500.0 + (i * 20),
            quote_volume=150000.0,
            trades_count=600,
            is_closed=True,
        )
        candles.append(candle)

    return candles


@pytest.fixture
def downtrend_candles() -> List[Dict[str, Any]]:
    """Generate candles with downtrend pattern."""
    from src.models.candle import Candle

    candles = []
    base_timestamp = int(datetime(2024, 1, 1).timestamp() * 1000)
    base_price = 150.0

    for i in range(50):
        price = base_price - (i * 0.5)  # Strong downtrend
        candle = Candle(
            symbol="BTCUSDT",
            timeframe="15m",
            timestamp=base_timestamp + (i * 15 * 60 * 1000),
            open=price,
            high=price + 0.2,
            low=price - 0.8,
            close=price - 0.5,
            volume=1500.0 + (i * 20),
            quote_volume=150000.0,
            trades_count=600,
            is_closed=True,
        )
        candles.append(candle)

    return candles


@pytest.fixture
def sideways_candles() -> List[Dict[str, Any]]:
    """Generate candles with sideways/ranging pattern."""
    from src.models.candle import Candle

    candles = []
    base_timestamp = int(datetime(2024, 1, 1).timestamp() * 1000)
    base_price = 100.0

    for i in range(50):
        # Oscillate around base price
        variation = ((i % 10) - 5) * 0.1
        price = base_price + variation

        candle = Candle(
            symbol="BTCUSDT",
            timeframe="15m",
            timestamp=base_timestamp + (i * 15 * 60 * 1000),
            open=price,
            high=price + 0.3,
            low=price - 0.3,
            close=price,
            volume=1000.0,
            quote_volume=100000.0,
            trades_count=500,
            is_closed=True,
        )
        candles.append(candle)

    return candles


@pytest.fixture
def volatile_candles() -> List[Dict[str, Any]]:
    """Generate highly volatile candles."""
    import random

    from src.models.candle import Candle

    random.seed(42)  # Reproducible
    candles = []
    base_timestamp = int(datetime(2024, 1, 1).timestamp() * 1000)
    base_price = 100.0

    for i in range(50):
        change = random.uniform(-3.0, 3.0)
        price = base_price + change

        candle = Candle(
            symbol="BTCUSDT",
            timeframe="15m",
            timestamp=base_timestamp + (i * 15 * 60 * 1000),
            open=price,
            high=price + abs(change) + 1.0,
            low=price - abs(change) - 1.0,
            close=price + random.uniform(-1.0, 1.0),
            volume=2000.0 + random.uniform(0, 1000),
            quote_volume=200000.0,
            trades_count=800,
            is_closed=True,
        )
        candles.append(candle)
        base_price = candle.close

    return candles


# =============================================================================
# TICKER FIXTURES
# =============================================================================


@pytest.fixture
def sample_ticker() -> Dict[str, Any]:
    """Generate sample ticker data."""
    from src.models.ticker import Ticker

    return Ticker(
        symbol="BTCUSDT",
        timestamp=int(datetime.now().timestamp() * 1000),
        last_price=95000.0,
        bid_price=94999.0,
        ask_price=95001.0,
        bid_size=10.5,
        ask_size=8.3,
        volume_24h=50000.0,
        quote_volume_24h=4750000000.0,
        high_24h=96000.0,
        low_24h=94000.0,
        open_24h=94500.0,
        price_change_24h=500.0,
        price_change_percent_24h=0.53,
    )


# =============================================================================
# ORDER & POSITION FIXTURES
# =============================================================================


@pytest.fixture
def sample_order() -> Dict[str, Any]:
    """Generate sample order data."""
    from src.core.enums import OrderSide, OrderStatus, OrderType
    from src.models.order import Order

    return Order(
        id="test-order-001",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal("0.1"),
        price=Decimal("95000.0"),
        status=OrderStatus.NEW,
        time_in_force="GTC",
        created_at=datetime.now(),
    )


@pytest.fixture
def sample_signal() -> Dict[str, Any]:
    """Generate sample trading signal."""
    from src.core.enums import SignalStrength, SignalType
    from src.models.signal import Signal

    return Signal(
        id="test-signal-001",
        timestamp=int(datetime.now().timestamp() * 1000),
        symbol="BTCUSDT",
        strategy_name="test_strategy",
        timeframe="15m",
        signal_type=SignalType.BUY,
        strength=SignalStrength.STRONG,
        confidence=0.75,
        entry_price=95000.0,
        stop_loss=94000.0,
        take_profit=[96000.0, 97000.0, 98000.0],
        risk_reward_ratio=2.0,
        metadata={"reason": "test"},
    )


# =============================================================================
# DATABASE FIXTURES
# =============================================================================


@pytest.fixture
def db_url() -> str:
    """Get test database URL."""
    return os.environ.get(
        "TEST_DATABASE_URL", "postgresql://trader:trader@localhost:5432/project103_test"
    )


@pytest_asyncio.fixture
async def db_connection(db_url):
    """Create async database connection for tests."""
    try:
        import asyncpg

        conn = await asyncpg.connect(db_url)
        yield conn
        await conn.close()
    except Exception:
        pytest.skip("Database not available")


@pytest.fixture
def mock_db():
    """Create mock database for unit tests."""
    mock = MagicMock()
    mock.execute = AsyncMock(return_value=None)
    mock.fetch = AsyncMock(return_value=[])
    mock.fetchone = AsyncMock(return_value=None)
    mock.fetchval = AsyncMock(return_value=None)
    return mock


# =============================================================================
# REDIS FIXTURES
# =============================================================================


@pytest.fixture
def redis_url() -> str:
    """Get test Redis URL."""
    return os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1")


@pytest_asyncio.fixture
async def redis_connection(redis_url):
    """Create async Redis connection for tests."""
    try:
        import redis.asyncio as redis

        client = redis.from_url(redis_url)
        yield client
        await client.flushdb()
        await client.close()
    except Exception:
        pytest.skip("Redis not available")


@pytest.fixture
def mock_redis():
    """Create mock Redis for unit tests."""
    mock = MagicMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.hget = AsyncMock(return_value=None)
    mock.hset = AsyncMock(return_value=1)
    mock.publish = AsyncMock(return_value=1)
    return mock


# =============================================================================
# EXCHANGE/API MOCK FIXTURES
# =============================================================================


@pytest.fixture
def mock_exchange():
    """Create mock exchange connector."""
    mock = MagicMock()
    mock.get_ticker = AsyncMock(
        return_value={"symbol": "BTCUSDT", "last_price": 95000.0, "bid": 94999.0, "ask": 95001.0}
    )
    mock.get_orderbook = AsyncMock(
        return_value={
            "bids": [[94999.0, 10.0], [94998.0, 20.0]],
            "asks": [[95001.0, 8.0], [95002.0, 15.0]],
        }
    )
    mock.place_order = AsyncMock(return_value={"order_id": "exchange-order-123", "status": "NEW"})
    mock.cancel_order = AsyncMock(return_value=True)
    mock.get_balance = AsyncMock(return_value={"USDT": {"free": 10000.0, "locked": 0.0}})
    return mock


@pytest.fixture
def mock_data_provider():
    """Create mock data provider."""
    mock = MagicMock()
    mock.get_candles = AsyncMock(return_value=[])
    mock.get_ticker = AsyncMock(return_value=None)
    mock.subscribe = AsyncMock(return_value=True)
    mock.unsubscribe = AsyncMock(return_value=True)
    return mock


# =============================================================================
# INDICATOR FIXTURES
# =============================================================================


@pytest.fixture
def indicator_factory():
    """Get indicator factory instance."""
    from src.indicators.factory import get_indicator_factory

    return get_indicator_factory()


@pytest.fixture
def rsi_config() -> Dict[str, Any]:
    """RSI indicator config."""
    return {"period": 14}


@pytest.fixture
def ema_config() -> Dict[str, Any]:
    """EMA indicator config."""
    return {"period": 20}


@pytest.fixture
def macd_config() -> Dict[str, Any]:
    """MACD indicator config."""
    return {"fast_period": 12, "slow_period": 26, "signal_period": 9}


@pytest.fixture
def bollinger_config() -> Dict[str, Any]:
    """Bollinger Bands config."""
    return {"period": 20, "std_dev": 2.0}


# =============================================================================
# STRATEGY FIXTURES
# =============================================================================


@pytest.fixture
def strategy_config() -> Dict[str, Any]:
    """Base strategy configuration."""
    return {
        "enabled": True,
        "timeframes": ["15m", "1h"],
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "min_confidence": 0.6,
        "target_rr": 2.0,
        "max_positions": 3,
    }


@pytest.fixture
def risk_config() -> Dict[str, Any]:
    """Risk management configuration."""
    return {
        "max_risk_per_trade": 0.01,
        "max_daily_drawdown": 0.05,
        "max_open_positions": 5,
        "max_position_size_percent": 0.1,
        "default_stop_loss_percent": 0.02,
        "default_take_profit_percent": 0.04,
    }


# =============================================================================
# UTILITY FIXTURES
# =============================================================================


@pytest.fixture
def test_symbol() -> str:
    """Default test symbol."""
    return "BTCUSDT"


@pytest.fixture
def test_timeframe() -> str:
    """Default test timeframe."""
    return "15m"


@pytest.fixture
def test_balance() -> float:
    """Default test account balance."""
    return 10000.0


@pytest.fixture
def freeze_time():
    """Fixture to freeze time for deterministic tests."""
    from unittest.mock import patch

    frozen_time = datetime(2024, 1, 15, 12, 0, 0)

    with patch("datetime.datetime") as mock_datetime:
        mock_datetime.now.return_value = frozen_time
        mock_datetime.utcnow.return_value = frozen_time
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        yield frozen_time


# =============================================================================
# JSON FIXTURES LOADER
# =============================================================================


@pytest.fixture
def load_fixture(fixtures_path):
    """Load JSON fixture file."""

    def _load(filename: str) -> Any:
        filepath = fixtures_path / filename
        if filepath.exists():
            with open(filepath) as f:
                return json.load(f)
        return None

    return _load
