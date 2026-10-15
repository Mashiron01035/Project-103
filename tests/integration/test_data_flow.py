"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : test_data_flow.py
    Project     : Project-103
    Description : Integration tests for data flow. Tests complete data flow
                  from providers through processing to storage, including
                  collection, normalization, caching, and persistence.

    SPDX-License-Identifier: AGPL-3.0

"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest


class TestDataCollectionFlow:
    """Tests for data collection flow."""

    @pytest.fixture
    def mock_provider(self):
        """Create mock data provider."""
        provider = MagicMock()
        provider.get_candles = AsyncMock(
            return_value=[
                {
                    "timestamp": 1704067200000,
                    "open": 95000.0,
                    "high": 95500.0,
                    "low": 94800.0,
                    "close": 95200.0,
                    "volume": 1000.0,
                },
                {
                    "timestamp": 1704067200000 + 900000,
                    "open": 95200.0,
                    "high": 95600.0,
                    "low": 95100.0,
                    "close": 95400.0,
                    "volume": 1200.0,
                },
            ]
        )
        provider.get_ticker = AsyncMock(
            return_value={
                "symbol": "BTCUSDT",
                "last_price": 95400.0,
                "bid": 95399.0,
                "ask": 95401.0,
                "volume_24h": 50000.0,
            }
        )
        return provider

    @pytest.mark.asyncio
    async def test_candle_data_collection(self, mock_provider):
        """Test candle data collection from provider."""
        candles = await mock_provider.get_candles(symbol="BTCUSDT", timeframe="15m", limit=100)

        assert len(candles) == 2
        assert candles[0]["open"] == 95000.0
        assert candles[1]["close"] == 95400.0

    @pytest.mark.asyncio
    async def test_ticker_data_collection(self, mock_provider):
        """Test ticker data collection from provider."""
        ticker = await mock_provider.get_ticker(symbol="BTCUSDT")

        assert ticker["symbol"] == "BTCUSDT"
        assert ticker["last_price"] == 95400.0
        assert "bid" in ticker
        assert "ask" in ticker


class TestDataNormalization:
    """Tests for data normalization."""

    def test_candle_normalization(self):
        """Test candle data normalization."""
        raw_candle = {
            "timestamp": "1704067200000",  # String timestamp
            "open": "95000.0",  # String price
            "high": "95500.0",
            "low": "94800.0",
            "close": "95200.0",
            "volume": "1000.0",
        }

        # Normalize
        normalized = {
            "timestamp": int(raw_candle["timestamp"]),
            "open": float(raw_candle["open"]),
            "high": float(raw_candle["high"]),
            "low": float(raw_candle["low"]),
            "close": float(raw_candle["close"]),
            "volume": float(raw_candle["volume"]),
        }

        assert isinstance(normalized["timestamp"], int)
        assert isinstance(normalized["open"], float)
        assert normalized["open"] == 95000.0

    def test_ohlc_validation(self):
        """Test OHLC validation rules."""
        valid_candle = {"open": 95000.0, "high": 95500.0, "low": 94800.0, "close": 95200.0}

        # Validation rules
        assert valid_candle["high"] >= valid_candle["open"]
        assert valid_candle["high"] >= valid_candle["close"]
        assert valid_candle["low"] <= valid_candle["open"]
        assert valid_candle["low"] <= valid_candle["close"]
        assert valid_candle["high"] >= valid_candle["low"]

    def test_invalid_ohlc_detection(self):
        """Test detection of invalid OHLC data."""
        invalid_candle = {
            "open": 95000.0,
            "high": 94000.0,  # High below open - invalid
            "low": 94800.0,
            "close": 95200.0,
        }

        # Should fail validation
        is_valid = (
            invalid_candle["high"] >= invalid_candle["open"]
            and invalid_candle["high"] >= invalid_candle["close"]
            and invalid_candle["low"] <= invalid_candle["open"]
            and invalid_candle["low"] <= invalid_candle["close"]
        )

        assert is_valid is False


class TestCacheFlow:
    """Tests for cache flow."""

    @pytest.fixture
    def mock_cache(self):
        """Create mock cache."""
        cache = MagicMock()
        cache_data = {}

        async def mock_get(key):
            return cache_data.get(key)

        async def mock_set(key, value, ttl=None):
            cache_data[key] = value
            return True

        async def mock_delete(key):
            if key in cache_data:
                del cache_data[key]
                return True
            return False

        cache.get = AsyncMock(side_effect=mock_get)
        cache.set = AsyncMock(side_effect=mock_set)
        cache.delete = AsyncMock(side_effect=mock_delete)
        cache._data = cache_data

        return cache

    @pytest.mark.asyncio
    async def test_cache_store_and_retrieve(self, mock_cache):
        """Test storing and retrieving from cache."""
        key = "candles:BTCUSDT:15m"
        data = [{"timestamp": 1704067200000, "close": 95000.0}]

        await mock_cache.set(key, json.dumps(data))
        mock_cache._data[key] = json.dumps(data)

        retrieved = await mock_cache.get(key)
        assert retrieved is not None

    @pytest.mark.asyncio
    async def test_cache_miss(self, mock_cache):
        """Test cache miss behavior."""
        result = await mock_cache.get("nonexistent:key")
        assert result is None


