"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : portfolio_manager.py
    Project     : Project-103
    Description : Portfolio manager for backtesting. Manages positions, balances,
                  and equity tracking during backtest execution.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.backtesting.models import EquityPoint, TradeResult
from src.backtesting.portfolio.commission_model import (
    CommissionModel,
    FixedCommission,
    PerLotCommission,
)
from src.backtesting.portfolio.slippage_model import FixedSlippage, MarketState, SlippageModel

logger = logging.getLogger(__name__)


@dataclass
class BacktestPosition:
    """Position in backtesting context."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    side: str = ""  # LONG or SHORT
    quantity: float = 0.0
    initial_quantity: float = 0.0  # Original quantity before partial closes
    entry_price: float = 0.0
    entry_time: datetime = field(default_factory=datetime.now)
    entry_commission: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    # Risk levels
    stop_loss: Optional[float] = None
    original_stop_loss: Optional[float] = None  # Original SL before BE
    take_profit: Optional[float] = None  # Legacy single TP

    # Multiple take profits
    take_profits: List[float] = field(default_factory=list)  # [TP1, TP2, TP3]
    tp_distribution: List[float] = field(default_factory=list)  # [0.6, 0.3, 0.1]
    tp_hit: List[bool] = field(default_factory=list)  # Track which TPs are hit

    # Breakeven and trailing
    breakeven_activated: bool = False
    breakeven_level: int = 0  # 0=none, 1=after TP1 (1%), 2=after 3% (SL=2%)
    trailing_activated: bool = False
    trailing_stop_distance: float = 0.0  # Distance to trail

    # DCA (Dollar Cost Averaging)
    dca_count: int = 0  # Number of DCA entries made
    dca_entries: List[Dict] = field(default_factory=list)  # [{price, quantity, time}]
    dca_next_level_price: Optional[float] = None  # Price for next DCA trigger
    total_cost: float = 0.0  # Total cost of all entries (for avg calculation)

    # Instrument properties
    contract_size: float = 1.0  # Units per lot: 1.0 for crypto, 100 for XAUUSD
    # USDxxx (USDJPY+): PnL is in the quote currency -> USD by dividing by the rate (as MT5 does)
    usd_base: bool = False

    # Margin tracking (for leveraged trading)
    margin_used: float = 0.0  # Margin locked for this position
    leverage: float = 10.0  # Leverage used

    # Strategy info
    strategy_name: str = ""
    signal_id: Optional[str] = None

    # Metadata (for regime, signal details, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Tracking
    highest_price: float = 0.0
    lowest_price: float = 0.0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0

    def quote_to_usd(self, price: float) -> float:
        """Quote-currency -> USD multiplier: 1/price for USDxxx, otherwise 1."""
        return 1.0 / price if self.usd_base and price > 0 else 1.0

    def update_price(self, new_price: float):
        """Update current price and track MFE/MAE."""
        self.current_price = new_price

        # Track high/low
        if new_price > self.highest_price or self.highest_price == 0:
            self.highest_price = new_price
        if new_price < self.lowest_price or self.lowest_price == 0:
            self.lowest_price = new_price

        # Calculate unrealized PnL (contract_size: 1.0 for crypto, 100 for XAUUSD), in USD
        cs = self.contract_size * self.quote_to_usd(new_price)
        if self.side == "LONG":
            self.unrealized_pnl = (new_price - self.entry_price) * self.quantity * cs
            # MFE/MAE for long
            self.max_favorable_excursion = max(
                self.max_favorable_excursion,
                (self.highest_price - self.entry_price) * self.quantity * cs,
            )
            self.max_adverse_excursion = min(
                self.max_adverse_excursion,
                (self.lowest_price - self.entry_price) * self.quantity * cs,
            )
            # Trailing stop for LONG - move SL up with price
            if self.trailing_activated and self.trailing_stop_distance > 0:
                new_sl = new_price - self.trailing_stop_distance
                if self.stop_loss is None or new_sl > self.stop_loss:
                    self.stop_loss = new_sl
        else:  # SHORT
            self.unrealized_pnl = (self.entry_price - new_price) * self.quantity * cs
            # MFE/MAE for short
            self.max_favorable_excursion = max(
                self.max_favorable_excursion,
                (self.entry_price - self.lowest_price) * self.quantity * cs,
            )
            self.max_adverse_excursion = min(
                self.max_adverse_excursion,
                (self.entry_price - self.highest_price) * self.quantity * cs,
            )
            # Trailing stop for SHORT - move SL down with price
            if self.trailing_activated and self.trailing_stop_distance > 0:
                new_sl = new_price + self.trailing_stop_distance
                if self.stop_loss is None or new_sl < self.stop_loss:
                    self.stop_loss = new_sl

    def check_stop_loss(self, current_price: float) -> bool:
        """Check if stop loss is hit."""
        if self.stop_loss is None:
            return False

        if self.side == "LONG":
            return current_price <= self.stop_loss
        else:
            return current_price >= self.stop_loss

    def check_take_profit(self, current_price: float) -> bool:
        """Check if take profit is hit (legacy single TP)."""
        if self.take_profit is None:
            return False

        if self.side == "LONG":
            return current_price >= self.take_profit
        else:
            return current_price <= self.take_profit

    def check_take_profits(self, current_price: float) -> Optional[int]:
        """
        Check which take profit level is hit.

        Returns:
            Index of TP hit (0, 1, 2) or None if no TP hit
        """
        if not self.take_profits:
            return None

        for i, tp in enumerate(self.take_profits):
            if i < len(self.tp_hit) and self.tp_hit[i]:
                continue  # Already hit

            if self.side == "LONG":
                if current_price >= tp:
                    return i
            else:  # SHORT
                if current_price <= tp:
                    return i

        return None

    def activate_breakeven(self, lock_profit_ratio: float = 0.5):
        """
        Move stop loss to lock profit after TP1.

        Args:
            lock_profit_ratio: How much of TP1 profit to lock (0.5 = 50%)
        """
        if not self.breakeven_activated:
            self.original_stop_loss = self.stop_loss

            # Calculate SL that locks profit (not just breakeven)
            if self.take_profits and len(self.take_profits) > 0:
                tp1 = self.take_profits[0]
                tp1_distance = abs(tp1 - self.entry_price)

                if self.side == "LONG":
                    # Lock 50% of TP1 profit
                    self.stop_loss = self.entry_price + (tp1_distance * lock_profit_ratio)
                else:
                    self.stop_loss = self.entry_price - (tp1_distance * lock_profit_ratio)

                logger.debug(
                    f"BE activated for {self.symbol}: SL={self.stop_loss:.4f} (locked {lock_profit_ratio*100:.0f}% of TP1)"
                )
            else:
                # No TPs — use best price (highest for LONG, lowest for SHORT)
                if self.side == "LONG":
                    profit_distance = self.highest_price - self.entry_price
                    if profit_distance > 0:
                        self.stop_loss = self.entry_price + (profit_distance * lock_profit_ratio)
                    else:
                        self.stop_loss = self.entry_price
                else:
                    profit_distance = self.entry_price - self.lowest_price
                    if profit_distance > 0:
                        self.stop_loss = self.entry_price - (profit_distance * lock_profit_ratio)
                    else:
                        self.stop_loss = self.entry_price
                logger.debug(
                    f"BE activated for {self.symbol}: SL={self.stop_loss:.4f} (locked {lock_profit_ratio*100:.0f}% of best price profit)"
                )

            self.breakeven_activated = True
            self.breakeven_level = 1

    def check_upgrade_breakeven(self, current_price: float):
        """
        Check if we should upgrade BE level (at 3% profit -> SL = 2%).

        Args:
            current_price: Current market price
        """
        if not self.breakeven_activated or self.breakeven_level >= 2:
            return

        # Calculate current profit percent
        if self.side == "LONG":
            profit_pct = (current_price - self.entry_price) / self.entry_price
        else:
            profit_pct = (self.entry_price - current_price) / self.entry_price

        # At 3% profit, move SL to lock 2%
        if profit_pct >= 0.03:
            lock_price = self.entry_price * 1.02 if self.side == "LONG" else self.entry_price * 0.98

            # Only upgrade if new SL is better
            if self.side == "LONG" and lock_price > self.stop_loss:
                self.stop_loss = lock_price
                self.breakeven_level = 2
                logger.debug(f"BE Level 2 for {self.symbol}: SL={self.stop_loss:.4f} (locked 2%)")
            elif self.side == "SHORT" and lock_price < self.stop_loss:
                self.stop_loss = lock_price
                self.breakeven_level = 2
                logger.debug(f"BE Level 2 for {self.symbol}: SL={self.stop_loss:.4f} (locked 2%)")

    def is_stop_loss_breakeven(self) -> bool:
        """Check if current SL is a breakeven (profit-locking) stop."""
        if not self.breakeven_activated:
            return False

        # If BE is active and SL is above entry (LONG) or below entry (SHORT)
        if self.side == "LONG":
            return self.stop_loss is not None and self.stop_loss >= self.entry_price
        else:
            return self.stop_loss is not None and self.stop_loss <= self.entry_price

    def activate_trailing(self, distance: float, start_from_tp1: bool = True):
        """
        Activate trailing stop after TP2.

        Args:
            distance: Distance to trail behind price
            start_from_tp1: If True, SL starts at TP1 level (not current price - distance)
        """
        if not self.trailing_activated:
            self.trailing_stop_distance = distance
            self.trailing_activated = True

            # Set initial trailing SL - start from TP1 level
            if start_from_tp1 and self.take_profits and len(self.take_profits) > 0:
                tp1 = self.take_profits[0]
                self.stop_loss = tp1
                logger.debug(
                    f"Trailing activated for {self.symbol}: SL at TP1={tp1:.4f}, trail={distance:.4f}"
                )
            else:
                # Fallback to current price - distance
                if self.side == "LONG":
                    self.stop_loss = self.current_price - distance
                else:
                    self.stop_loss = self.current_price + distance
                logger.debug(
                    f"Trailing activated for {self.symbol}: SL={self.stop_loss:.4f}, trail={distance:.4f}"
                )


class PortfolioManager:
    """
    Manages portfolio state during backtesting.

    Tracks balances, positions, trades, and equity curve.
    """

    def __init__(
        self,
        initial_balance: Decimal,
        commission_model: Optional[CommissionModel] = None,
        slippage_model: Optional[SlippageModel] = None,
        leverage: float = 10.0,
        balance_cap: float = 0.0,
    ):
        """
        Initialize portfolio manager.

        Args:
            initial_balance: Starting balance in quote currency
            commission_model: Model for calculating commissions
            slippage_model: Model for calculating slippage
            leverage: Trading leverage (default 10x)
            balance_cap: Same cap the engine uses for position sizing
                (>=100 dollar mode, 0<cap<100 percent mode, 0 off). Batch
                thresholds are measured against the capped base so that the
                size/threshold ratio stays constant as the account compounds.
        """
        # Balances are kept as Decimal for exact money arithmetic. Coerce here so a
        # float/int caller works the same as the engine's Decimal (avoids a
        # float -= Decimal TypeError on the first open_position).
        self.initial_balance = Decimal(str(initial_balance))
        self.current_balance = Decimal(str(initial_balance))
        self.commission_model = commission_model or FixedCommission()
        self.slippage_model = slippage_model or FixedSlippage()
        self.leverage = leverage
        self.balance_cap = balance_cap

        # State
        self.positions: Dict[str, BacktestPosition] = {}
        self.trades: List[TradeResult] = []
        self.equity_curve: List[EquityPoint] = []

        # Tracking
        self.total_commission = 0.0
        self.total_slippage = 0.0
        self.total_margin_used = 0.0  # Track margin usage
        self.peak_equity = float(initial_balance)
        self.max_drawdown = 0.0
        self.max_drawdown_pct = 0.0

        # Fan batch trailing state (keyed by batch_id, NOT stored on positions)
        self._fan_trailing_state: Dict[str, Dict[str, Any]] = {}

        logger.info(
            f"PortfolioManager initialized with ${float(initial_balance):,.2f}, leverage={leverage}x"
        )

    def _fee_to_usd(self, fee: float, q2u: float) -> float:
        """A percentage fee is computed on the quote-currency notional -> converted to USD.
        PerLotCommission is already in $ per lot — left untouched."""
        if q2u == 1.0 or isinstance(self.commission_model, PerLotCommission):
            return fee
        return fee * q2u

    def open_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        timestamp: datetime,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        take_profits: Optional[List[float]] = None,
        tp_distribution: Optional[List[float]] = None,
        strategy_name: str = "",
        signal_id: Optional[str] = None,
        market_state: Optional[MarketState] = None,
        metadata: Optional[Dict[str, Any]] = None,
        contract_size: float = 1.0,
        fee_type: str = "taker",
    ) -> Optional[BacktestPosition]:
        """
        Open a new position.

        Args:
            symbol: Trading symbol
            side: 'LONG' or 'SHORT'
            quantity: Position size
            price: Entry price
            timestamp: Entry time
            stop_loss: Stop loss price
            take_profit: Take profit price
            strategy_name: Strategy that generated the signal
            signal_id: ID of the signal
            market_state: Current market state for slippage

        Returns:
            Created position or None if insufficient balance
        """
        # Calculate slippage
        slippage = self.slippage_model.calculate(
            side="BUY" if side == "LONG" else "SELL",
            quantity=quantity,
            price=price,
            market_state=market_state,
        )

        execution_price = price + slippage
        from src.core.trading_pairs import trading_pairs as _tp

        usd_base = _tp.usd_is_base(symbol)
        q2u = 1.0 / execution_price if usd_base and execution_price > 0 else 1.0
        self.total_slippage += abs(slippage * quantity * contract_size * q2u)

        # Calculate commission
        commission = self.commission_model.calculate(
            quantity=quantity,
            price=execution_price,
            is_maker=(fee_type == "maker"),
            contract_size=contract_size,
        )
        commission = self._fee_to_usd(commission, q2u)
        self.total_commission += commission

        # Calculate required margin (with leverage)
        # For MT5 instruments: notional = qty * price * contract_size
        notional_value = Decimal(str(quantity * execution_price * contract_size * q2u))
        margin_required = notional_value / Decimal(str(self.leverage))
        total_cost = margin_required + Decimal(str(commission))

        # Check balance (only need margin, not full notional)
        if total_cost > self.current_balance:
            logger.warning(
                f"Insufficient margin to open position. "
                f"Required: ${float(total_cost):,.2f} (margin: ${float(margin_required):,.2f}), "
                f"Available: ${float(self.current_balance):,.2f}"
            )
            return None

        # Deduct margin from balance (not full notional!)
        self.current_balance -= total_cost
        self.total_margin_used += float(margin_required)

        # Create position
        position = BacktestPosition(
            symbol=symbol,
            side=side,
            quantity=quantity,
            initial_quantity=quantity,
            entry_price=execution_price,
            entry_time=timestamp,
            entry_commission=commission,
            current_price=execution_price,
            stop_loss=stop_loss,
            original_stop_loss=stop_loss,
            take_profit=take_profit,
            take_profits=take_profits or [],
            tp_distribution=tp_distribution or [],
            tp_hit=[False] * len(take_profits or []),
            contract_size=contract_size,
            usd_base=usd_base,
            margin_used=float(margin_required),
            leverage=self.leverage,
            strategy_name=strategy_name,
            signal_id=signal_id,
            metadata=metadata or {},
            highest_price=execution_price,
            lowest_price=execution_price,
        )

        self.positions[position.id] = position

        logger.info(
            f"Opened {side} position on {symbol}: "
            f"qty={quantity}, price={execution_price:.2f}, "
            f"SL={stop_loss}, commission={commission:.4f}"
        )

        return position

    def close_position(
        self,
        position_id: str,
        price: float,
        timestamp: datetime,
        exit_reason: str = "signal",
        market_state: Optional[MarketState] = None,
        fee_type: str = "taker",
    ) -> Optional[TradeResult]:
        """
        Close an existing position.

        Args:
            position_id: ID of position to close
            price: Exit price
            timestamp: Exit time
            exit_reason: Reason for closing
            market_state: Current market state for slippage

        Returns:
            TradeResult or None if position not found
        """
        if position_id not in self.positions:
            logger.warning(f"Position {position_id} not found")
            return None

        position = self.positions[position_id]

        # Calculate slippage
        slippage = self.slippage_model.calculate(
            side="SELL" if position.side == "LONG" else "BUY",
            quantity=position.quantity,
            price=price,
            market_state=market_state,
        )

        execution_price = price + slippage
        self.total_slippage += abs(
            slippage
            * position.quantity
            * position.contract_size
            * position.quote_to_usd(execution_price)
        )

        # Calculate exit commission (skip for round-trip models — already charged at entry)
        if getattr(self.commission_model, "round_trip", False):
            commission = 0.0
        else:
            commission = self.commission_model.calculate(
                quantity=position.quantity,
                price=execution_price,
                is_maker=(fee_type == "maker"),
                contract_size=position.contract_size,
            )
            commission = self._fee_to_usd(commission, position.quote_to_usd(execution_price))
        self.total_commission += commission

        # Calculate P&L (contract_size: 1.0 for crypto, 100 for XAUUSD)
        cs = position.contract_size
        if position.side == "LONG":
            pnl = (execution_price - position.entry_price) * position.quantity * cs
        else:
            pnl = (position.entry_price - execution_price) * position.quantity * cs

        pnl_pct = (pnl / (position.entry_price * position.quantity * cs)) * 100
        pnl *= position.quote_to_usd(execution_price)  # USDxxx: JPY -> USD at the exit rate
        net_pnl = pnl - commission - position.entry_commission

        # Update balance (return margin + PnL, not full notional)
        margin_returned = Decimal(str(position.margin_used))
        self.current_balance += margin_returned + Decimal(str(pnl)) - Decimal(str(commission))
        self.total_margin_used -= position.margin_used

        # Enrich metadata with SL/TP for export
        trade_metadata = dict(position.metadata) if position.metadata else {}
        if position.original_stop_loss is not None:
            trade_metadata["stop_loss"] = position.original_stop_loss
        if position.take_profits:
            trade_metadata["take_profits"] = list(position.take_profits)
        trade_metadata["contract_size"] = position.contract_size

        # Create trade result
        trade = TradeResult(
            symbol=position.symbol,
            side=position.side,
            entry_price=position.entry_price,
            entry_quantity=position.quantity,
            entry_time=position.entry_time,
            entry_commission=position.entry_commission,
            exit_price=execution_price,
            exit_time=timestamp,
            exit_commission=commission,
            exit_reason=exit_reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
            net_pnl=net_pnl,
            strategy_name=position.strategy_name,
            signal_id=position.signal_id,
            slippage=abs(slippage * position.quantity),
            hold_time=timestamp - position.entry_time,
            max_favorable_excursion=position.max_favorable_excursion,
            max_adverse_excursion=position.max_adverse_excursion,
            metadata=trade_metadata,
        )

        self.trades.append(trade)
        del self.positions[position_id]

        logger.info(
            f"Closed {position.side} position on {position.symbol}: "
            f"exit={execution_price:.2f}, PnL={net_pnl:.2f} ({pnl_pct:.2f}%), "
            f"reason={exit_reason}"
        )

        return trade

    def partial_close_position(
        self,
        position_id: str,
        close_percent: float,
        price: float,
        timestamp: datetime,
        exit_reason: str = "partial_tp",
        market_state: Optional[MarketState] = None,
        fee_type: str = "taker",
    ) -> Optional[TradeResult]:
        """
        Partially close a position.

        Args:
            position_id: ID of position to partially close
            close_percent: Percentage of position to close (0.0 - 1.0)
            price: Exit price
            timestamp: Exit time
            exit_reason: Reason for closing
            market_state: Current market state for slippage

        Returns:
            TradeResult for the closed portion
        """
        if position_id not in self.positions:
            logger.warning(f"Position {position_id} not found for partial close")
            return None

        position = self.positions[position_id]
        close_quantity = position.quantity * close_percent

        if close_quantity <= 0:
            return None

        # Calculate slippage
        slippage = self.slippage_model.calculate(
            side="SELL" if position.side == "LONG" else "BUY",
            quantity=close_quantity,
            price=price,
            market_state=market_state,
        )

        execution_price = price + slippage
        self.total_slippage += abs(
            slippage
            * close_quantity
            * position.contract_size
            * position.quote_to_usd(execution_price)
        )

        # Calculate exit commission (skip for round-trip models — already charged at entry)
        if getattr(self.commission_model, "round_trip", False):
            commission = 0.0
        else:
            commission = self.commission_model.calculate(
                quantity=close_quantity,
                price=execution_price,
                is_maker=(fee_type == "maker"),
                contract_size=position.contract_size,
            )
            commission = self._fee_to_usd(commission, position.quote_to_usd(execution_price))
        self.total_commission += commission

        # Calculate P&L for closed portion (contract_size: 1.0 for crypto, 100 for XAUUSD)
        cs = position.contract_size
        if position.side == "LONG":
            pnl = (execution_price - position.entry_price) * close_quantity * cs
        else:
            pnl = (position.entry_price - execution_price) * close_quantity * cs

        # Proportional entry commission
        entry_commission_portion = position.entry_commission * close_percent
        pnl_pct = (pnl / (position.entry_price * close_quantity * cs)) * 100
        pnl *= position.quote_to_usd(execution_price)  # USDxxx: JPY -> USD at the exit rate
        net_pnl = pnl - commission - entry_commission_portion

        # Calculate proportional margin to return
        margin_portion = position.margin_used * close_percent

        # Update balance (return margin portion + PnL)
        self.current_balance += (
            Decimal(str(margin_portion)) + Decimal(str(pnl)) - Decimal(str(commission))
        )
        self.total_margin_used -= margin_portion

        # Update position
        position.quantity -= close_quantity
        position.entry_commission -= entry_commission_portion
        position.margin_used -= margin_portion
        position.realized_pnl += net_pnl

        # Enrich metadata with SL/TP for export
        trade_metadata = dict(position.metadata) if position.metadata else {}
        if position.original_stop_loss is not None:
            trade_metadata["stop_loss"] = position.original_stop_loss
        if position.take_profits:
            trade_metadata["take_profits"] = list(position.take_profits)
        trade_metadata["contract_size"] = position.contract_size

        # Create trade result for closed portion
        trade = TradeResult(
            symbol=position.symbol,
            side=position.side,
            entry_price=position.entry_price,
            entry_quantity=close_quantity,
            entry_time=position.entry_time,
            entry_commission=entry_commission_portion,
            exit_price=execution_price,
            exit_time=timestamp,
            exit_commission=commission,
            exit_reason=exit_reason,
            pnl=pnl,
            pnl_pct=pnl_pct,
            net_pnl=net_pnl,
            strategy_name=position.strategy_name,
            signal_id=position.signal_id,
            slippage=abs(slippage * close_quantity),
            hold_time=timestamp - position.entry_time,
            max_favorable_excursion=position.max_favorable_excursion,
            max_adverse_excursion=position.max_adverse_excursion,
            metadata=trade_metadata,
        )

        self.trades.append(trade)

        logger.info(
            f"Partial close {position.side} {position.symbol}: "
            f"{close_percent*100:.0f}% @ {execution_price:.2f}, "
            f"PnL={net_pnl:.2f} ({pnl_pct:.2f}%), remaining={position.quantity:.4f}"
        )

        # If position fully closed, remove it
        if position.quantity <= 0.0001:  # Small threshold for floating point
            del self.positions[position_id]
            logger.info(f"Position {position.symbol} fully closed")

        return trade

    def _check_hedge_groups(self, prices: Dict[str, float]) -> List[tuple]:
        """
        Check hedge groups for paired close conditions.

        Groups positions by hedge_group_id and checks:
        - If total unrealized loss exceeds max_loss_percent -> close ALL in group
        - If total unrealized profit exceeds profit_close_percent -> close ALL in group

        Returns:
            List of (position_id, exit_price, reason) tuples to close
        """
        positions_to_close = []

        # Group positions by hedge_group_id
        hedge_groups: Dict[str, List] = {}
        for pos_id, position in self.positions.items():
            group_id = position.metadata.get("hedge_group_id") if position.metadata else None
            if group_id:
                if group_id not in hedge_groups:
                    hedge_groups[group_id] = []
                hedge_groups[group_id].append((pos_id, position))

        if hedge_groups:
            logger.debug(
                f"Found {len(hedge_groups)} hedge groups with {sum(len(v) for v in hedge_groups.values())} positions"
            )

        # Check each hedge group
        for group_id, positions in hedge_groups.items():
            # Even with 1 position (other side hit TP), check loss threshold
            if len(positions) == 0:
                continue

            # Calculate total unrealized P&L for group
            total_notional = 0
            total_unrealized_pnl = 0

            for pos_id, position in positions:
                if position.symbol not in prices:
                    continue

                current_price = prices[position.symbol]
                cs = position.contract_size * position.quote_to_usd(current_price)
                notional = position.quantity * position.entry_price * cs

                # Calculate unrealized P&L
                if position.side == "LONG":
                    pnl = (current_price - position.entry_price) * position.quantity * cs
                else:  # SHORT
                    pnl = (position.entry_price - current_price) * position.quantity * cs

                total_notional += notional
                total_unrealized_pnl += pnl

            if total_notional == 0:
                continue

            # Calculate percentage P&L
            pnl_percent = (total_unrealized_pnl / total_notional) * 100

            # Get thresholds from first position's metadata
            first_pos = positions[0][1]
            max_loss_pct = first_pos.metadata.get("hedge_max_loss_pct", 2.0)
            profit_close_pct = first_pos.metadata.get("hedge_profit_close_pct", 0.3)

            # Check if we should close the group
            should_close = False
            close_reason = ""

            if pnl_percent <= -max_loss_pct:
                should_close = True
                close_reason = f"hedge_max_loss ({pnl_percent:.2f}%)"
                logger.info(
                    f"Hedge group {group_id}: closing due to max loss "
                    f"({pnl_percent:.2f}% <= -{max_loss_pct}%)"
                )
            elif pnl_percent >= profit_close_pct:
                should_close = True
                close_reason = f"hedge_profit_close ({pnl_percent:.2f}%)"
                logger.info(
                    f"Hedge group {group_id}: closing to lock profit "
                    f"({pnl_percent:.2f}% >= {profit_close_pct}%)"
                )

            if should_close:
                for pos_id, position in positions:
                    if position.symbol in prices:
                        positions_to_close.append((pos_id, prices[position.symbol], close_reason))

        return positions_to_close

    def _sizing_base(self, balance: float) -> float:
        """Apply the position-sizing balance cap.

        Mirrors BacktestEngine._calculate_position_size and, live-side,
        SignalLifecycleManager._apply_sizing_cap.
        """
        cap = self.balance_cap
        if cap <= 0:
            return balance
        return balance * (cap / 100.0) if cap < 100 else min(balance, cap)

    def _check_fan_batches(
        self,
        prices: Dict[str, float],
        tick_data_loader=None,
        candle_times: Optional[Dict[str, tuple]] = None,
    ) -> List[tuple]:
        """
        Check fan batches for batch close conditions.

        Uses trailing stop on batch PnL:
        - When total batch PnL > 0, activate trailing
        - Track peak total_pnl, close when it drops by callback from peak
        - Hard profit target and emergency stop still apply

        When tick_data_loader is available, replays ticks within the candle
        for more accurate close timing and exit price.

        Trailing state is stored in self._fan_trailing_state (class-level dict),
        NOT on position metadata — prevents state loss when positions are closed.

        Returns:
            List of (position_id, exit_price, reason) tuples to close
        """
        positions_to_close = []

        # Group positions by fan_batch_id
        fan_batches: Dict[str, List] = {}
        for pos_id, position in self.positions.items():
            batch_id = position.metadata.get("fan_batch_id") if position.metadata else None
            if batch_id:
                if batch_id not in fan_batches:
                    fan_batches[batch_id] = []
                fan_batches[batch_id].append((pos_id, position))

        # Clean up trailing state for batches that no longer have open positions
        stale_ids = [bid for bid in self._fan_trailing_state if bid not in fan_batches]
        for bid in stale_ids:
            del self._fan_trailing_state[bid]

        # Calculate account equity for percentage calculations
        equity = float(self.current_balance)
        for pos in self.positions.values():
            equity += pos.unrealized_pnl

        # Batch thresholds are measured against the SAME base that sized the
        # legs (see _calculate_position_size). Without the cap here, a dollar
        # cap plus compounding silently rescales the strategy: leg size stays
        # put while equity grows, so `pnl / equity` shrinks and the profit
        # target drifts out of reach (measured on a grid strategy in 2026: the
        # 1% target fired in 21% of exits early in the run and 0% by the end,
        # while stop_loss exits went 37% -> 65%).
        equity = self._sizing_base(equity)

        if equity <= 0:
            return positions_to_close

        # Check each fan batch
        for batch_id, positions in fan_batches.items():
            if len(positions) == 0:
                continue

            total_pnl = 0.0
            has_price = False

            for pos_id, position in positions:
                if position.symbol not in prices:
                    continue

                has_price = True
                current_price = prices[position.symbol]
                cs = position.contract_size * position.quote_to_usd(current_price)

                # Calculate unrealized PnL
                if position.side == "LONG":
                    pnl = (current_price - position.entry_price) * position.quantity * cs
                else:
                    pnl = (position.entry_price - current_price) * position.quantity * cs

                total_pnl += pnl

            if not has_price:
                continue

            total_pnl_pct = (total_pnl / equity) * 100

            # Get thresholds from first position's metadata
            first_pos = positions[0][1]
            profit_target_pct = first_pos.metadata.get("batch_profit_target_pct", 0.3)
            max_loss_pct = first_pos.metadata.get("batch_max_loss_pct", 5.0)
            trailing_callback_pct = first_pos.metadata.get("batch_trailing_callback_pct", 0.15)

            # --- Trailing state (stored on PortfolioManager, not on positions) ---
            if batch_id not in self._fan_trailing_state:
                self._fan_trailing_state[batch_id] = {
                    "trailing_active": False,
                    "peak_pnl": 0.0,
                }
            state = self._fan_trailing_state[batch_id]

            should_close = False
            close_reason = ""
            tick_exit_bid = None
            tick_exit_ask = None

            # --- Tick-level resolution FIRST (most accurate) ---
            # Replay ticks sampled every ~3s (matching live monitoring frequency)
            # to find the exact moment a close condition triggered.
            # Returns bid/ask for proper exit pricing (LONG@bid, SHORT@ask).
            if tick_data_loader is not None and candle_times:
                symbol = first_pos.symbol
                if symbol in candle_times:
                    c_start, c_end = candle_times[symbol]
                    ticks = tick_data_loader.get_ticks_for_candle(symbol, c_start, c_end)
                    if ticks is not None and len(ticks) > 0:
                        t_bid, t_ask, tick_reason = self._resolve_fan_batch_tick(
                            positions,
                            ticks,
                            equity,
                            state,
                            profit_target_pct,
                            max_loss_pct,
                            trailing_callback_pct,
                        )
                        if t_bid is not None:
                            should_close = True
                            close_reason = tick_reason
                            tick_exit_bid = t_bid
                            tick_exit_ask = t_ask
                            logger.info(
                                f"Fan batch {batch_id[:8]}: tick-level close — "
                                f"{close_reason}, bid={t_bid:.2f}, ask={t_ask:.2f}, "
                                f"{len(positions)} positions"
                            )

            # --- Candle-based fallback (when no tick data) ---
            if not should_close:
                # Emergency stop — always checked first
                if total_pnl_pct <= -max_loss_pct:
                    should_close = True
                    close_reason = f"fan_batch_close (max_loss, {total_pnl_pct:.2f}%)"
                    logger.info(
                        f"Fan batch {batch_id[:8]}: closing — max loss hit "
                        f"({total_pnl_pct:.2f}% <= -{max_loss_pct}%), "
                        f"{len(positions)} positions"
                    )
                # Hard profit target
                elif total_pnl_pct >= profit_target_pct:
                    should_close = True
                    close_reason = f"fan_batch_close (profit_target, {total_pnl_pct:.2f}%)"
                    logger.info(
                        f"Fan batch {batch_id[:8]}: closing — profit target hit "
                        f"({total_pnl_pct:.2f}% >= {profit_target_pct}%), "
                        f"{len(positions)} positions"
                    )
                # Trailing activation & tracking
                elif total_pnl > 0:
                    if not state["trailing_active"]:
                        state["trailing_active"] = True
                        state["peak_pnl"] = total_pnl
                        logger.info(
                            f"Fan batch {batch_id[:8]}: trailing activated, "
                            f"peak={total_pnl:.2f} ({total_pnl_pct:.2f}%), "
                            f"{len(positions)} positions"
                        )
                    else:
                        if total_pnl > state["peak_pnl"]:
                            state["peak_pnl"] = total_pnl

                        callback_amount = equity * trailing_callback_pct / 100
                        if total_pnl <= state["peak_pnl"] - callback_amount:
                            should_close = True
                            peak_pct = (state["peak_pnl"] / equity) * 100
                            close_reason = (
                                f"fan_batch_close (trailing, "
                                f"peak={peak_pct:.2f}%, now={total_pnl_pct:.2f}%)"
                            )
                            logger.info(
                                f"Fan batch {batch_id[:8]}: trailing close — "
                                f"peak={state['peak_pnl']:.2f} ({peak_pct:.2f}%), "
                                f"now={total_pnl:.2f} ({total_pnl_pct:.2f}%), "
                                f"callback={callback_amount:.2f}"
                            )

                # Trailing was active but PnL went negative → reset
                elif state["trailing_active"] and total_pnl <= 0:
                    state["trailing_active"] = False
                    state["peak_pnl"] = 0.0
                    logger.debug(
                        f"Fan batch {batch_id[:8]}: trailing reset — "
                        f"PnL back to zero, now={total_pnl:.2f}. Batch continues."
                    )

            if should_close:
                for pos_id, position in positions:
                    if position.symbol in prices:
                        if tick_exit_bid is not None:
                            # Tick-level: LONG exits at bid, SHORT exits at ask
                            exit_px = tick_exit_bid if position.side == "LONG" else tick_exit_ask
                        else:
                            exit_px = prices[position.symbol]
                        positions_to_close.append((pos_id, exit_px, close_reason))
                # Clean up trailing state for closed batch
                if batch_id in self._fan_trailing_state:
                    del self._fan_trailing_state[batch_id]

        return positions_to_close

    def check_fan_batches_tick_by_tick(
        self,
        prices: Dict[str, float],
        timestamp: datetime,
        tick_data_loader=None,
        candle_times: Optional[Dict[str, tuple]] = None,
    ) -> List:
        """
        Check fan batches with tick-level resolution and close immediately.

        Matches live behavior where position monitor runs every 1-5 seconds.
        Returns list of TradeResult for closed positions.
        """
        fan_closes = self._check_fan_batches(
            prices, tick_data_loader=tick_data_loader, candle_times=candle_times
        )

        closed_trades = []
        for pos_id, exit_px, reason in fan_closes:
            if pos_id in self.positions:
                trade = self.close_position(pos_id, exit_px, timestamp, exit_reason=reason)
                if trade:
                    closed_trades.append(trade)

        return closed_trades

    def _resolve_fan_batch_tick(
        self,
        positions: List[tuple],
        ticks,
        equity: float,
        trailing_state: dict,
        profit_target_pct: float,
        max_loss_pct: float,
        trailing_callback_pct: float,
    ) -> tuple:
        """
        Replay ticks to find exact price where fan batch close triggered.

        Simulates the batch PnL calculation on each tick to find the first
        tick where a close condition (profit target, trailing callback,
        max loss) is met.

        Returns:
            Tuple of (bid, ask, close_reason) or (None, None, None).
            Caller uses bid for LONG exits, ask for SHORT exits.
        """
        if equity <= 0:
            return None, None, None

        # Snapshot trailing state to simulate within ticks
        sim_trailing_active = trailing_state.get("trailing_active", False)
        sim_peak_pnl = trailing_state.get("peak_pnl", 0.0)

        # Sample ticks every ~10 seconds to approximate live monitoring
        # without catching intra-candle micro-noise.
        SAMPLE_INTERVAL_MS = 10000
        last_checked_ms = 0

        for row in ticks:
            tick_time_ms = int(row[0])

            # Skip ticks within the sampling interval
            if tick_time_ms - last_checked_ms < SAMPLE_INTERVAL_MS:
                continue
            last_checked_ms = tick_time_ms

            bid = float(row[1])
            ask = float(row[2])

            # Calculate batch PnL at this tick
            total_pnl = 0.0
            for _, position in positions:
                cs = position.contract_size * position.quote_to_usd(bid)
                # LONG exits at bid, SHORT exits at ask
                if position.side == "LONG":
                    pnl = (bid - position.entry_price) * position.quantity * cs
                else:
                    pnl = (position.entry_price - ask) * position.quantity * cs
                total_pnl += pnl

            total_pnl_pct = (total_pnl / equity) * 100

            # Tick-level: only check max_loss and profit_target (hard stops).
            # Trailing is left to candle-level fallback to avoid cutting
            # profits on intra-candle noise.
            if total_pnl_pct <= -max_loss_pct:
                reason = f"fan_batch_close (max_loss, {total_pnl_pct:.2f}%)"
                return bid, ask, reason

            if total_pnl_pct >= profit_target_pct:
                reason = f"fan_batch_close (profit_target, {total_pnl_pct:.2f}%)"
                return bid, ask, reason

            # Track trailing state across ticks (for accuracy) but don't close
            if total_pnl > 0:
                if not sim_trailing_active:
                    sim_trailing_active = True
                    sim_peak_pnl = total_pnl
                else:
                    if total_pnl > sim_peak_pnl:
                        sim_peak_pnl = total_pnl
            elif sim_trailing_active and total_pnl <= 0:
                sim_trailing_active = False
                sim_peak_pnl = 0.0

        # No trigger found in ticks — update trailing state from simulation
        trailing_state["trailing_active"] = sim_trailing_active
        trailing_state["peak_pnl"] = sim_peak_pnl
        return None, None, None

    def _apply_hedge_dca(self, prices: Dict[str, float], timestamp: datetime) -> List[str]:
        """
        Apply DCA (Dollar Cost Averaging) to positions at loss.

        Supports two modes:
        1. Hedge DCA: hedge_dca_enabled=True - for hedge positions (no SL)
        2. Pure DCA: dca_enabled=True - for single-direction positions with SL

        When a position reaches the DCA threshold loss,
        doubles the position at current price to improve average entry.

        Returns:
            List of position IDs that were DCA'd
        """
        dca_applied = []

        for pos_id, position in list(self.positions.items()):
            if position.symbol not in prices:
                continue

            metadata = position.metadata or {}

            # Check if DCA is enabled (hedge DCA or pure DCA)
            hedge_dca = metadata.get("hedge_dca_enabled", False)
            pure_dca = metadata.get("dca_enabled", False)

            if not hedge_dca and not pure_dca:
                continue

            current_price = prices[position.symbol]

            # Get DCA parameters based on mode
            if hedge_dca:
                dca_threshold = metadata.get("hedge_dca_threshold", 0.5)
                max_dca_levels = metadata.get("hedge_max_dca_levels", 1)
                dca_add_size_pct = metadata.get("hedge_dca_size_pct", 0.5)
                tp_percent_list = [metadata.get("hedge_tp_percent", 0.8)]
            else:  # pure_dca
                dca_threshold = metadata.get("dca_threshold", 0.5)
                max_dca_levels = metadata.get("dca_max_levels", 1)
                dca_add_size_pct = metadata.get("dca_add_size_pct", 0.5)
                tp_percent_list = metadata.get("tp_percent", [0.8, 1.2])

            current_dca_level = metadata.get("dca_level", 0)

            # Check if already at max DCA level
            if current_dca_level >= max_dca_levels:
                continue

            # Calculate current loss percentage
            if position.side == "LONG":
                loss_pct = (position.entry_price - current_price) / position.entry_price * 100
            else:  # SHORT
                loss_pct = (current_price - position.entry_price) / position.entry_price * 100

            # Check if at DCA threshold
            # Calculate required loss for next DCA level
            next_dca_level = current_dca_level + 1
            required_loss_pct = dca_threshold * next_dca_level

            if loss_pct < required_loss_pct:
                # Not at next DCA level yet, but track extreme for retracement mode
                dca_require_retracement = metadata.get("dca_require_retracement", False)
                if dca_require_retracement:
                    # Update extreme price tracking
                    extreme_price = metadata.get("dca_extreme_price")
                    if position.side == "LONG":
                        if extreme_price is None or current_price < extreme_price:
                            position.metadata["dca_extreme_price"] = current_price
                    else:  # SHORT
                        if extreme_price is None or current_price > extreme_price:
                            position.metadata["dca_extreme_price"] = current_price
                continue

            # Check for retracement-based DCA
            dca_require_retracement = metadata.get("dca_require_retracement", False)
            if dca_require_retracement:
                extreme_price = metadata.get("dca_extreme_price")
                touched_levels = metadata.get("dca_touched_levels", set())
                if isinstance(touched_levels, list):
                    touched_levels = set(touched_levels)

                # First time at this level - mark as touched and set extreme
                if extreme_price is None:
                    # First time tracking - set extreme and mark level
                    position.metadata["dca_extreme_price"] = current_price
                    touched_levels.add(next_dca_level)
                    position.metadata["dca_touched_levels"] = list(touched_levels)
                    logger.debug(
                        f"DCA level {next_dca_level} touched for {position.symbol}, waiting for retracement"
                    )
                    continue

                # Calculate how deep price has gone
                if position.side == "LONG":
                    extreme_loss_pct = (
                        (position.entry_price - extreme_price) / position.entry_price * 100
                    )
                else:  # SHORT
                    extreme_loss_pct = (
                        (extreme_price - position.entry_price) / position.entry_price * 100
                    )

                # Mark which levels have been touched (price went past them)
                for level in range(1, max_dca_levels + 1):
                    level_threshold = dca_threshold * level
                    if extreme_loss_pct >= level_threshold:
                        touched_levels.add(level)
                position.metadata["dca_touched_levels"] = list(touched_levels)

                # Check if next level was previously touched
                if next_dca_level not in touched_levels:
                    # Level just reached for first time - mark and update extreme
                    touched_levels.add(next_dca_level)
                    position.metadata["dca_touched_levels"] = list(touched_levels)
                    # Update extreme price (price went further against us)
                    if position.side == "LONG":
                        if current_price < extreme_price:
                            position.metadata["dca_extreme_price"] = current_price
                    else:
                        if current_price > extreme_price:
                            position.metadata["dca_extreme_price"] = current_price
                    logger.debug(
                        f"DCA level {next_dca_level} touched for {position.symbol}, waiting for retracement"
                    )
                    continue

                # Level was touched before - check if price has returned (retracement)
                if position.side == "LONG":
                    # For LONG: price should be higher than extreme (bounced back)
                    if current_price <= extreme_price:
                        # Still at or below extreme - update extreme and wait
                        position.metadata["dca_extreme_price"] = current_price
                        continue
                else:  # SHORT
                    # For SHORT: price should be lower than extreme (bounced back)
                    if current_price >= extreme_price:
                        # Still at or above extreme - update extreme and wait
                        position.metadata["dca_extreme_price"] = current_price
                        continue

                # Retracement confirmed! Price moved back toward entry
                logger.info(
                    f"DCA retracement confirmed for {position.symbol}: "
                    f"extreme={extreme_price:.4f}, current={current_price:.4f}, "
                    f"level={next_dca_level}"
                )

            # DCA trend filter: don't DCA if price is moving too fast against position
            # This indicates a strong trend that DCA won't help
            dca_use_trend_filter = metadata.get("dca_use_trend_filter", False)
            if dca_use_trend_filter:
                # If loss is more than 2x the DCA threshold, likely a strong trend
                trend_threshold_mult = 2.0
                if loss_pct > dca_threshold * trend_threshold_mult:
                    logger.debug(
                        f"DCA skipped for {position.symbol}: strong trend detected "
                        f"(loss {loss_pct:.2f}% > {dca_threshold * trend_threshold_mult:.2f}%)"
                    )
                    continue

            # DCA RSI filter: only DCA if entry RSI was very extreme
            dca_rsi_filter = metadata.get("dca_rsi_filter", False)
            if dca_rsi_filter:
                entry_rsi = metadata.get("entry_rsi", 50)
                rsi_threshold = metadata.get("dca_rsi_extreme_threshold", 25.0)
                position_direction = metadata.get("position_direction", "")

                # For LONG: RSI should be < threshold (very oversold)
                # For SHORT: RSI should be > (100 - threshold) (very overbought)
                if position_direction == "LONG" and entry_rsi > rsi_threshold:
                    logger.debug(
                        f"DCA skipped for {position.symbol} LONG: entry RSI {entry_rsi:.1f} > {rsi_threshold}"
                    )
                    continue
                elif position_direction == "SHORT" and entry_rsi < (100 - rsi_threshold):
                    logger.debug(
                        f"DCA skipped for {position.symbol} SHORT: entry RSI {entry_rsi:.1f} < {100 - rsi_threshold}"
                    )
                    continue

            # Apply DCA: add more to position at current price
            # Calculate additional quantity based on dca_add_size_pct or initial_quantity
            if dca_add_size_pct > 0:
                # dca_add_size_pct = 0.5 means add 50% of initial
                additional_qty = position.initial_quantity * dca_add_size_pct
            else:
                additional_qty = position.initial_quantity  # Add same amount as original

            # Check margin before DCA
            additional_notional = (
                additional_qty
                * current_price
                * position.contract_size
                * position.quote_to_usd(current_price)
            )
            additional_margin = additional_notional / self.leverage
            additional_margin_decimal = Decimal(str(additional_margin))
            if additional_margin_decimal > self.current_balance:
                logger.debug(
                    f"DCA skipped for {position.symbol}: insufficient margin "
                    f"(need ${additional_margin:.2f}, have ${float(self.current_balance):.2f})"
                )
                continue

            # Deduct margin for DCA
            self.current_balance -= additional_margin_decimal
            self.total_margin_used += additional_margin
            position.margin_used += additional_margin

            # Calculate new average entry price
            old_notional = position.quantity * position.entry_price
            new_notional = additional_qty * current_price
            total_qty = position.quantity + additional_qty
            new_avg_entry = (old_notional + new_notional) / total_qty

            logger.info(
                f"DCA applied to {position.symbol} {position.side}: "
                f"level {current_dca_level} -> {current_dca_level + 1}, "
                f"qty {position.quantity:.4f} -> {total_qty:.4f}, "
                f"entry {position.entry_price:.4f} -> {new_avg_entry:.4f}"
            )

            # Update position
            position.quantity = total_qty
            position.initial_quantity = total_qty  # Update initial for partial close calcs
            position.entry_price = new_avg_entry

            # Update take profits based on new entry
            if position.side == "LONG":
                new_tps = [new_avg_entry * (1 + tp / 100) for tp in tp_percent_list]
            else:
                new_tps = [new_avg_entry * (1 - tp / 100) for tp in tp_percent_list]

            position.take_profits = new_tps
            position.tp_hit = [False] * len(new_tps)

            # Update SL for pure DCA if recalc enabled
            if pure_dca and metadata.get("dca_recalc_sl_tp", True):
                # Recalculate SL based on new average entry
                original_sl = metadata.get("original_sl")
                if original_sl and position.stop_loss:
                    # Keep same % distance from new entry, with tightening factor
                    sl_tighten = metadata.get("dca_sl_tighten_factor", 0.8)
                    if position.side == "LONG":
                        sl_pct = (position.entry_price - original_sl) / position.entry_price
                        new_sl = new_avg_entry * (1 - sl_pct * sl_tighten)
                    else:
                        sl_pct = (original_sl - position.entry_price) / position.entry_price
                        new_sl = new_avg_entry * (1 + sl_pct * sl_tighten)
                    position.stop_loss = new_sl
                    position.original_stop_loss = new_sl

            # Update DCA level in metadata
            position.metadata["dca_level"] = current_dca_level + 1

            dca_applied.append(pos_id)

        return dca_applied

    def update_positions(self, prices: Dict[str, float], timestamp: datetime):
        """
        Update all positions with current prices.

        Args:
            prices: Dict of symbol -> current price
            timestamp: Current timestamp
        """
        for position in self.positions.values():
            if position.symbol in prices:
                position.update_price(prices[position.symbol])

    def check_risk_levels(
        self,
        prices: Dict[str, float],
        timestamp: datetime,
        highs: Optional[Dict[str, float]] = None,
        lows: Optional[Dict[str, float]] = None,
        tick_resolver=None,
        candle_times: Optional[Dict[str, tuple]] = None,
        tick_data_loader=None,
        skip_fan_batches: bool = False,
        bar_minutes: float = 0.0,
        short_trigger_spread: float = 0.0,
    ) -> List[TradeResult]:
        """
        Check stop loss and take profit levels for all positions.

        Handles:
        - Multiple take profit levels with partial exits
        - Breakeven activation after TP1
        - Trailing stop activation after TP2
        - Accurate SL check using high/low prices

        Args:
            prices: Dict of symbol -> current close price
            timestamp: Current timestamp
            highs: Dict of symbol -> candle high (for SHORT SL check)
            lows: Dict of symbol -> candle low (for LONG SL check)
            bar_minutes: Candle duration; prices are the candle close, so the
                current moment is timestamp + bar_minutes (used by time exit)

        Returns:
            List of closed trades
        """
        closed_trades = []
        positions_to_close = []
        partial_closes = []
        time_exits_due = []

        # Check hedge groups first - close pairs if thresholds exceeded
        hedge_closes = self._check_hedge_groups(prices)
        positions_to_close.extend(hedge_closes)

        fan_closes = []
        if not skip_fan_batches:
            fan_closes = self._check_fan_batches(
                prices, tick_data_loader=tick_data_loader, candle_times=candle_times
            )
            positions_to_close.extend(fan_closes)

        # Apply DCA to hedge positions at loss (before closing checks)
        self._apply_hedge_dca(prices, timestamp)

        # Track position IDs already marked for batch close to skip individual checks
        batch_close_ids = {pos_id for pos_id, _, _ in hedge_closes} | {
            pos_id for pos_id, _, _ in fan_closes
        }

        for position_id, position in list(self.positions.items()):
            # Skip if already marked for batch close (hedge or fan)
            if position_id in batch_close_ids:
                continue
            if position.symbol not in prices:
                continue

            current_price = prices[position.symbol]

            # === Time exit: close after N minutes by market ===
            # Opt-in via signal metadata 'time_exit_minutes' (NOT max_hold_minutes,
            # which several strategies already carry as informational only).
            # timestamp = candle OPEN label, but prices are the candle CLOSE ->
            # age is measured at candle close (timestamp + bar_minutes), otherwise
            # the exit lands one bar late. SL/TP of this candle are checked first
            # (the broker holds them live); the time exit is applied after the loop.
            time_exit = (position.metadata or {}).get("time_exit_minutes")
            if time_exit:
                age_min = (timestamp - position.entry_time).total_seconds() / 60 + bar_minutes
                if age_min >= float(time_exit) - 1e-9:
                    time_exits_due.append((position_id, current_price))

            # Get high/low for accurate SL check
            candle_high = highs.get(position.symbol, current_price) if highs else current_price
            candle_low = lows.get(position.symbol, current_price) if lows else current_price

            # A SHORT closes at the ASK: at the broker a short's stop triggers when the
            # ask (= bid + spread) reaches SL, and its take-profit when the ask reaches TP.
            # CSV candles are built from BID, so without this shift shorts missed stops
            # and collected take-profits by the width of the spread. Reconciliation
            # against the broker: one short had ask >= SL while bid never touched it —
            # broker -$57, backtest +$1.73; over ~1,000 shorts, 9 missed stops and 4 false TPs.
            # Shifted before is_ambiguous: the tick resolver (already on ask) must be
            # triggered by the candle's ask range. 0 = previous behaviour.
            if position.side == "SHORT" and short_trigger_spread > 0:
                candle_high += short_trigger_spread
                candle_low += short_trigger_spread

            # === STEP 1: Check SL/TP using PRE-EXISTING levels (set from previous candles) ===
            # This prevents the intra-candle bias where trailing ratchets up on close
            # and then the same candle's low triggers the tighter SL.

            # --- Tick-level resolution for ambiguous candles ---
            # When both SL and TP fall within the same candle's range,
            # replay actual ticks to determine which was hit first.
            if (
                tick_resolver is not None
                and candle_times is not None
                and position.symbol in candle_times
                and tick_resolver.is_ambiguous(position, candle_high, candle_low)
            ):
                c_start, c_end = candle_times[position.symbol]
                resolutions = tick_resolver.resolve(position.symbol, c_start, c_end, position)
                if resolutions:
                    for res in resolutions:
                        if res.action in ("stop_loss", "breakeven"):
                            positions_to_close.append((position_id, res.price, res.action))
                        elif res.tp_index is not None:
                            # Mark TP as hit
                            if res.tp_index < len(position.tp_hit):
                                position.tp_hit[res.tp_index] = True

                            # Calculate close percentage
                            if res.tp_index < len(position.tp_distribution):
                                close_pct = position.tp_distribution[res.tp_index]
                            else:
                                remaining_tps = len(position.take_profits) - res.tp_index
                                close_pct = 1.0 / remaining_tps if remaining_tps > 0 else 1.0

                            remaining_pct = position.quantity / position.initial_quantity
                            adjusted_close_pct = min(close_pct / remaining_pct, 1.0)

                            partial_closes.append(
                                (
                                    position_id,
                                    adjusted_close_pct,
                                    res.price,
                                    f"take_profit_{res.tp_index + 1}",
                                )
                            )

                            # Post-TP state transitions (BE/trailing)
                            if res.tp_index == 0 and not position.breakeven_activated:
                                if position.original_stop_loss is not None:
                                    metadata = position.metadata or {}
                                    use_be = metadata.get("use_breakeven", True)
                                    if use_be and not metadata.get("use_trailing_stop", False):
                                        position.activate_breakeven(lock_profit_ratio=0.5)
                                    elif use_be and metadata.get("use_trailing_stop", False):
                                        trail_pct = (
                                            metadata.get("trailing_stop_distance", 0.4) / 100
                                        )
                                        trail_dist = position.entry_price * trail_pct
                                        position.activate_trailing(trail_dist, start_from_tp1=True)
                            elif res.tp_index == 1 and not position.trailing_activated:
                                if position.original_stop_loss is not None:
                                    trail_dist = position.entry_price * 0.003
                                    position.activate_trailing(trail_dist, start_from_tp1=True)

                    # Skip candle-based checks — tick resolver handled this position
                    # Still do STEP 2 (update_price, BE upgrades) below
                    # Jump past the candle-based SL/TP block
                    position.update_price(current_price)
                    position.check_upgrade_breakeven(current_price)
                    continue
                # Empty resolutions = no tick data, fall through to candle-based

            # Check stop loss using high/low for accuracy
            # LONG: SL triggers if low <= stop_loss
            # SHORT: SL triggers if high >= stop_loss
            sl_check_price = candle_low if position.side == "LONG" else candle_high

            if position.check_stop_loss(sl_check_price):
                # Determine if this is a breakeven exit or real stop loss
                exit_reason = "breakeven" if position.is_stop_loss_breakeven() else "stop_loss"
                # Exit at SL price for accurate P&L, but validate SL is on correct side
                # (prevents invalid SL above entry for LONG acting as free take-profit)
                sl = position.stop_loss
                if sl and (
                    (position.side == "LONG" and sl <= position.entry_price)
                    or (position.side == "SHORT" and sl >= position.entry_price)
                    or position.breakeven_activated  # BE SL above entry is intentional
                ):
                    exit_price = sl
                else:
                    exit_price = sl_check_price
                logger.debug(
                    f"SL triggered for {position.symbol} {position.side}: "
                    f"check_price={sl_check_price:.4f}, SL={position.stop_loss}, "
                    f"original_SL={position.original_stop_loss}, BE={position.breakeven_activated}"
                )
                positions_to_close.append((position_id, exit_price, exit_reason))
                continue

            # Check multiple take profits using high/low for accuracy
            # LONG: TP triggers if high >= take_profit
            # SHORT: TP triggers if low <= take_profit
            tp_check_price = candle_high if position.side == "LONG" else candle_low

            if position.take_profits:
                tp_index = position.check_take_profits(tp_check_price)

                if tp_index is not None:
                    # Mark TP as hit
                    position.tp_hit[tp_index] = True

                    # Get distribution for this TP
                    if tp_index < len(position.tp_distribution):
                        close_pct = position.tp_distribution[tp_index]
                    else:
                        close_pct = 1.0 / (len(position.take_profits) - tp_index)

                    # Adjust for remaining position
                    # If 60% closed at TP1, TP2 should close 30/40 = 75% of remaining
                    remaining_pct = position.quantity / position.initial_quantity
                    adjusted_close_pct = min(close_pct / remaining_pct, 1.0)

                    # Exit at TP price (not candle close) for accurate P&L
                    tp_price = position.take_profits[tp_index]

                    partial_closes.append(
                        (position_id, adjusted_close_pct, tp_price, f"take_profit_{tp_index + 1}")
                    )

                    # After TP1: activate breakeven (trailing is activated separately by profit %)
                    # Only activate BE if position has a stop loss (not for hedge positions)
                    if tp_index == 0 and not position.breakeven_activated:
                        if position.original_stop_loss is not None:
                            metadata = position.metadata or {}
                            # Check if BE is enabled (default True for backwards compatibility)
                            use_breakeven = metadata.get("use_breakeven", True)
                            # Check if using profit-based BE activation
                            be_activation_pct = metadata.get("breakeven_activation_percent", 0)

                            if not use_breakeven:
                                logger.debug(
                                    f"BE disabled for {position.symbol} - keeping original SL"
                                )
                            elif be_activation_pct > 0:
                                # BE will be activated by profit % check, not at TP1
                                logger.debug(
                                    f"BE for {position.symbol} waits for {be_activation_pct}% profit"
                                )
                            # If using trailing_activation_percent, just do breakeven here
                            elif metadata.get("trailing_activation_percent", 0) > 0:
                                position.activate_breakeven(lock_profit_ratio=0.5)
                                logger.debug(
                                    f"BE activated for {position.symbol} after TP1 (trailing waits for {metadata.get('trailing_activation_percent')}%)"
                                )
                            # Legacy: activate trailing immediately after TP1 if no activation_percent
                            elif metadata.get("use_trailing_stop", False):
                                trail_pct = metadata.get("trailing_stop_distance", 0.4) / 100
                                trail_distance = position.entry_price * trail_pct
                                position.activate_trailing(trail_distance, start_from_tp1=True)
                                logger.debug(
                                    f"Trailing activated for {position.symbol} after TP1: {trail_pct*100:.1f}%"
                                )
                            else:
                                position.activate_breakeven(lock_profit_ratio=0.5)
                        else:
                            # Hedge mode: don't close pair on TP - let each side hit TP independently
                            # This allows both sides to profit if price oscillates
                            logger.debug(
                                f"Skipping BE for {position.symbol} - no original SL (hedge mode)"
                            )

                    # After TP2: activate trailing from TP1 level (if not already trailing)
                    # Only activate trailing if position has a stop loss (not for hedge positions)
                    if (
                        tp_index == 1
                        and not position.trailing_activated
                        and position.original_stop_loss is not None
                    ):
                        # Trail at 0.3% of entry price (matches production)
                        trail_distance = position.entry_price * 0.003
                        position.activate_trailing(trail_distance, start_from_tp1=True)

            # Legacy: check single take profit
            elif position.check_take_profit(tp_check_price):
                tp_price = position.take_profit if position.take_profit else current_price
                positions_to_close.append((position_id, tp_price, "take_profit"))

            # === STEP 2: Update position for NEXT candle ===
            # Trailing ratchet, BE upgrades, and profit-based activations apply from next candle.

            # Tick-level trailing update: replay ticks to ratchet trailing SL
            # on each tick, matching live behavior (SL sync every ~1 sec).
            if (
                tick_data_loader is not None
                and candle_times
                and position.trailing_activated
                and position.trailing_stop_distance > 0
                and position.symbol in candle_times
            ):
                c_start, c_end = candle_times[position.symbol]
                ticks = tick_data_loader.get_ticks_for_candle(position.symbol, c_start, c_end)
                if ticks is not None and len(ticks) > 0:
                    for row in ticks:
                        bid = float(row[1])
                        ask = float(row[2])
                        tick_price = bid if position.side == "LONG" else ask
                        position.update_price(tick_price)

            # Update position price with candle close (fallback / final state)
            position.update_price(current_price)

            # Check if we should upgrade BE level (at 3% profit -> SL = 2%)
            position.check_upgrade_breakeven(current_price)

            # Check if we should activate BE based on profit percentage
            # (separate from TP-triggered BE)
            if not position.breakeven_activated and position.original_stop_loss is not None:
                metadata = position.metadata or {}
                be_activation_pct = metadata.get("breakeven_activation_percent", 0)
                if be_activation_pct > 0 and metadata.get("use_breakeven", True):
                    # Calculate current profit percentage
                    if position.side == "LONG":
                        profit_pct = (
                            (current_price - position.entry_price) / position.entry_price
                        ) * 100
                    else:
                        profit_pct = (
                            (position.entry_price - current_price) / position.entry_price
                        ) * 100

                    # Activate BE when profit reaches breakeven_activation_percent
                    if profit_pct >= be_activation_pct:
                        lock_ratio = metadata.get("breakeven_lock_profit_ratio", 0.5)
                        position.activate_breakeven(lock_profit_ratio=lock_ratio)
                        logger.debug(
                            f"BE activated for {position.symbol} at {profit_pct:.1f}% profit "
                            f"(threshold={be_activation_pct}%)"
                        )

            # Check if we should activate trailing based on profit percentage
            # (separate from TP-triggered trailing)
            if not position.trailing_activated and position.original_stop_loss is not None:
                metadata = position.metadata or {}
                activation_pct = metadata.get("trailing_activation_percent", 0)
                if activation_pct > 0 and metadata.get("use_trailing_stop", False):
                    # Calculate current profit percentage
                    if position.side == "LONG":
                        profit_pct = (
                            (current_price - position.entry_price) / position.entry_price
                        ) * 100
                    else:
                        profit_pct = (
                            (position.entry_price - current_price) / position.entry_price
                        ) * 100

                    # Activate trailing when profit reaches activation_percent
                    if profit_pct >= activation_pct:
                        callback_pct = metadata.get("trailing_callback_percent", 0.5) / 100
                        trail_distance = position.entry_price * callback_pct
                        position.activate_trailing(trail_distance, start_from_tp1=False)
                        # Set SL to lock in some profit (activation - callback)
                        locked_profit_pct = (
                            activation_pct - metadata.get("trailing_callback_percent", 0.5)
                        ) / 100
                        if position.side == "LONG":
                            position.stop_loss = position.entry_price * (1 + locked_profit_pct)
                        else:
                            position.stop_loss = position.entry_price * (1 - locked_profit_pct)
                        logger.debug(
                            f"Trailing activated for {position.symbol} at {profit_pct:.1f}% profit "
                            f"(activation={activation_pct}%, callback={callback_pct*100:.1f}%)"
                        )

        # Time exits: only positions that SL/TP of this candle did not close.
        # A remainder after a partial TP is closed below by the positions_to_close pass.
        already_closing = {pos_id for pos_id, _, _ in positions_to_close}
        for pos_id, price in time_exits_due:
            if pos_id not in already_closing:
                positions_to_close.append((pos_id, price, "time_exit"))

        # Process partial closes (TP = limit order = maker fee)
        for pos_id, close_pct, price, reason in partial_closes:
            if pos_id in self.positions:  # Position might be closed already
                trade = self.partial_close_position(
                    pos_id, close_pct, price, timestamp, exit_reason=reason, fee_type="maker"
                )
                if trade:
                    closed_trades.append(trade)

        # Close full positions (SL = stop-market = taker, final TP = limit = maker)
        for pos_id, price, reason in positions_to_close:
            if pos_id in self.positions:  # Position might be closed already
                is_tp = "take_profit" in reason
                trade = self.close_position(
                    pos_id,
                    price,
                    timestamp,
                    exit_reason=reason,
                    fee_type="maker" if is_tp else "taker",
                )
                if trade:
                    closed_trades.append(trade)

        return closed_trades

    def update_equity(self, timestamp: datetime, prices: Dict[str, float]):
        """
        Update equity curve with current portfolio value.

        Args:
            timestamp: Current timestamp
            prices: Dict of symbol -> current price
        """
        # Calculate unrealized P&L
        unrealized_pnl = Decimal("0")
        for position in self.positions.values():
            if position.symbol in prices:
                position.update_price(prices[position.symbol])
                unrealized_pnl += Decimal(str(position.unrealized_pnl))

        # open_position() moves margin out of current_balance, but that margin is
        # still the account's money: equity = free balance + locked margin +
        # unrealized PnL. Without the margin term every open position showed up
        # as a drawdown the size of its margin.
        locked_margin = sum(
            (Decimal(str(p.margin_used)) for p in self.positions.values()), Decimal("0")
        )
        total_equity = self.current_balance + locked_margin + unrealized_pnl

        # Track drawdown
        if float(total_equity) > self.peak_equity:
            self.peak_equity = float(total_equity)

        drawdown = self.peak_equity - float(total_equity)
        drawdown_pct = (drawdown / self.peak_equity) * 100 if self.peak_equity > 0 else 0

        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
            self.max_drawdown_pct = drawdown_pct

        # Record equity point
        equity_point = EquityPoint(
            timestamp=timestamp,
            balance=self.current_balance,
            unrealized_pnl=unrealized_pnl,
            total_equity=total_equity,
            positions_count=len(self.positions),
            drawdown=drawdown,
            drawdown_pct=drawdown_pct,
        )

        self.equity_curve.append(equity_point)

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get portfolio statistics.

        Returns:
            Dictionary with portfolio statistics
        """
        winning_trades = [t for t in self.trades if t.net_pnl > 0]
        losing_trades = [t for t in self.trades if t.net_pnl <= 0]

        total_trades = len(self.trades)
        win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0

        avg_win = (
            sum(t.net_pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
        )
        avg_loss = (
            sum(t.net_pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0
        )

        gross_profit = sum(t.net_pnl for t in winning_trades)
        gross_loss = abs(sum(t.net_pnl for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "initial_balance": float(self.initial_balance),
            "current_balance": float(self.current_balance),
            "total_trades": total_trades,
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "total_commission": self.total_commission,
            "total_slippage": self.total_slippage,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "open_positions": len(self.positions),
        }

    def reset(self):
        """Reset portfolio to initial state."""
        self.current_balance = self.initial_balance
        self.positions.clear()
        self.trades.clear()
        self.equity_curve.clear()
        self.total_commission = 0.0
        self.total_slippage = 0.0
        self.peak_equity = float(self.initial_balance)
        self.max_drawdown = 0.0
        self.max_drawdown_pct = 0.0
