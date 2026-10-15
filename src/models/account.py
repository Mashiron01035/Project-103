"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : account
    Project     : Project-103
    Description : Account model for account state and balance. This module provides account data models for tracking account information.

    SPDX-License-Identifier: AGPL-3.0

"""

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.models.base import BaseModel, SerializableMixin, Timestamped, TimestampMixin

if TYPE_CHECKING:
    from src.models.trade import Trade


@dataclass
class AccountBalance:
    """Account balance for a specific asset."""

    asset: str  # Asset symbol (e.g., USDT, BTC)
    free: float = 0.0  # Available balance
    locked: float = 0.0  # Locked in orders
    total: float = 0.0  # Total balance

    @property
    def available(self) -> float:
        """Get available balance."""
        return self.free

    @property
    def used(self) -> float:
        """Get used balance."""
        return self.locked

    def update(self, free: float, locked: float) -> None:
        """Update balance values."""
        self.free = free
        self.locked = locked
        self.total = free + locked

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary."""
        return {"asset": self.asset, "free": self.free, "locked": self.locked, "total": self.total}


@dataclass
class AccountMargin:
    """Account margin information."""

    # Margin levels
    initial_margin: float = 0.0  # Initial margin required
    maintenance_margin: float = 0.0  # Maintenance margin required
    margin_balance: float = 0.0  # Current margin balance
    available_margin: float = 0.0  # Available for new positions

    # Margin ratios
    margin_level: float = 0.0  # Margin level percentage
    margin_ratio: float = 0.0  # Used/Total ratio

    # Leverage
    account_leverage: float = 1.0  # Account-wide leverage
    max_leverage: float = 1.0  # Maximum allowed leverage

    # Risk
    liquidation_price: Optional[float] = None  # Estimated liquidation

    def calculate_margin_level(self) -> float:
        """Calculate margin level."""
        if self.maintenance_margin > 0:
            self.margin_level = (self.margin_balance / self.maintenance_margin) * 100
        else:
            self.margin_level = float("inf")
        return self.margin_level

    def is_margin_call(self, threshold: float = 100) -> bool:
        """Check if margin call is triggered."""
        return self.margin_level < threshold

    def is_stop_out(self, threshold: float = 50) -> bool:
        """Check if stop out level reached."""
        return self.margin_level < threshold

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "initial_margin": self.initial_margin,
            "maintenance_margin": self.maintenance_margin,
            "margin_balance": self.margin_balance,
            "available_margin": self.available_margin,
            "margin_level": self.margin_level,
            "margin_ratio": self.margin_ratio,
            "account_leverage": self.account_leverage,
            "max_leverage": self.max_leverage,
            "liquidation_price": self.liquidation_price,
        }


@dataclass
class AccountRisk:
    """Account risk metrics."""

    # Exposure
    total_exposure: float = 0.0  # Total position value
    max_exposure: float = 0.0  # Maximum allowed exposure
    exposure_ratio: float = 0.0  # Current/Max ratio

    # Position limits
    open_positions: int = 0
    max_positions: int = 0

    # Order limits
    open_orders: int = 0
    max_orders: int = 0

    # Risk metrics
    value_at_risk: float = 0.0  # VaR
    expected_shortfall: float = 0.0  # CVaR
    max_drawdown: float = 0.0  # Maximum drawdown
    current_drawdown: float = 0.0  # Current drawdown

    # Risk scores
    risk_score: float = 0.0  # Overall risk score (0-1)
    leverage_risk: float = 0.0  # Leverage risk score
    concentration_risk: float = 0.0  # Position concentration risk

    def calculate_risk_score(self) -> float:
        """Calculate overall risk score."""
        scores = []

        # Exposure risk
        if self.max_exposure > 0:
            exposure_score = min(1.0, self.total_exposure / self.max_exposure)
            scores.append(exposure_score)

        # Position limit risk
        if self.max_positions > 0:
            position_score = self.open_positions / self.max_positions
            scores.append(position_score)

        # Drawdown risk
        if self.max_drawdown != 0:
            drawdown_score = abs(self.current_drawdown / self.max_drawdown)
            scores.append(drawdown_score)

        # Average all risk scores
        if scores:
            self.risk_score = sum(scores) / len(scores)

        return self.risk_score

    def get_risk_level(self) -> str:
        """Get risk level classification."""
        if self.risk_score < 0.3:
            return "LOW"
        elif self.risk_score < 0.6:
            return "MEDIUM"
        elif self.risk_score < 0.8:
            return "HIGH"
        else:
            return "CRITICAL"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_exposure": self.total_exposure,
            "max_exposure": self.max_exposure,
            "exposure_ratio": self.exposure_ratio,
            "open_positions": self.open_positions,
            "max_positions": self.max_positions,
            "open_orders": self.open_orders,
            "max_orders": self.max_orders,
            "value_at_risk": self.value_at_risk,
            "expected_shortfall": self.expected_shortfall,
            "max_drawdown": self.max_drawdown,
            "current_drawdown": self.current_drawdown,
            "risk_score": self.risk_score,
            "leverage_risk": self.leverage_risk,
            "concentration_risk": self.concentration_risk,
        }


