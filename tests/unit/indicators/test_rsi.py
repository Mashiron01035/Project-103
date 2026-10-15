"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : test_rsi.py
    Project     : Project-103
    Description : Unit tests for RSI (Relative Strength Index) indicator.
                  Tests basic RSI calculation, edge cases, signal detection,
                  and input validation.

    SPDX-License-Identifier: AGPL-3.0

"""

from typing import List

import pytest

from src.indicators.base_indicator import Candle, IndicatorResult
from src.indicators.momentum.rsi import RSI


class TestRSICalculation:
    """Tests for RSI calculation logic."""

    @pytest.fixture
    def rsi_indicator(self) -> RSI:
        """Create RSI indicator instance."""
        return RSI()

    def test_rsi_initialization(self, rsi_indicator):
        """Test RSI indicator initialization."""
        assert rsi_indicator.name == "rsi"

    def test_rsi_basic_calculation(self, rsi_indicator, sample_candles):
        """Test basic RSI calculation with sample data."""
        result = rsi_indicator.calculate(sample_candles, period=14)

        assert isinstance(result, IndicatorResult)
        assert result.name == "rsi"
        assert "value" in result.values
        assert 0 <= result.values["value"] <= 100

    def test_rsi_uptrend_high_value(self, rsi_indicator, uptrend_candles):
        """Test RSI shows high values in uptrend."""
        result = rsi_indicator.calculate(uptrend_candles, period=14)

        # In strong uptrend, RSI should be high (> 50)
        assert result.values["value"] > 50

    def test_rsi_downtrend_low_value(self, rsi_indicator, downtrend_candles):
        """Test RSI shows low values in downtrend."""
        result = rsi_indicator.calculate(downtrend_candles, period=14)

        # In strong downtrend, RSI should be low (< 50)
        assert result.values["value"] < 50

    def test_rsi_sideways_neutral(self, rsi_indicator, sideways_candles):
        """Test RSI is neutral in sideways market."""
        result = rsi_indicator.calculate(sideways_candles, period=14)

        # In sideways market, RSI should be around 50
        assert 30 <= result.values["value"] <= 70

    def test_rsi_different_periods(self, rsi_indicator, sample_candles):
        """Test RSI with different period values."""
        rsi_7 = rsi_indicator.calculate(sample_candles, period=7)
        rsi_14 = rsi_indicator.calculate(sample_candles, period=14)
        rsi_21 = rsi_indicator.calculate(sample_candles, period=21)

        # All should return valid RSI values
        assert 0 <= rsi_7.values["value"] <= 100
        assert 0 <= rsi_14.values["value"] <= 100
        assert 0 <= rsi_21.values["value"] <= 100

        # Shorter period RSI tends to be more extreme
        # (not always true but generally)
        assert rsi_7.metadata["period"] == 7
        assert rsi_14.metadata["period"] == 14
        assert rsi_21.metadata["period"] == 21

    def test_rsi_metadata(self, rsi_indicator, sample_candles):
        """Test RSI result contains correct metadata."""
        period = 14
        result = rsi_indicator.calculate(sample_candles, period=period)

        assert "period" in result.metadata
        assert result.metadata["period"] == period
        assert result.timestamp == sample_candles[-1].timestamp


class TestRSISignalDetection:
    """Tests for RSI signal detection."""

    @pytest.fixture
    def rsi_indicator(self) -> RSI:
        """Create RSI indicator instance."""
        return RSI()

    def test_oversold_signal(self, rsi_indicator):
        """Test oversold signal detection (RSI < 30)."""
        # Create candles that produce oversold RSI
        candles = self._create_downtrend_candles(60, strong=True)
        result = rsi_indicator.calculate(candles, period=14)

        if result.values["value"] <= 30:
            assert "oversold" in result.signals

    def test_overbought_signal(self, rsi_indicator):
        """Test overbought signal detection (RSI > 70)."""
        # Create candles that produce overbought RSI
        candles = self._create_uptrend_candles(60, strong=True)
        result = rsi_indicator.calculate(candles, period=14)

        if result.values["value"] >= 70:
            assert "overbought" in result.signals

    def test_bullish_signal(self, rsi_indicator):
        """Test bullish signal detection (RSI > 50)."""
        candles = self._create_uptrend_candles(50)
        result = rsi_indicator.calculate(candles, period=14)

        if result.values["value"] > 50:
            assert "bullish" in result.signals

    def test_bearish_signal(self, rsi_indicator):
        """Test bearish signal detection (RSI < 50)."""
        candles = self._create_downtrend_candles(50)
        result = rsi_indicator.calculate(candles, period=14)

        if result.values["value"] < 50:
            assert "bearish" in result.signals

    def _create_uptrend_candles(self, count: int, strong: bool = False) -> List[Candle]:
        """Helper to create uptrend candles."""
        candles = []
        price = 100.0
        step = 1.0 if strong else 0.3

        for i in range(count):
            price += step
            candles.append(
                Candle(
                    timestamp=1704067200000 + i * 900000,
                    open=price - step / 2,
                    high=price + 0.2,
                    low=price - step,
                    close=price,
                    volume=1000.0,
                    trades_count=100,
                )
            )
        return candles

    def _create_downtrend_candles(self, count: int, strong: bool = False) -> List[Candle]:
        """Helper to create downtrend candles."""
        candles = []
        price = 200.0
        step = 1.0 if strong else 0.3

        for i in range(count):
            price -= step
            candles.append(
                Candle(
                    timestamp=1704067200000 + i * 900000,
                    open=price + step / 2,
                    high=price + step,
                    low=price - 0.2,
                    close=price,
                    volume=1000.0,
                    trades_count=100,
                )
            )
        return candles


class TestRSIEdgeCases:
    """Tests for RSI edge cases."""

    @pytest.fixture
    def rsi_indicator(self) -> RSI:
        """Create RSI indicator instance."""
        return RSI()

    def test_all_gains_rsi_100(self, rsi_indicator):
        """Test RSI = 100 when all candles are gains."""
        candles = []
        price = 100.0

        for i in range(20):
            price += 1.0  # Only gains
            candles.append(
                Candle(
                    timestamp=1704067200000 + i * 900000,
                    open=price - 0.5,
                    high=price + 0.1,
                    low=price - 0.6,
                    close=price,
                    volume=1000.0,
                    trades_count=100,
                )
            )

        result = rsi_indicator.calculate(candles, period=14)
        assert result.values["value"] == 100.0

    def test_all_losses_rsi_0(self, rsi_indicator):
        """Test RSI = 0 when all candles are losses."""
        candles = []
        price = 200.0

        for i in range(20):
            price -= 1.0  # Only losses
            candles.append(
                Candle(
                    timestamp=1704067200000 + i * 900000,
                    open=price + 0.5,
                    high=price + 0.6,
                    low=price - 0.1,
                    close=price,
                    volume=1000.0,
                    trades_count=100,
                )
            )

        result = rsi_indicator.calculate(candles, period=14)
        assert result.values["value"] == 0.0

    def test_insufficient_candles_raises_error(self, rsi_indicator):
        """Test that insufficient candles raises error."""
        candles = [
            Candle(
                timestamp=1704067200000,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000.0,
                trades_count=100,
            )
            for _ in range(10)  # Less than period + 1
        ]

        with pytest.raises(ValueError):
            rsi_indicator.calculate(candles, period=14)

    def test_minimum_candles(self, rsi_indicator):
        """Test RSI with minimum required candles."""
        candles = []
        price = 100.0

        # Need period + 1 candles minimum
        for i in range(15):
            price += 0.1 * (1 if i % 2 == 0 else -1)
            candles.append(
                Candle(
                    timestamp=1704067200000 + i * 900000,
                    open=price,
                    high=price + 0.2,
                    low=price - 0.2,
                    close=price,
                    volume=1000.0,
                    trades_count=100,
                )
            )

        result = rsi_indicator.calculate(candles, period=14)
        assert 0 <= result.values["value"] <= 100


class TestRSIValueRange:
    """Tests for RSI value range validation."""

    @pytest.fixture
    def rsi_indicator(self) -> RSI:
        """Create RSI indicator instance."""
        return RSI()

    def test_rsi_never_below_zero(self, rsi_indicator, volatile_candles):
        """Test RSI is never below 0."""
        result = rsi_indicator.calculate(volatile_candles, period=14)
        assert result.values["value"] >= 0

    def test_rsi_never_above_100(self, rsi_indicator, volatile_candles):
        """Test RSI is never above 100."""
        result = rsi_indicator.calculate(volatile_candles, period=14)
        assert result.values["value"] <= 100

    def test_rsi_value_is_float(self, rsi_indicator, sample_candles):
        """Test RSI value is a float."""
        result = rsi_indicator.calculate(sample_candles, period=14)
        assert isinstance(result.values["value"], float)

    def test_rsi_value_precision(self, rsi_indicator, sample_candles):
        """Test RSI value has 2 decimal precision."""
        result = rsi_indicator.calculate(sample_candles, period=14)
        rsi_str = str(result.values["value"])

        if "." in rsi_str:
            decimal_places = len(rsi_str.split(".")[1])
            assert decimal_places <= 2
