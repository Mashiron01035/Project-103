"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : risk_manager.py
    Project     : Project-103
    Description : Main risk manager that coordinates all risk management components.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.core.trading_pairs import trading_pairs
from src.models.position import Position
from src.models.signal import Signal, SignalType
from src.risk.correlation_analyzer import CorrelationAnalyzer
from src.risk.drawdown_controller import DrawdownController
from src.risk.portfolio_manager import PortfolioManager
from src.risk.position_sizing import PositionSizer
from src.risk.stop_loss_calculator import StopLossCalculator
from src.risk.take_profit_calculator import TakeProfitCalculator

logger = logging.getLogger(__name__)


@dataclass
class RiskCalculationRequest:
    """Request for risk calculation."""

    signal: Signal
    account_balance: float
    open_positions: List[Position]
    daily_pnl: float
    market_data: Dict[str, Any]
    risk_config: Optional[Dict[str, Any]] = None


@dataclass
class RiskDecision:
    """Risk management decision."""

    approved: bool
    position_size: float  # Size in base currency
    position_size_usdt: float  # Size in USDT
    leverage: int

    # Price levels
    stop_loss: float
    take_profit: List[float]  # Multiple TP levels

    # Risk parameters
    risk_amount: float  # Risk in USDT
    risk_percent: float  # Risk as % of account
    potential_profit: float  # Potential profit at first TP
    risk_reward_ratio: float

    # Rejection reason (if not approved)
    rejection_reason: Optional[str]

    # Recommendations
    recommendations: List[str]

    # Additional info
    metadata: Dict[str, Any]