@dataclass
class AccountPerformance:
    """Account performance metrics."""

    # Returns
    total_return: float = 0.0  # Total return percentage
    daily_return: float = 0.0  # Today's return
    weekly_return: float = 0.0  # This week's return
    monthly_return: float = 0.0  # This month's return
    yearly_return: float = 0.0  # This year's return

    # P&L
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_pnl: float = 0.0

    # Trading metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    # Risk-adjusted returns
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Other metrics
    profit_factor: float = 0.0
    expectancy: float = 0.0
    recovery_factor: float = 0.0

    def update_metrics(
        self, trades: List["Trade"], initial_balance: float, current_balance: float
    ) -> None:
        """
        Update performance metrics.

        Args:
            trades: List of trades
            initial_balance: Initial account balance
            current_balance: Current account balance
        """
        if initial_balance > 0:
            self.total_return = ((current_balance - initial_balance) / initial_balance) * 100

        if trades:
            self.total_trades = len(trades)
            self.winning_trades = sum(1 for t in trades if t.is_profitable)
            self.losing_trades = self.total_trades - self.winning_trades

            if self.total_trades > 0:
                self.win_rate = self.winning_trades / self.total_trades

            # Calculate profit factor
            gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
            gross_loss = sum(abs(t.pnl) for t in trades if t.pnl < 0)

            if gross_loss > 0:
                self.profit_factor = gross_profit / gross_loss
            elif gross_profit > 0:
                self.profit_factor = float("inf")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_return": self.total_return,
            "daily_return": self.daily_return,
            "weekly_return": self.weekly_return,
            "monthly_return": self.monthly_return,
            "yearly_return": self.yearly_return,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "total_pnl": self.total_pnl,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "calmar_ratio": self.calmar_ratio,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "recovery_factor": self.recovery_factor,
        }


