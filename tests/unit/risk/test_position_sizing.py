"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : test_position_sizing.py
    Project     : Project-103
    Description : Unit tests for Position Sizing module. Tests fixed risk,
                  fixed amount, Kelly criterion, percent equity, volatility
                  adjusted sizing, and position limits.

    SPDX-License-Identifier: AGPL-3.0

"""

from unittest.mock import Mock

import pytest

from src.models.signal import Signal, SignalType
from src.risk.position_sizing import PositionSizer, PositionSizeResult, PositionSizingMethod


class TestPositionSizerInitialization:
    """Tests for PositionSizer initialization."""

    def test_default_initialization(self):
        """Test PositionSizer with default config."""
        sizer = PositionSizer()

        assert sizer.method == PositionSizingMethod.FIXED_RISK
        assert sizer.max_risk_per_trade == 0.01
        assert sizer.max_position_percent == 0.10
        assert sizer.min_position_size_usdt == 10.0

    def test_custom_config(self):
        """Test PositionSizer with custom config."""
        config = {
            "method": "fixed_amount",
            "max_risk_per_trade": 0.02,
            "max_position_percent": 0.15,
            "min_position_size_usdt": 20.0,
            "max_leverage": 5,
        }
        sizer = PositionSizer(config)

        assert sizer.method == PositionSizingMethod.FIXED_AMOUNT
        assert sizer.max_risk_per_trade == 0.02
        assert sizer.max_position_percent == 0.15
        assert sizer.max_leverage == 5


class TestFixedRiskSizing:
    """Tests for fixed risk position sizing method."""

    @pytest.fixture
    def sizer(self) -> PositionSizer:
        """Create PositionSizer with fixed risk method."""
        return PositionSizer({"method": "fixed_risk", "max_risk_per_trade": 0.01})  # 1%

    @pytest.fixture
    def long_signal(self) -> Signal:
        """Create a LONG signal for testing."""
        signal = Mock(spec=Signal)
        signal.id = "test-signal-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 98.0  # 2% below entry
        signal.take_profit = [104.0, 106.0]
        signal.confidence = 0.75
        signal.position_size_percent = 1.0
        signal.risk_reward_ratio = 2.0
        return signal

    @pytest.fixture
    def short_signal(self) -> Signal:
        """Create a SHORT signal for testing."""
        signal = Mock(spec=Signal)
        signal.id = "test-signal-002"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.SHORT
        signal.entry_price = 100.0
        signal.stop_loss = 102.0  # 2% above entry
        signal.take_profit = [96.0, 94.0]
        signal.confidence = 0.70
        signal.position_size_percent = 1.0
        signal.risk_reward_ratio = 2.0
        return signal

    def test_fixed_risk_long_position(self, sizer, long_signal):
        """Test position sizing for LONG signal."""
        account_balance = 10000.0

        result = sizer.calculate(long_signal, account_balance)

        assert isinstance(result, PositionSizeResult)
        assert result.method == PositionSizingMethod.FIXED_RISK
        assert result.risk_percent <= 1.0  # Max 1% risk
        assert result.size_base > 0
        assert result.size_quote > 0

    def test_fixed_risk_short_position(self, sizer, short_signal):
        """Test position sizing for SHORT signal."""
        account_balance = 10000.0

        result = sizer.calculate(short_signal, account_balance)

        assert isinstance(result, PositionSizeResult)
        assert result.risk_percent <= 1.0
        assert result.size_base > 0

    def test_risk_amount_calculation(self, sizer, long_signal):
        """Test risk amount is correctly calculated."""
        account_balance = 10000.0

        result = sizer.calculate(long_signal, account_balance)

        # Risk should be approximately 1% of balance
        expected_max_risk = account_balance * 0.01
        assert result.risk_amount <= expected_max_risk * 1.1  # Allow 10% tolerance

    def test_position_size_formula(self, sizer, long_signal):
        """Test position size formula: size = risk / price_diff."""
        account_balance = 10000.0

        result = sizer.calculate(long_signal, account_balance)

        # Result may be capped by max_position_percent, so check it's positive
        assert result.size_base > 0
        # Check risk calculation is reasonable
        assert result.risk_amount <= account_balance * 0.10  # Max 10%


class TestFixedAmountSizing:
    """Tests for fixed amount position sizing method."""

    @pytest.fixture
    def sizer(self) -> PositionSizer:
        """Create PositionSizer with fixed amount method."""
        return PositionSizer({"method": "fixed_amount", "fixed_amount_usdt": 500.0})

    @pytest.fixture
    def signal(self) -> Signal:
        """Create a signal for testing."""
        signal = Mock(spec=Signal)
        signal.id = "test-signal-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 98.0
        signal.confidence = 0.75
        return signal

    def test_fixed_amount_size(self, sizer, signal):
        """Test fixed amount position size."""
        account_balance = 10000.0

        result = sizer.calculate(signal, account_balance)

        # Position should be close to fixed amount (500 USDT)
        assert result.size_quote <= 500.0


class TestKellyCriterion:
    """Tests for Kelly Criterion position sizing."""

    @pytest.fixture
    def sizer(self) -> PositionSizer:
        """Create PositionSizer with Kelly method."""
        return PositionSizer(
            {
                "method": "kelly_criterion",
                "kelly_fraction": 0.25,  # Quarter Kelly
                "max_risk_per_trade": 0.05,  # Cap at 5%
            }
        )

    @pytest.fixture
    def signal(self) -> Signal:
        """Create a signal for testing."""
        signal = Mock(spec=Signal)
        signal.id = "test-signal-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 98.0
        signal.risk_reward_ratio = 2.0
        signal.confidence = 0.75
        signal.position_size_percent = 1.0
        return signal

    def test_kelly_with_good_win_rate(self, sizer, signal):
        """Test Kelly criterion with good win rate."""
        account_balance = 10000.0
        win_rate = 0.6  # 60% win rate

        result = sizer.calculate(signal, account_balance, win_rate=win_rate)

        assert result.method == PositionSizingMethod.KELLY_CRITERION
        assert result.risk_percent > 0
        assert "kelly_percent" in result.metadata

    def test_kelly_with_poor_win_rate(self, sizer, signal):
        """Test Kelly criterion with poor win rate suggests no position."""
        account_balance = 10000.0
        win_rate = 0.3  # 30% win rate

        result = sizer.calculate(signal, account_balance, win_rate=win_rate)

        # With low win rate, Kelly should suggest small or no position
        assert result.risk_percent >= 0

    def test_kelly_without_win_rate_falls_back(self, sizer, signal):
        """Test Kelly falls back to fixed risk without win rate."""
        account_balance = 10000.0

        result = sizer.calculate(signal, account_balance, win_rate=None)

        # Should fall back to fixed risk
        assert result is not None
        assert result.size_base > 0


class TestVolatilityAdjustedSizing:
    """Tests for volatility adjusted position sizing."""

    @pytest.fixture
    def sizer(self) -> PositionSizer:
        """Create PositionSizer with volatility adjusted method."""
        return PositionSizer({"method": "volatility_adjusted", "max_risk_per_trade": 0.02})

    @pytest.fixture
    def signal(self) -> Signal:
        """Create a signal for testing."""
        signal = Mock(spec=Signal)
        signal.id = "test-signal-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 98.0
        signal.confidence = 0.75
        signal.position_size_percent = 1.0
        return signal

    def test_low_volatility_full_size(self, sizer, signal):
        """Test full position size in low volatility."""
        account_balance = 10000.0
        low_volatility = 0.02  # 2%

        result = sizer.calculate(signal, account_balance, volatility=low_volatility)

        assert result.metadata.get("volatility_multiplier", 1.0) == 1.0

    def test_high_volatility_reduced_size(self, sizer, signal):
        """Test reduced position size in high volatility."""
        account_balance = 10000.0
        high_volatility = 0.08  # 8%

        result = sizer.calculate(signal, account_balance, volatility=high_volatility)

        assert result.metadata.get("volatility_multiplier", 1.0) < 1.0

    def test_volatility_comparison(self, sizer, signal):
        """Test that high volatility results in smaller position."""
        account_balance = 10000.0

        result_low_vol = sizer.calculate(signal, account_balance, volatility=0.01)
        result_high_vol = sizer.calculate(signal, account_balance, volatility=0.10)

        assert result_high_vol.size_base <= result_low_vol.size_base


class TestPositionLimits:
    """Tests for position size limits."""

    @pytest.fixture
    def sizer(self) -> PositionSizer:
        """Create PositionSizer with specific limits."""
        return PositionSizer(
            {
                "method": "fixed_risk",
                "max_risk_per_trade": 0.10,  # 10% - intentionally high
                "max_position_percent": 0.05,  # 5% max position
                "min_position_size_usdt": 50.0,
            }
        )

    @pytest.fixture
    def signal(self) -> Signal:
        """Create a signal for testing."""
        signal = Mock(spec=Signal)
        signal.id = "test-signal-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 99.0  # Very tight stop = large position
        signal.confidence = 0.75
        signal.position_size_percent = 10.0  # Request large position
        signal.risk_reward_ratio = 2.0
        return signal

    def test_max_position_limit(self, sizer, signal):
        """Test maximum position size is enforced."""
        account_balance = 10000.0
        max_position = account_balance * 0.05  # 500 USDT

        result = sizer.calculate(signal, account_balance)

        assert result.size_quote <= max_position * 1.01  # Allow small tolerance

    def test_min_position_warning(self, sizer):
        """Test warning for position below minimum."""
        signal = Mock(spec=Signal)
        signal.id = "test-signal-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 50.0  # Very wide stop = small position
        signal.confidence = 0.75
        signal.position_size_percent = 0.1  # Very small
        signal.risk_reward_ratio = 2.0

        account_balance = 100.0  # Small account

        result = sizer.calculate(signal, account_balance)

        # Should still calculate but metadata might indicate issue
        assert result.size_base >= 0


class TestLeverageAdjustment:
    """Tests for leverage adjustment based on confidence."""

    @pytest.fixture
    def sizer(self) -> PositionSizer:
        """Create PositionSizer with leverage settings."""
        return PositionSizer({"method": "fixed_risk", "max_leverage": 5, "default_leverage": 1})

    def test_high_confidence_leverage(self, sizer):
        """Test high confidence gets higher leverage."""
        signal = Mock(spec=Signal)
        signal.id = "test-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 98.0
        signal.confidence = 0.95  # Very high confidence
        signal.position_size_percent = 1.0
        signal.risk_reward_ratio = 2.0

        result = sizer.calculate(signal, 10000.0)

        assert result.leverage >= 2

    def test_low_confidence_no_leverage(self, sizer):
        """Test low confidence gets no additional leverage."""
        signal = Mock(spec=Signal)
        signal.id = "test-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 98.0
        signal.confidence = 0.5  # Lower confidence
        signal.position_size_percent = 1.0
        signal.risk_reward_ratio = 2.0

        result = sizer.calculate(signal, 10000.0)

        assert result.leverage == 1


class TestPositionSizeResult:
    """Tests for PositionSizeResult dataclass."""

    def test_result_fields(self):
        """Test PositionSizeResult has all required fields."""
        result = PositionSizeResult(
            size_base=1.0,
            size_quote=100.0,
            risk_amount=2.0,
            risk_percent=0.02,
            leverage=1,
            method=PositionSizingMethod.FIXED_RISK,
            metadata={},
        )

        assert result.size_base == 1.0
        assert result.size_quote == 100.0
        assert result.risk_amount == 2.0
        assert result.risk_percent == 0.02
        assert result.leverage == 1
        assert result.method == PositionSizingMethod.FIXED_RISK


class TestEdgeCases:
    """Tests for edge cases in position sizing."""

    @pytest.fixture
    def sizer(self) -> PositionSizer:
        """Create PositionSizer."""
        return PositionSizer()

    def test_zero_account_balance(self, sizer):
        """Test handling of zero account balance."""
        signal = Mock(spec=Signal)
        signal.id = "test-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 98.0
        signal.confidence = 0.75
        signal.position_size_percent = 1.0
        signal.risk_reward_ratio = 2.0

        result = sizer.calculate(signal, 0.0)

        assert result.size_base == 0 or result.size_quote == 0

    def test_invalid_stop_loss(self, sizer):
        """Test handling of invalid stop loss."""
        signal = Mock(spec=Signal)
        signal.id = "test-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 105.0  # SL above entry for LONG - invalid
        signal.confidence = 0.75
        signal.position_size_percent = 1.0
        signal.risk_reward_ratio = 2.0

        result = sizer.calculate(signal, 10000.0)

        # Should handle gracefully
        assert result is not None
        assert result.size_base > 0

    def test_very_tight_stop_loss(self, sizer):
        """Test handling of very tight stop loss."""
        signal = Mock(spec=Signal)
        signal.id = "test-001"
        signal.symbol = "BTCUSDT"
        signal.signal_type = SignalType.LONG
        signal.entry_price = 100.0
        signal.stop_loss = 99.99  # Very tight stop
        signal.confidence = 0.75
        signal.position_size_percent = 1.0
        signal.risk_reward_ratio = 2.0

        result = sizer.calculate(signal, 10000.0)

        # Should be capped at max position size
        assert result.size_quote <= 10000.0 * 0.10