class RiskManager:
    """
    Main risk manager coordinating all risk components.

    Evaluates signals, calculates position sizes, sets stop losses
    and take profits, and enforces risk limits.
    """

    def __init__(self, account_balance: float, config: Optional[Dict[str, Any]] = None):
        """
        Initialize RiskManager.

        Args:
            account_balance: Initial account balance
            config: Risk management configuration
        """
        self.account_balance = account_balance
        self.config = config or {}

        # Initialize components
        self.position_sizer = PositionSizer(self.config.get("position_sizing", {}))
        self.sl_calculator = StopLossCalculator(self.config.get("stop_loss", {}))
        self.tp_calculator = TakeProfitCalculator(self.config.get("take_profit", {}))
        self.portfolio_manager = PortfolioManager(self.config.get("portfolio", {}))
        self.correlation_analyzer = CorrelationAnalyzer(self.config.get("correlation", {}))
        self.drawdown_controller = DrawdownController(self.config.get("drawdown", {}))

        # Risk parameters
        self.min_risk_reward = self.config.get("min_risk_reward", 1.5)

        logger.info(f"RiskManager initialized with balance={account_balance}")

    def evaluate_signal(self, request: RiskCalculationRequest) -> RiskDecision:
        """
        Evaluate signal and make risk decision.

        Args:
            request: Risk calculation request

        Returns:
            RiskDecision object
        """
        signal = request.signal
        metadata = {"signal_id": signal.id}
        recommendations = []

        # Check drawdown status
        dd_metrics = self.drawdown_controller.update(request.account_balance)
        metadata["drawdown_status"] = dd_metrics.status.value

        if not self.drawdown_controller.can_trade(dd_metrics):
            return RiskDecision(
                approved=False,
                position_size=0,
                position_size_usdt=0,
                leverage=1,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                risk_amount=0,
                risk_percent=0,
                potential_profit=0,
                risk_reward_ratio=0,
                rejection_reason="Trading halted due to drawdown",
                recommendations=[],
                metadata=metadata,
            )

        # Check portfolio limits
        can_open, reason = self.portfolio_manager.can_open_position(
            signal.symbol, request.open_positions, request.account_balance
        )

        if not can_open:
            return RiskDecision(
                approved=False,
                position_size=0,
                position_size_usdt=0,
                leverage=1,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                risk_amount=0,
                risk_percent=0,
                potential_profit=0,
                risk_reward_ratio=0,
                rejection_reason=reason,
                recommendations=[],
                metadata=metadata,
            )

        # Check correlation
        existing_symbols = [p.symbol for p in request.open_positions]
        if not self.correlation_analyzer.is_safe_to_add(signal.symbol, existing_symbols):
            return RiskDecision(
                approved=False,
                position_size=0,
                position_size_usdt=0,
                leverage=1,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                risk_amount=0,
                risk_percent=0,
                potential_profit=0,
                risk_reward_ratio=0,
                rejection_reason="Too many correlated positions",
                recommendations=[],
                metadata=metadata,
            )

        # Calculate stop loss
        stop_loss = self.sl_calculator.calculate(
            signal.entry_price,
            signal.signal_type,
            request.market_data,
            request.market_data.get("atr"),
        )

        # Calculate take profit levels
        tp_levels = self.tp_calculator.calculate(
            signal.entry_price, stop_loss, signal.signal_type, request.market_data
        )

        take_profit = [level.price for level in tp_levels]

        # Check risk/reward ratio
        if tp_levels:
            first_tp = tp_levels[0]
            if first_tp.risk_reward_ratio < self.min_risk_reward:
                return RiskDecision(
                    approved=False,
                    position_size=0,
                    position_size_usdt=0,
                    leverage=1,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_amount=0,
                    risk_percent=0,
                    potential_profit=0,
                    risk_reward_ratio=first_tp.risk_reward_ratio,
                    rejection_reason=f"R:R {first_tp.risk_reward_ratio:.2f} below minimum {self.min_risk_reward}",
                    recommendations=[],
                    metadata=metadata,
                )

        # Calculate position size
        position_result = self.position_sizer.calculate(
            signal, request.account_balance, request.market_data.get("volatility", 0)
        )

        # Apply drawdown risk multiplier
        risk_multiplier = self.drawdown_controller.get_risk_multiplier(dd_metrics)
        if risk_multiplier < 1.0:
            position_result.size_base *= risk_multiplier
            position_result.size_quote *= risk_multiplier
            position_result.risk_amount *= risk_multiplier
            recommendations.append(
                f"Position size reduced to {risk_multiplier*100:.0f}% due to drawdown"
            )

        # Calculate potential profit
        if tp_levels:
            first_tp_price = tp_levels[0].price
            if signal.signal_type.value == "LONG":
                potential_profit = (first_tp_price - signal.entry_price) * position_result.size_base
            else:
                potential_profit = (signal.entry_price - first_tp_price) * position_result.size_base
        else:
            potential_profit = 0

        symbol = signal.symbol
        pair_info = trading_pairs.get(symbol)

        position_size = trading_pairs.round_quantity(
            symbol, position_result.size_base, signal.entry_price
        )
        position_size_usdt = position_size * signal.entry_price

        if pair_info:
            stop_loss = pair_info.round_price(stop_loss)
            take_profit = [pair_info.round_price(tp) for tp in take_profit]

        # Create approved decision
        return RiskDecision(
            approved=True,
            position_size=position_size,
            position_size_usdt=position_size_usdt,
            leverage=position_result.leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=position_result.risk_amount,
            risk_percent=position_result.risk_percent,
            potential_profit=potential_profit,
            risk_reward_ratio=tp_levels[0].risk_reward_ratio if tp_levels else 0,
            rejection_reason=None,
            recommendations=recommendations,
            metadata=metadata,
        )

    def update_account_balance(self, new_balance: float) -> None:
        """
        Update account balance.

        Args:
            new_balance: New account balance
        """
        self.account_balance = new_balance
        logger.info(f"Account balance updated to {new_balance:.2f}")

    def calculate_position_size(
        self, entry_price: float, stop_loss: float, risk_amount: float
    ) -> float:
        """
        Calculate position size based on risk amount.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            risk_amount: Risk amount in USDT

        Returns:
            Position size in base currency
        """
        price_diff = abs(entry_price - stop_loss)
        if price_diff <= 0:
            logger.error("Invalid price difference for position size calculation")
            return 0.0

        return risk_amount / price_diff

    def check_daily_limits(self, daily_pnl: float, account_balance: float) -> bool:
        """
        Check if daily limits are exceeded.

        Args:
            daily_pnl: Current daily P&L
            account_balance: Account balance

        Returns:
            True if within limits, False if exceeded
        """
        max_daily_loss = self.config.get("max_daily_loss_percent", 0.05)  # 5%
        max_loss_amount = account_balance * max_daily_loss

        if daily_pnl < -max_loss_amount:
            logger.warning(
                f"Daily loss limit exceeded: {daily_pnl:.2f} " f"(max: {-max_loss_amount:.2f})"
            )
            return False

        return True

    def adjust_for_volatility(self, base_risk: float, volatility: float) -> float:
        """
        Adjust risk based on market volatility.

        Args:
            base_risk: Base risk amount
            volatility: Market volatility (0-1)

        Returns:
            Adjusted risk amount
        """
        # Reduce risk in high volatility
        if volatility > 0.05:  # 5% volatility
            multiplier = 0.5
        elif volatility > 0.03:  # 3% volatility
            multiplier = 0.75
        elif volatility > 0.02:  # 2% volatility
            multiplier = 0.9
        else:
            multiplier = 1.0

        adjusted_risk = base_risk * multiplier

        if multiplier < 1.0:
            logger.info(
                f"Risk adjusted for volatility: {base_risk:.2f} -> "
                f"{adjusted_risk:.2f} (volatility: {volatility:.2%})"
            )

        return adjusted_risk

    def get_risk_reward_ratio(
        self, symbol: str, volatility: float, is_shitcoin: bool = False
    ) -> float:
        """
        Get target risk/reward ratio based on asset characteristics.

        Args:
            symbol: Trading symbol
            volatility: Market volatility
            is_shitcoin: Whether asset is high-risk

        Returns:
            Target R:R ratio
        """
        # Base R:R ratios from config
        base_rr = self.config.get("base_risk_reward", 2.0)
        shitcoin_rr = self.config.get("shitcoin_risk_reward", 3.0)

        if is_shitcoin:
            target_rr = shitcoin_rr
        else:
            target_rr = base_rr

        # Increase R:R requirement in high volatility
        if volatility > 0.05:
            target_rr *= 1.5
        elif volatility > 0.03:
            target_rr *= 1.25

        return target_rr

    def should_reduce_exposure(self, positions: List[Position], account_balance: float) -> bool:
        """
        Check if exposure should be reduced.

        Args:
            positions: Open positions
            account_balance: Account balance

        Returns:
            True if should reduce exposure
        """
        metrics = self.portfolio_manager.calculate_metrics(positions, account_balance)

        # Check if exposure is too high
        max_exposure = self.config.get("max_exposure_percent", 0.50)  # 50%
        warning_threshold = max_exposure * 0.8  # 80% of max

        if metrics.exposure_percent / 100 >= warning_threshold:
            logger.warning(
                f"High exposure: {metrics.exposure_percent:.1f}% "
                f"(warning threshold: {warning_threshold*100:.1f}%)"
            )
            return True

        return False

    def get_max_position_size_for_symbol(self, symbol: str, account_balance: float) -> float:
        """
        Get maximum position size for a symbol.

        Args:
            symbol: Trading symbol
            account_balance: Account balance

        Returns:
            Maximum position size in USDT
        """
        max_percent = self.config.get("max_position_percent", 0.10)  # 10%
        return account_balance * max_percent

    def validate_stop_loss(
        self, entry_price: float, stop_loss: float, signal_type: SignalType
    ) -> tuple[bool, Optional[str]]:
        """
        Validate stop loss placement.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            signal_type: Signal type (LONG/SHORT)

        Returns:
            Tuple of (is_valid, error_message)
        """
        if stop_loss <= 0:
            return False, "Stop loss must be positive"

        # Check stop loss is on correct side
        if signal_type.value == "LONG":
            if stop_loss >= entry_price:
                return False, "Stop loss must be below entry for LONG"
        else:  # SHORT
            if stop_loss <= entry_price:
                return False, "Stop loss must be above entry for SHORT"

        # Check stop loss distance
        distance_percent = abs(entry_price - stop_loss) / entry_price
        min_distance = self.config.get("min_stop_distance_percent", 0.005)  # 0.5%
        max_distance = self.config.get("max_stop_distance_percent", 0.10)  # 10%

        if distance_percent < min_distance:
            return False, f"Stop loss too tight: {distance_percent:.2%} (min: {min_distance:.2%})"

        if distance_percent > max_distance:
            return False, f"Stop loss too wide: {distance_percent:.2%} (max: {max_distance:.2%})"

        return True, None

    def get_portfolio_metrics(
        self, positions: List[Position], account_balance: float
    ) -> Dict[str, Any]:
        """
        Get comprehensive portfolio metrics.

        Args:
            positions: List of open positions
            account_balance: Account balance

        Returns:
            Dictionary with portfolio metrics
        """
        portfolio_metrics = self.portfolio_manager.calculate_metrics(positions, account_balance)

        dd_metrics = self.drawdown_controller.update(account_balance)

        return {
            "positions": {
                "total": portfolio_metrics.total_positions,
                "long": portfolio_metrics.open_long_positions,
                "short": portfolio_metrics.open_short_positions,
                "symbols": portfolio_metrics.symbols,
            },
            "exposure": {
                "total_usdt": portfolio_metrics.total_exposure_usdt,
                "percent": portfolio_metrics.exposure_percent,
                "avg_position": portfolio_metrics.avg_position_size_usdt,
                "largest": portfolio_metrics.largest_position_usdt,
                "smallest": portfolio_metrics.smallest_position_usdt,
            },
            "drawdown": {
                "current_percent": dd_metrics.current_drawdown,
                "current_amount": dd_metrics.current_drawdown_amount,
                "max_percent": dd_metrics.max_drawdown,
                "status": dd_metrics.status.value,
                "days_in_drawdown": dd_metrics.days_in_drawdown,
                "recovery_needed": dd_metrics.recovery_needed,
            },
            "limits": {
                "max_positions": self.portfolio_manager.max_positions,
                "max_exposure_percent": self.portfolio_manager.max_exposure_percent * 100,
                "positions_remaining": self.portfolio_manager.max_positions
                - portfolio_metrics.total_positions,
            },
        }

    def get_risk_summary(self) -> Dict[str, Any]:
        """
        Get summary of risk management configuration.

        Returns:
            Dictionary with risk management settings
        """
        return {
            "account_balance": self.account_balance,
            "position_sizing": {
                "method": self.position_sizer.method.value,
                "max_risk_per_trade": self.position_sizer.max_risk_per_trade * 100,
                "max_position_percent": self.position_sizer.max_position_percent * 100,
            },
            "stop_loss": {
                "method": self.sl_calculator.method.value,
                "atr_multiplier": self.sl_calculator.atr_multiplier,
                "default_percentage": self.sl_calculator.default_percentage * 100,
            },
            "take_profit": {
                "method": self.tp_calculator.method.value,
                "num_levels": self.tp_calculator.num_levels,
                "default_rr_ratios": self.tp_calculator.default_rr_ratios,
            },
            "portfolio": {
                "max_positions": self.portfolio_manager.max_positions,
                "max_exposure_percent": self.portfolio_manager.max_exposure_percent * 100,
            },
            "drawdown": {
                "max_daily": self.drawdown_controller.max_daily_drawdown * 100,
                "max_weekly": self.drawdown_controller.max_weekly_drawdown * 100,
                "max_monthly": self.drawdown_controller.max_monthly_drawdown * 100,
            },
        }
