"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : test_cost_models.py
    Project     : Project-103
    Description : Execution-cost models — commission and slippage. Covers the
                  crypto %-notional vs forex $/lot distinction (a real source of
                  mis-charged backtests), maker/taker ordering, the round-trip
                  flag, and the invariant that slippage is always unfavorable.

    SPDX-License-Identifier: AGPL-3.0

"""

import pytest

from src.backtesting.portfolio.commission_model import (
    FixedCommission,
    PerLotCommission,
    RebateCommission,
    ZeroCommission,
    create_commission_model,
)
from src.backtesting.portfolio.slippage_model import (
    FixedSlippage,
    VolumeBasedSlippage,
    ZeroSlippage,
    create_slippage_model,
)


class TestFixedCommission:
    def test_commission_is_rate_times_notional(self):
        model = FixedCommission(maker_fee=0.0002, taker_fee=0.0004)
        # notional = qty * price * contract_size
        assert model.calculate(2.0, 100.0, is_maker=False) == pytest.approx(2 * 100 * 0.0004)

    def test_taker_costs_more_than_maker(self):
        model = FixedCommission(maker_fee=0.0002, taker_fee=0.0004)
        maker = model.calculate(1.0, 100.0, is_maker=True)
        taker = model.calculate(1.0, 100.0, is_maker=False)
        assert taker > maker > 0

    def test_contract_size_scales_commission(self):
        model = FixedCommission(taker_fee=0.0004)
        crypto = model.calculate(1.0, 100.0, contract_size=1.0)
        gold = model.calculate(1.0, 100.0, contract_size=100.0)
        assert gold == pytest.approx(crypto * 100.0)


class TestPerLotCommission:
    """Forex/CFD brokers charge a flat $/lot — independent of price, charged once."""

    def test_commission_is_per_lot_times_quantity(self):
        model = PerLotCommission(commission_per_lot=7.0)
        assert model.calculate(2.0, 100.0) == pytest.approx(14.0)

    def test_commission_is_price_independent(self):
        model = PerLotCommission(commission_per_lot=6.0)
        cheap = model.calculate(1.0, 100.0)
        pricey = model.calculate(1.0, 99_999.0)
        assert cheap == pricey == pytest.approx(6.0)

    def test_is_round_trip(self):
        # The engine reads this flag to skip charging commission again at exit.
        assert PerLotCommission().round_trip is True


class TestRebateAndZero:
    def test_maker_rebate_is_negative(self):
        model = RebateCommission(maker_rebate=0.0001, taker_fee=0.0004)
        assert model.calculate(1.0, 100.0, is_maker=True) < 0  # paid TO the maker
        assert model.calculate(1.0, 100.0, is_maker=False) > 0

    def test_zero_commission_is_zero(self):
        assert ZeroCommission().calculate(10.0, 12345.0) == 0.0


class TestCommissionFactory:
    @pytest.mark.parametrize(
        "name,cls",
        [
            ("fixed", FixedCommission),
            ("per_lot", PerLotCommission),
            ("zero", ZeroCommission),
        ],
    )
    def test_factory_builds_requested_model(self, name, cls):
        assert isinstance(create_commission_model(name), cls)

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError):
            create_commission_model("does_not_exist")


class TestSlippageDirection:
    """Slippage is always against the trader: pay up on a buy, receive less on a sell."""

    def test_fixed_slippage_sign_and_magnitude(self):
        model = FixedSlippage(slippage_pct=0.001)
        assert model.calculate("BUY", 1.0, 100.0) == pytest.approx(0.1)
        assert model.calculate("SELL", 1.0, 100.0) == pytest.approx(-0.1)

    def test_zero_slippage_is_zero(self):
        assert ZeroSlippage().calculate("BUY", 1.0, 100.0) == 0.0

    @pytest.mark.parametrize(
        "model",
        [FixedSlippage(slippage_pct=0.0005), VolumeBasedSlippage(base_slippage=0.0003)],
    )
    def test_slippage_is_always_unfavorable(self, model):
        buy = model.calculate("BUY", 1.0, 100.0)
        sell = model.calculate("SELL", 1.0, 100.0)
        assert buy >= 0  # buying never gets a better-than-quoted fill
        assert sell <= 0  # selling never gets a better-than-quoted fill


class TestSlippageFactory:
    def test_factory_builds_fixed(self):
        assert isinstance(create_slippage_model("fixed"), FixedSlippage)

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError):
            create_slippage_model("nope")