@dataclass
class Account(BaseModel, TimestampMixin, SerializableMixin, Timestamped):
    """Complete account model."""

    # Account info
    account_id: str = ""
    account_type: str = "SPOT"  # SPOT, MARGIN, FUTURES, etc.
    currency: str = "USDT"  # Base currency

    # Balances
    balances: Dict[str, AccountBalance] = field(default_factory=dict)
    total_balance: float = 0.0  # Total in base currency
    available_balance: float = 0.0  # Available for trading

    # Equity and margin
    equity: float = 0.0  # Account equity
    margin_info: AccountMargin = field(default_factory=AccountMargin)

    # Risk
    risk_info: AccountRisk = field(default_factory=AccountRisk)

    # Performance
    performance: AccountPerformance = field(default_factory=AccountPerformance)

    # Positions and orders
    positions: List[Dict[str, Any]] = field(default_factory=list)
    open_orders: List[Dict[str, Any]] = field(default_factory=list)

    # Trading permissions
    can_trade: bool = True
    can_withdraw: bool = True
    can_deposit: bool = True

    # Limits
    maker_fee: float = 0.0002  # 0.02%
    taker_fee: float = 0.0004  # 0.04%

    # Status
    is_locked: bool = False
    lock_reason: Optional[str] = None

    # Metadata
    exchange: str = "unknown"
    last_update: int = field(default_factory=lambda: int(time.time() * 1000))

    def validate(self) -> List[str]:
        """Validate account data."""
        errors = []

        if not self.account_id:
            errors.append("Account ID is required")

        if not self.currency:
            errors.append("Currency is required")

        if self.total_balance < 0:
            errors.append("Total balance cannot be negative")

        if self.maker_fee < 0 or self.taker_fee < 0:
            errors.append("Fees cannot be negative")

        return errors

    @property
    def is_margin_account(self) -> bool:
        """Check if margin account."""
        return self.account_type in ["MARGIN", "FUTURES", "PERPETUAL"]

    @property
    def free_balance(self) -> float:
        """Get free balance in base currency."""
        if self.currency in self.balances:
            return self.balances[self.currency].free
        return 0

    @property
    def used_balance(self) -> float:
        """Get used balance in base currency."""
        if self.currency in self.balances:
            return self.balances[self.currency].locked
        return 0

    @property
    def margin_level(self) -> float:
        """Get margin level percentage."""
        if self.is_margin_account:
            return self.margin_info.margin_level
        return float("inf")

    @property
    def risk_level(self) -> str:
        """Get account risk level."""
        return self.risk_info.get_risk_level()

    def add_balance(self, asset: str, free: float, locked: float) -> None:
        """
        Add or update balance for an asset.

        Args:
            asset: Asset symbol
            free: Free balance
            locked: Locked balance
        """
        if asset not in self.balances:
            self.balances[asset] = AccountBalance(asset=asset)

        self.balances[asset].update(free, locked)

        # Update total if base currency
        if asset == self.currency:
            self.total_balance = self.balances[asset].total
            self.available_balance = self.balances[asset].free

        self.touch()

    def update_margin(
        self, initial: float, maintenance: float, balance: float, available: float
    ) -> None:
        """
        Update margin information.

        Args:
            initial: Initial margin
            maintenance: Maintenance margin
            balance: Margin balance
            available: Available margin
        """
        self.margin_info.initial_margin = initial
        self.margin_info.maintenance_margin = maintenance
        self.margin_info.margin_balance = balance
        self.margin_info.available_margin = available
        self.margin_info.calculate_margin_level()

        self.touch()

    def update_risk(self, exposure: float, positions: int, orders: int) -> None:
        """
        Update risk information.

        Args:
            exposure: Total exposure
            positions: Open positions count
            orders: Open orders count
        """
        self.risk_info.total_exposure = exposure
        self.risk_info.open_positions = positions
        self.risk_info.open_orders = orders
        self.risk_info.calculate_risk_score()

        self.touch()

    def lock(self, reason: str) -> None:
        """
        Lock account.

        Args:
            reason: Lock reason
        """
        self.is_locked = True
        self.lock_reason = reason
        self.can_trade = False
        self.touch()

    def unlock(self) -> None:
        """Unlock account."""
        self.is_locked = False
        self.lock_reason = None
        self.can_trade = True
        self.touch()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Account":
        """Create account from dictionary."""
        account = cls(
            account_id=data.get("account_id", ""),
            account_type=data.get("account_type", "SPOT"),
            currency=data.get("currency", "USDT"),
            total_balance=float(data.get("total_balance", 0)),
            available_balance=float(data.get("available_balance", 0)),
            equity=float(data.get("equity", 0)),
            can_trade=data.get("can_trade", True),
            can_withdraw=data.get("can_withdraw", True),
            can_deposit=data.get("can_deposit", True),
            maker_fee=float(data.get("maker_fee", 0.0002)),
            taker_fee=float(data.get("taker_fee", 0.0004)),
            is_locked=data.get("is_locked", False),
            lock_reason=data.get("lock_reason"),
            exchange=data.get("exchange", "unknown"),
            last_update=data.get("last_update", int(time.time() * 1000)),
        )

        # Set timestamps
        account.created_at = data.get("created_at", account.created_at)
        account.updated_at = data.get("updated_at", account.updated_at)

        # Load balances
        for asset, balance_data in data.get("balances", {}).items():
            if isinstance(balance_data, dict):
                balance = AccountBalance(
                    asset=asset,
                    free=float(balance_data.get("free", 0)),
                    locked=float(balance_data.get("locked", 0)),
                    total=float(balance_data.get("total", 0)),
                )
                account.balances[asset] = balance

        # Load margin info
        margin_data = data.get("margin_info", {})
        if margin_data:
            account.margin_info = AccountMargin(
                initial_margin=float(margin_data.get("initial_margin", 0)),
                maintenance_margin=float(margin_data.get("maintenance_margin", 0)),
                margin_balance=float(margin_data.get("margin_balance", 0)),
                available_margin=float(margin_data.get("available_margin", 0)),
                margin_level=float(margin_data.get("margin_level", 0)),
                margin_ratio=float(margin_data.get("margin_ratio", 0)),
                account_leverage=float(margin_data.get("account_leverage", 1)),
                max_leverage=float(margin_data.get("max_leverage", 1)),
                liquidation_price=margin_data.get("liquidation_price"),
            )

        # Load risk info
        risk_data = data.get("risk_info", {})
        if risk_data:
            account.risk_info = AccountRisk(
                total_exposure=float(risk_data.get("total_exposure", 0)),
                max_exposure=float(risk_data.get("max_exposure", 0)),
                exposure_ratio=float(risk_data.get("exposure_ratio", 0)),
                open_positions=int(risk_data.get("open_positions", 0)),
                max_positions=int(risk_data.get("max_positions", 0)),
                open_orders=int(risk_data.get("open_orders", 0)),
                max_orders=int(risk_data.get("max_orders", 0)),
                value_at_risk=float(risk_data.get("value_at_risk", 0)),
                expected_shortfall=float(risk_data.get("expected_shortfall", 0)),
                max_drawdown=float(risk_data.get("max_drawdown", 0)),
                current_drawdown=float(risk_data.get("current_drawdown", 0)),
                risk_score=float(risk_data.get("risk_score", 0)),
                leverage_risk=float(risk_data.get("leverage_risk", 0)),
                concentration_risk=float(risk_data.get("concentration_risk", 0)),
            )

        # Load performance
        perf_data = data.get("performance", {})
        if perf_data:
            account.performance = AccountPerformance(
                total_return=float(perf_data.get("total_return", 0)),
                daily_return=float(perf_data.get("daily_return", 0)),
                weekly_return=float(perf_data.get("weekly_return", 0)),
                monthly_return=float(perf_data.get("monthly_return", 0)),
                yearly_return=float(perf_data.get("yearly_return", 0)),
                realized_pnl=float(perf_data.get("realized_pnl", 0)),
                unrealized_pnl=float(perf_data.get("unrealized_pnl", 0)),
                total_pnl=float(perf_data.get("total_pnl", 0)),
                total_trades=int(perf_data.get("total_trades", 0)),
                winning_trades=int(perf_data.get("winning_trades", 0)),
                losing_trades=int(perf_data.get("losing_trades", 0)),
                win_rate=float(perf_data.get("win_rate", 0)),
                sharpe_ratio=float(perf_data.get("sharpe_ratio", 0)),
                sortino_ratio=float(perf_data.get("sortino_ratio", 0)),
                calmar_ratio=float(perf_data.get("calmar_ratio", 0)),
                profit_factor=float(perf_data.get("profit_factor", 0)),
                expectancy=float(perf_data.get("expectancy", 0)),
                recovery_factor=float(perf_data.get("recovery_factor", 0)),
            )

        # Load positions and orders
        account.positions = data.get("positions", [])
        account.open_orders = data.get("open_orders", [])

        return account

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "account_id": self.account_id,
            "account_type": self.account_type,
            "currency": self.currency,
            "balances": {asset: balance.to_dict() for asset, balance in self.balances.items()},
            "total_balance": self.total_balance,
            "available_balance": self.available_balance,
            "equity": self.equity,
            "margin_info": self.margin_info.to_dict(),
            "risk_info": self.risk_info.to_dict(),
            "performance": self.performance.to_dict(),
            "positions": self.positions,
            "open_orders": self.open_orders,
            "can_trade": self.can_trade,
            "can_withdraw": self.can_withdraw,
            "can_deposit": self.can_deposit,
            "maker_fee": self.maker_fee,
            "taker_fee": self.taker_fee,
            "is_locked": self.is_locked,
            "lock_reason": self.lock_reason,
            "exchange": self.exchange,
            "last_update": self.last_update,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def __repr__(self) -> str:
        """String representation."""
        lock_str = " 🔒" if self.is_locked else ""
        risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}.get(
            self.risk_level, "⚪"
        )

        return (
            f"Account({self.account_id} {self.currency} "
            f"Balance:{self.total_balance:.2f} "
            f"Available:{self.available_balance:.2f} "
            f"{risk_icon}{lock_str})"
        )