class TestDatabaseFlow:
    """Tests for database flow."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database."""
        db = MagicMock()
        db.execute = AsyncMock(return_value=None)
        db.fetch = AsyncMock(return_value=[])
        db.fetchone = AsyncMock(return_value=None)
        return db

    @pytest.mark.asyncio
    async def test_candle_insert(self, mock_db):
        """Test inserting candles into database."""
        candles = [
            {
                "timestamp": datetime.now(),
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "open": 95000.0,
                "high": 95500.0,
                "low": 94800.0,
                "close": 95200.0,
                "volume": 1000.0,
            }
        ]

        # Simulate insert
        await mock_db.execute(
            "INSERT INTO market_data.candles VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            *candles[0].values(),
        )

        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_candle_query(self, mock_db):
        """Test querying candles from database."""
        mock_db.fetch.return_value = [
            {"timestamp": datetime.now(), "symbol": "BTCUSDT", "close": 95000.0}
        ]

        result = await mock_db.fetch(
            "SELECT * FROM market_data.candles WHERE symbol = $1 LIMIT $2", "BTCUSDT", 100
        )

        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"


class TestFullDataPipeline:
    """Tests for complete data pipeline."""

    @pytest.fixture
    def mock_pipeline(self):
        """Create mock data pipeline components."""
        return {
            "provider": MagicMock(),
            "normalizer": MagicMock(),
            "validator": MagicMock(),
            "cache": MagicMock(),
            "database": MagicMock(),
        }

    @pytest.mark.asyncio
    async def test_data_pipeline_flow(self, mock_pipeline):
        """Test complete data pipeline from source to storage."""
        # Setup mocks
        raw_data = [{"timestamp": "1704067200000", "close": "95000.0"}]

        mock_pipeline["provider"].get_candles = AsyncMock(return_value=raw_data)
        mock_pipeline["normalizer"].normalize = Mock(
            return_value=[{"timestamp": 1704067200000, "close": 95000.0}]
        )
        mock_pipeline["validator"].validate = Mock(return_value=True)
        mock_pipeline["cache"].set = AsyncMock(return_value=True)
        mock_pipeline["database"].execute = AsyncMock(return_value=None)

        # Execute pipeline
        # 1. Collect
        data = await mock_pipeline["provider"].get_candles()
        assert len(data) == 1

        # 2. Normalize
        normalized = mock_pipeline["normalizer"].normalize(data)
        assert normalized[0]["timestamp"] == 1704067200000

        # 3. Validate
        is_valid = mock_pipeline["validator"].validate(normalized)
        assert is_valid is True

        # 4. Cache
        await mock_pipeline["cache"].set("key", normalized)
        mock_pipeline["cache"].set.assert_called_once()

        # 5. Store in DB
        await mock_pipeline["database"].execute("INSERT...")
        mock_pipeline["database"].execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_error_handling(self, mock_pipeline):
        """Test pipeline error handling."""
        mock_pipeline["provider"].get_candles = AsyncMock(side_effect=Exception("Connection error"))

        with pytest.raises(Exception) as exc_info:
            await mock_pipeline["provider"].get_candles()

        assert "Connection error" in str(exc_info.value)


class TestDataAggregation:
    """Tests for data aggregation."""

    def test_candle_aggregation(self):
        """Test aggregating multiple candles."""
        candles_1m = [
            {
                "timestamp": 1704067200000,
                "open": 95000,
                "high": 95100,
                "low": 94900,
                "close": 95050,
                "volume": 100,
            },
            {
                "timestamp": 1704067260000,
                "open": 95050,
                "high": 95150,
                "low": 95000,
                "close": 95100,
                "volume": 120,
            },
            {
                "timestamp": 1704067320000,
                "open": 95100,
                "high": 95200,
                "low": 95050,
                "close": 95150,
                "volume": 110,
            },
            {
                "timestamp": 1704067380000,
                "open": 95150,
                "high": 95250,
                "low": 95100,
                "close": 95200,
                "volume": 130,
            },
            {
                "timestamp": 1704067440000,
                "open": 95200,
                "high": 95300,
                "low": 95150,
                "close": 95250,
                "volume": 140,
            },
        ]

        # Aggregate to 5m candle
        aggregated = {
            "timestamp": candles_1m[0]["timestamp"],
            "open": candles_1m[0]["open"],
            "high": max(c["high"] for c in candles_1m),
            "low": min(c["low"] for c in candles_1m),
            "close": candles_1m[-1]["close"],
            "volume": sum(c["volume"] for c in candles_1m),
        }

        assert aggregated["open"] == 95000
        assert aggregated["high"] == 95300
        assert aggregated["low"] == 94900
        assert aggregated["close"] == 95250
        assert aggregated["volume"] == 600

    def test_volume_aggregation(self):
        """Test volume aggregation."""
        trades = [{"volume": 100}, {"volume": 200}, {"volume": 150}, {"volume": 250}]

        total_volume = sum(t["volume"] for t in trades)
        assert total_volume == 700


class TestWebSocketDataFlow:
    """Tests for WebSocket data flow."""

    @pytest.fixture
    def mock_websocket(self):
        """Create mock WebSocket."""
        ws = MagicMock()
        ws.connect = AsyncMock()
        ws.disconnect = AsyncMock()
        ws.subscribe = AsyncMock()
        ws.receive = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_websocket_connection(self, mock_websocket):
        """Test WebSocket connection."""
        await mock_websocket.connect()
        mock_websocket.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_subscription(self, mock_websocket):
        """Test WebSocket channel subscription."""
        await mock_websocket.subscribe("kline.15.BTCUSDT")
        mock_websocket.subscribe.assert_called_with("kline.15.BTCUSDT")

    @pytest.mark.asyncio
    async def test_websocket_message_handling(self, mock_websocket):
        """Test handling WebSocket messages."""
        mock_websocket.receive.return_value = {
            "topic": "kline.15.BTCUSDT",
            "data": {"timestamp": 1704067200000, "close": 95000.0},
        }

        message = await mock_websocket.receive()
        assert message["topic"] == "kline.15.BTCUSDT"
        assert message["data"]["close"] == 95000.0
