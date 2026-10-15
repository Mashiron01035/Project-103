"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 22.06.2026.
    File        : test_money_path.py
    Project     : Project-103
    Description : Money-path invariants for the backtest portfolio — the math
                  that, if wrong, silently mis-states every PnL. Covers PnL sign
                  and contract-size scaling, SL/TP direction, the trailing-stop
                  ratchet, fee impact, balance conservation, and partial-close
                  conservation.

    SPDX-License-Identifier: AGPL-3.0

"""

from datetime import datetime

import pytest

from src.backtesting.portfolio.commission_model import FixedCommission, ZeroCommission
from src.backtesting.portfolio.portfolio_manager import BacktestPosition, PortfolioManager
from src.backtesting.portfolio.slippage_model import ZeroSlippage

TS = datetime(2026, 1, 1, 12, 0, 0)


def _make_position(side: str, entry: float, qty: float = 1.0, cs: float = 1.0) -> BacktestPosition:
    pos = BacktestPosition(
        symbol="XAUUSD",
        side=side,
        quantity=qty,
        initial_quantity=qty,
        entry_price=entry,
        current_price=entry,
        contract_size=cs,
        highest_price=entry,
        lowest_price=entry,
    )
    return pos


def _clean_pm(balance: float = 100_000.0, leverage: float = 1.0) -> PortfolioManager:
    """Portfolio manager with zero frictions, so realized PnL is pure price math."""
    return PortfolioManager(
        initial_balance=balance,
        commission_model=ZeroCommission(),
        slippage_model=ZeroSlippage(),
        leverage=leverage,
    )


class TestUnrealizedPnLSign:
    """A position must gain when it should and lose when it should — never inverted."""

    def test_long_gains_when_price_rises(self):
        pos = _make_position("LONG", entry=100.0)
        pos.update_price(110.0)
        assert pos.unrealized_pnl == pytest.approx(10.0)

    def test_long_loses_when_price_falls(self):
        pos = _make_position("LONG", entry=100.0)
        pos.update_price(90.0)
        assert pos.unrealized_pnl == pytest.approx(-10.0)

    def test_short_gains_when_price_falls(self):
        pos = _make_position("SHORT", entry=100.0)
        pos.update_price(90.0)
        assert pos.unrealized_pnl == pytest.approx(10.0)

    def test_short_loses_when_price_rises(self):
        pos = _make_position("SHORT", entry=100.0)
        pos.update_price(110.0)
        assert pos.unrealized_pnl == pytest.approx(-10.0)


class TestContractSizeScaling:
    """contract_size is the crypto(1)/XAUUSD(100) lever — PnL must scale linearly with it."""

    def test_xauusd_pnl_is_contract_size_times_crypto(self):
        crypto = _make_position("LONG", entry=100.0, qty=1.0, cs=1.0)
        gold = _make_position("LONG", entry=100.0, qty=1.0, cs=100.0)
        crypto.update_price(101.0)
        gold.update_price(101.0)
        assert gold.unrealized_pnl == pytest.approx(crypto.unrealized_pnl * 100.0)
        assert gold.unrealized_pnl == pytest.approx(100.0)  # $1 move * 1 lot * 100


class TestStopAndTargetDirection:
    """SL/TP triggers are side-dependent; an inverted comparison would close at the wrong time."""

    def test_long_stop_triggers_below_not_above(self):
        pos = _make_position("LONG", entry=100.0)
        pos.stop_loss = 95.0
        assert pos.check_stop_loss(94.99) is True
        assert pos.check_stop_loss(95.01) is False

    def test_short_stop_triggers_above_not_below(self):
        pos = _make_position("SHORT", entry=100.0)
        pos.stop_loss = 105.0
        assert pos.check_stop_loss(105.01) is True
        assert pos.check_stop_loss(104.99) is False

    def test_long_target_triggers_above(self):
        pos = _make_position("LONG", entry=100.0)
        pos.take_profit = 110.0
        assert pos.check_take_profit(110.0) is True
        assert pos.check_take_profit(109.99) is False

    def test_multi_tp_returns_first_unhit_level(self):
        pos = _make_position("LONG", entry=100.0)
        pos.take_profits = [110.0, 120.0, 130.0]
        pos.tp_hit = [False, False, False]
        assert pos.check_take_profits(110.0) == 0
        pos.tp_hit[0] = True
        # TP1 already taken -> price at 120 should now surface TP2 (index 1)
        assert pos.check_take_profits(120.0) == 1
        assert pos.check_take_profits(115.0) is None


class TestTrailingRatchet:
    """A trailing stop may only tighten in the favorable direction — never loosen."""

    def test_long_trailing_never_moves_down(self):
        pos = _make_position("LONG", entry=100.0)
        pos.trailing_activated = True
        pos.trailing_stop_distance = 5.0

        pos.update_price(110.0)
        assert pos.stop_loss == pytest.approx(105.0)

        pos.update_price(120.0)  # higher high -> stop ratchets up
        assert pos.stop_loss == pytest.approx(115.0)

        pos.update_price(112.0)  # pullback -> stop must hold, not loosen
        assert pos.stop_loss == pytest.approx(115.0)

    def test_short_trailing_never_moves_up(self):
        pos = _make_position("SHORT", entry=100.0)
        pos.trailing_activated = True
        pos.trailing_stop_distance = 5.0

        pos.update_price(90.0)
        assert pos.stop_loss == pytest.approx(95.0)

        pos.update_price(80.0)
        assert pos.stop_loss == pytest.approx(85.0)

        pos.update_price(88.0)  # bounce -> stop holds
        assert pos.stop_loss == pytest.approx(85.0)


class TestRealizedPnLLifecycle:
    """Full open -> close round trips through PortfolioManager."""

    def test_long_winner_realizes_price_difference(self):
        pm = _clean_pm()
        pos = pm.open_position("XAUUSD", "LONG", 1.0, 100.0, TS)
        trade = pm.close_position(pos.id, 110.0, TS)
        assert trade.pnl == pytest.approx(10.0)
        assert trade.net_pnl == pytest.approx(10.0)  # zero frictions

    def test_short_winner_realizes_price_difference(self):
        pm = _clean_pm()
        pos = pm.open_position("XAUUSD", "SHORT", 1.0, 100.0, TS)
        trade = pm.close_position(pos.id, 90.0, TS)
        assert trade.pnl == pytest.approx(10.0)

    def test_stop_loss_caps_the_loss(self):
        pm = _clean_pm()
        pos = pm.open_position("XAUUSD", "LONG", 1.0, 100.0, TS, stop_loss=95.0)
        trade = pm.close_position(pos.id, 95.0, TS, exit_reason="stop_loss")
        # Loss is bounded to the stop distance, nothing worse.
        assert trade.net_pnl == pytest.approx(-5.0)

    def test_balance_conserved_through_round_trip(self):
        pm = _clean_pm(balance=100_000.0, leverage=1.0)
        pos = pm.open_position("XAUUSD", "LONG", 2.0, 100.0, TS)
        pm.close_position(pos.id, 105.0, TS)
        # Margin fully returned; balance moves by exactly realized PnL (2 lots * $5).
        assert float(pm.current_balance) == pytest.approx(100_000.0 + 10.0)


class TestFeesReducePnL:
    """Net PnL must never flatter gross PnL — fees only ever subtract."""

    def test_fees_make_a_flat_trade_negative(self):
        pm = PortfolioManager(
            initial_balance=100_000.0,
            commission_model=FixedCommission(maker_fee=0.0002, taker_fee=0.0004),
            slippage_model=ZeroSlippage(),
            leverage=1.0,
        )
        pos = pm.open_position("XAUUSD", "LONG", 1.0, 100.0, TS)  # taker entry
        trade = pm.close_position(pos.id, 100.0, TS)  # same price, taker exit
        # Gross is zero; net is exactly the two taker fees (entry + exit).
        expected_fee = 100.0 * 0.0004
        assert trade.pnl == pytest.approx(0.0)
        assert trade.net_pnl == pytest.approx(-2 * expected_fee)
        assert trade.net_pnl < trade.pnl

    def test_zero_commission_makes_net_equal_gross(self):
        pm = _clean_pm()
        pos = pm.open_position("XAUUSD", "LONG", 1.0, 100.0, TS)
        trade = pm.close_position(pos.id, 108.0, TS)
        assert trade.net_pnl == pytest.approx(trade.pnl)


class TestPartialCloseConservation:
    """Closing a position in pieces must total exactly the same PnL as one close."""

    def test_two_halves_sum_to_full_close(self):
        pm = _clean_pm()
        pos = pm.open_position("XAUUSD", "LONG", 1.0, 100.0, TS)

        first = pm.partial_close_position(pos.id, 0.5, 110.0, TS)
        assert pos.quantity == pytest.approx(0.5)  # remaining size tracked

        second = pm.close_position(pos.id, 110.0, TS)
        # 1 lot * $10 move = $10, regardless of how it was sliced.
        assert first.pnl + second.pnl == pytest.approx(10.0)


class TestMarginGuard:
    """Opening must be refused when margin exceeds available balance."""

    def test_insufficient_margin_returns_none(self):
        pm = _clean_pm(balance=5.0, leverage=1.0)  # notional 100 > balance 5
        pos = pm.open_position("XAUUSD", "LONG", 1.0, 100.0, TS)
        assert pos is None


class TestBalanceType:
    """Regression: a plain float balance must work, not just a Decimal."""

    def test_accepts_float_balance(self):
        # Balances are Decimal internally; passing a float used to crash on the
        # first open_position with `float -= Decimal`. The constructor now coerces.
        pm = _clean_pm(balance=10_000.0)
        pos = pm.open_position("XAUUSD", "LONG", 1.0, 100.0, TS)
        assert pos is not None
        trade = pm.close_position(pos.id, 105.0, TS)
        assert trade.net_pnl == pytest.approx(5.0)


class TestEquityIncludesLockedMargin:
    """Regression: margin locked in open positions is still the trader's money.

    `open_position` moves margin out of `current_balance`; equity used to be
    `current_balance + unrealized`, so every open position showed up as a
    drawdown the size of its margin (an 85% "max drawdown" on a run whose worst
    week was -$300).
    """

    def test_flat_open_position_does_not_move_equity(self):
        pm = _clean_pm(balance=10_000.0, leverage=10.0)
        pm.open_position("XAUUSD", "LONG", 1.0, 1_000.0, TS)
        pm.update_equity(TS, {"XAUUSD": 1_000.0})
        point = pm.equity_curve[-1]
        assert float(point.total_equity) == pytest.approx(10_000.0)
        assert point.drawdown == pytest.approx(0.0)

    def test_equity_tracks_unrealized_on_top_of_margin(self):
        pm = _clean_pm(balance=10_000.0, leverage=10.0)
        pos = pm.open_position("XAUUSD", "LONG", 1.0, 1_000.0, TS)
        move = 10.0 * pos.contract_size  # $10 move on 1 lot
        pm.update_equity(TS, {"XAUUSD": 1_010.0})
        assert float(pm.equity_curve[-1].total_equity) == pytest.approx(10_000.0 + move)
