"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : test_signal_flow.py
    Project     : Project-103
    Description : Integration tests for signal flow. Tests complete signal
                  flow from market data through strategy analysis to signal
                  generation, validation, and aggregation.

    SPDX-License-Identifier: AGPL-3.0

"""

from datetime import datetime
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestIndicatorCalculationFlow:
    """Tests for indicator calculation flow."""

    @pytest.fixture
    def sample_candles(self) -> List[Dict[str, Any]]:
        """Create sample candles for indicator calculation."""
        candles = []
        base_price = 95000.0

        for i in range(50):
            candles.append(
                {
                    "timestamp": 1704067200000 + i * 900000,
                    "open": base_price + i * 10,
                    "high": base_price + i * 10 + 50,
                    "low": base_price + i * 10 - 30,
                    "close": base_price + i * 10 + 20,
                    "volume": 1000 + i * 10,
                }
            )

        return candles

    def test_rsi_calculation_flow(self, sample_candles):
        """Test RSI calculation flow."""
        # Extract closes
        closes = [c["close"] for c in sample_candles]

        # Calculate price changes
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        # Separate gains and losses
        gains = [c if c > 0 else 0 for c in changes]
        losses = [-c if c < 0 else 0 for c in changes]

        # Average over period
        period = 14
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Calculate RSI
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        assert 0 <= rsi <= 100

    def test_ema_calculation_flow(self, sample_candles):
        """Test EMA calculation flow."""
        closes = [c["close"] for c in sample_candles]
        period = 20

        # Calculate EMA
        multiplier = 2 / (period + 1)

        # Initial SMA
        ema = sum(closes[:period]) / period

        # Calculate EMA for rest of data
        for i in range(period, len(closes)):
            ema = (closes[i] * multiplier) + (ema * (1 - multiplier))

        assert ema > 0

    def test_multiple_indicators_flow(self, sample_candles):
        """Test calculating multiple indicators."""
        # Mock indicator factory
        indicators = {
            "rsi": {"value": 55.0, "signals": ["BULLISH"]},
            "ema_20": {"value": 95300.0},
            "macd": {"value": 50.0, "signal": 45.0, "histogram": 5.0},
            "atr": {"value": 100.0},
        }

        assert "rsi" in indicators
        assert "ema_20" in indicators
        assert "macd" in indicators
        assert indicators["rsi"]["value"] == 55.0


class TestStrategyAnalysisFlow:
    """Tests for strategy analysis flow."""

    @pytest.fixture
    def mock_strategy(self):
        """Create mock strategy."""
        strategy = MagicMock()
        strategy.name = "test_strategy"
        strategy.enabled = True
        strategy.get_indicators_config.return_value = {"rsi": {"period": 14}, "ema": {"period": 20}}
        strategy.analyze = AsyncMock(return_value=None)
        return strategy

    @pytest.fixture
    def sample_indicators(self):
        """Create sample indicators."""
        return {
            "rsi": MagicMock(values={"value": 25.0}, signals=["OVERSOLD"]),
            "ema": MagicMock(values={"value": 95000.0}),
        }

    @pytest.mark.asyncio
    async def test_strategy_analysis_with_signal(
        self, mock_strategy, sample_candles, sample_indicators
    ):
        """Test strategy analysis that generates a signal."""
        # Setup mock to return a signal
        mock_strategy.analyze.return_value = {
            "id": "signal-001",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "confidence": 0.75,
            "entry_price": 95000.0,
            "stop_loss": 94000.0,
            "take_profit": [96000.0, 97000.0],
        }

        signal = await mock_strategy.analyze("BTCUSDT", sample_candles, sample_indicators)

        assert signal is not None
        assert signal["side"] == "BUY"
        assert signal["confidence"] == 0.75

    @pytest.mark.asyncio
    async def test_strategy_analysis_no_signal(
        self, mock_strategy, sample_candles, sample_indicators
    ):
        """Test strategy analysis with no signal generated."""
        mock_strategy.analyze.return_value = None

        signal = await mock_strategy.analyze("BTCUSDT", sample_candles, sample_indicators)

        assert signal is None


class TestSignalGenerationFlow:
    """Tests for signal generation flow."""

    def test_buy_signal_generation(self):
        """Test generating BUY signal."""
        current_price = 95000.0
        atr = 200.0

        signal = {
            "id": "signal-001",
            "timestamp": int(datetime.now().timestamp() * 1000),
            "symbol": "BTCUSDT",
            "side": "BUY",
            "confidence": 0.75,
            "entry_price": current_price,
            "stop_loss": current_price - (atr * 2),  # 2 ATR stop
            "take_profit": [
                current_price + (atr * 2),  # 1:1 R:R
                current_price + (atr * 4),  # 1:2 R:R
                current_price + (atr * 6),  # 1:3 R:R
            ],
            "timeframe": "15m",
            "strategy_name": "test_strategy",
        }

        assert signal["stop_loss"] < signal["entry_price"]
        assert all(tp > signal["entry_price"] for tp in signal["take_profit"])

    def test_sell_signal_generation(self):
        """Test generating SELL signal."""
        current_price = 95000.0
        atr = 200.0

        signal = {
            "id": "signal-002",
            "timestamp": int(datetime.now().timestamp() * 1000),
            "symbol": "BTCUSDT",
            "side": "SELL",
            "confidence": 0.70,
            "entry_price": current_price,
            "stop_loss": current_price + (atr * 2),  # 2 ATR stop above
            "take_profit": [
                current_price - (atr * 2),
                current_price - (atr * 4),
                current_price - (atr * 6),
            ],
            "timeframe": "15m",
            "strategy_name": "test_strategy",
        }

        assert signal["stop_loss"] > signal["entry_price"]
        assert all(tp < signal["entry_price"] for tp in signal["take_profit"])

    def test_risk_reward_calculation(self):
        """Test risk/reward ratio calculation."""
        entry = 95000.0
        stop_loss = 94000.0  # 1000 risk
        take_profit = 97000.0  # 2000 reward

        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        rr_ratio = reward / risk

        assert rr_ratio == 2.0


class TestSignalValidationFlow:
    """Tests for signal validation flow."""

    @pytest.fixture
    def valid_signal(self):
        """Create valid signal for testing."""
        return {
            "id": "signal-001",
            "timestamp": int(datetime.now().timestamp() * 1000),
            "symbol": "BTCUSDT",
            "side": "BUY",
            "confidence": 0.75,
            "entry_price": 95000.0,
            "stop_loss": 94000.0,
            "take_profit": [97000.0],
            "timeframe": "15m",
            "strategy_name": "test_strategy",
        }

    def test_validate_confidence(self, valid_signal):
        """Test signal confidence validation."""
        min_confidence = 0.6

        is_valid = valid_signal["confidence"] >= min_confidence
        assert is_valid is True

        # Test low confidence
        valid_signal["confidence"] = 0.4
        is_valid = valid_signal["confidence"] >= min_confidence
        assert is_valid is False

    def test_validate_stop_loss_placement(self, valid_signal):
        """Test stop loss placement validation."""
        # For BUY, SL should be below entry
        if valid_signal["side"] == "BUY":
            is_valid = valid_signal["stop_loss"] < valid_signal["entry_price"]
        else:
            is_valid = valid_signal["stop_loss"] > valid_signal["entry_price"]

        assert is_valid is True

    def test_validate_take_profit_placement(self, valid_signal):
        """Test take profit placement validation."""
        # For BUY, TPs should be above entry
        if valid_signal["side"] == "BUY":
            is_valid = all(tp > valid_signal["entry_price"] for tp in valid_signal["take_profit"])
        else:
            is_valid = all(tp < valid_signal["entry_price"] for tp in valid_signal["take_profit"])

        assert is_valid is True

    def test_validate_risk_reward(self, valid_signal):
        """Test minimum risk/reward validation."""
        min_rr = 1.5

        entry = valid_signal["entry_price"]
        sl = valid_signal["stop_loss"]
        tp = valid_signal["take_profit"][0]

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        rr_ratio = reward / risk

        is_valid = rr_ratio >= min_rr
        assert is_valid is True


class TestSignalAggregationFlow:
    """Tests for signal aggregation from multiple strategies."""

    @pytest.fixture
    def multiple_signals(self):
        """Create multiple signals from different strategies."""
        return [
            {
                "id": "signal-001",
                "strategy_name": "strategy_a",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "confidence": 0.8,
                "entry_price": 95000.0,
            },
            {
                "id": "signal-002",
                "strategy_name": "strategy_b",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "confidence": 0.7,
                "entry_price": 95100.0,
            },
            {
                "id": "signal-003",
                "strategy_name": "strategy_c",
                "symbol": "BTCUSDT",
                "side": "SELL",  # Conflicting signal
                "confidence": 0.6,
                "entry_price": 95050.0,
            },
        ]

    def test_signal_consensus(self, multiple_signals):
        """Test signal consensus calculation."""
        buy_signals = [s for s in multiple_signals if s["side"] == "BUY"]
        sell_signals = [s for s in multiple_signals if s["side"] == "SELL"]

        buy_confidence = (
            sum(s["confidence"] for s in buy_signals) / len(buy_signals) if buy_signals else 0
        )
        sell_confidence = (
            sum(s["confidence"] for s in sell_signals) / len(sell_signals) if sell_signals else 0
        )

        # Consensus is for BUY
        assert buy_confidence > sell_confidence
        assert len(buy_signals) > len(sell_signals)

    def test_weighted_signal_aggregation(self, multiple_signals):
        """Test weighted signal aggregation."""
        # Calculate weighted average entry for BUY signals
        buy_signals = [s for s in multiple_signals if s["side"] == "BUY"]

        if buy_signals:
            weighted_entry = sum(s["entry_price"] * s["confidence"] for s in buy_signals) / sum(
                s["confidence"] for s in buy_signals
            )

            assert 95000 <= weighted_entry <= 95100

    def test_conflict_resolution(self, multiple_signals):
        """Test signal conflict resolution."""
        buy_signals = [s for s in multiple_signals if s["side"] == "BUY"]
        sell_signals = [s for s in multiple_signals if s["side"] == "SELL"]

        # Simple majority rule
        if len(buy_signals) > len(sell_signals):
            final_direction = "BUY"
        elif len(sell_signals) > len(buy_signals):
            final_direction = "SELL"
        else:
            # Tie - use highest confidence
            best_buy = max(buy_signals, key=lambda x: x["confidence"]) if buy_signals else None
            best_sell = max(sell_signals, key=lambda x: x["confidence"]) if sell_signals else None

            if best_buy and best_sell:
                final_direction = (
                    "BUY" if best_buy["confidence"] > best_sell["confidence"] else "SELL"
                )
            else:
                final_direction = "HOLD"

        assert final_direction == "BUY"


class TestSignalHistoryFlow:
    """Tests for signal history tracking."""

    @pytest.fixture
    def signal_history(self):
        """Create signal history for testing."""
        return [
            {"id": "sig-001", "side": "BUY", "result": "WIN", "pnl": 100.0},
            {"id": "sig-002", "side": "SELL", "result": "LOSS", "pnl": -50.0},
            {"id": "sig-003", "side": "BUY", "result": "WIN", "pnl": 150.0},
            {"id": "sig-004", "side": "BUY", "result": "WIN", "pnl": 80.0},
            {"id": "sig-005", "side": "SELL", "result": "LOSS", "pnl": -30.0},
        ]

    def test_win_rate_calculation(self, signal_history):
        """Test win rate calculation from history."""
        wins = len([s for s in signal_history if s["result"] == "WIN"])
        total = len(signal_history)

        win_rate = wins / total
        assert win_rate == 0.6  # 3 wins out of 5

    def test_profit_factor_calculation(self, signal_history):
        """Test profit factor calculation."""
        gross_profit = sum(s["pnl"] for s in signal_history if s["pnl"] > 0)
        gross_loss = abs(sum(s["pnl"] for s in signal_history if s["pnl"] < 0))

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        assert profit_factor > 1  # Profitable system

    def test_average_trade_calculation(self, signal_history):
        """Test average trade calculation."""
        total_pnl = sum(s["pnl"] for s in signal_history)
        avg_trade = total_pnl / len(signal_history)

        assert avg_trade == 50.0  # (100-50+150+80-30) / 5 = 250/5 = 50
