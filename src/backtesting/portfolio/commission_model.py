"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 27.11.2025.
    File        : commission_model.py
    Project     : Project-103
    Description : Commission models for backtesting. Provides different commission
                  calculation strategies: fixed, tiered, and rebate-based.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class CommissionTier:
    """Commission tier for tiered pricing."""

    min_volume: float
    max_volume: float
    maker_fee: float
    taker_fee: float


class CommissionModel(ABC):
    """Abstract base class for commission models."""

    @abstractmethod
    def calculate(
        self, quantity: float, price: float, is_maker: bool = False, contract_size: float = 1.0
    ) -> float:
        """
        Calculate commission for an order.

        Args:
            quantity: Order quantity
            price: Order price
            is_maker: Whether order is maker (limit) or taker (market)
            contract_size: Contract size (1.0 for crypto, 100 for XAUUSD)

        Returns:
            Commission amount in quote currency
        """
        pass


class FixedCommission(CommissionModel):
    """
    Fixed percentage commission model.

    Standard fee structure used by most exchanges.
    """

    def __init__(self, maker_fee: float = 0.0002, taker_fee: float = 0.0004):
        """
        Initialize fixed commission model.

        Args:
            maker_fee: Fee rate for maker orders (default 0.02%)
            taker_fee: Fee rate for taker orders (default 0.04%)
        """
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

    def calculate(
        self, quantity: float, price: float, is_maker: bool = False, contract_size: float = 1.0
    ) -> float:
        """Calculate commission using fixed rates."""
        notional_value = quantity * price * contract_size
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        commission = notional_value * fee_rate

        return commission


class TieredCommission(CommissionModel):
    """
    Tiered commission model based on trading volume.

    Higher volume traders get lower fees.
    """

    def __init__(self, tiers: List[CommissionTier]):
        """
        Initialize tiered commission model.

        Args:
            tiers: List of commission tiers sorted by volume
        """
        self.tiers = sorted(tiers, key=lambda x: x.min_volume)
        self.monthly_volume = 0.0

    def calculate(
        self, quantity: float, price: float, is_maker: bool = False, contract_size: float = 1.0
    ) -> float:
        """Calculate commission based on volume tier."""
        notional_value = quantity * price * contract_size

        # Find applicable tier
        applicable_tier = self.tiers[0]  # Default to first tier
        for tier in self.tiers:
            if tier.min_volume <= self.monthly_volume < tier.max_volume:
                applicable_tier = tier
                break

        fee_rate = applicable_tier.maker_fee if is_maker else applicable_tier.taker_fee
        commission = notional_value * fee_rate

        # Update monthly volume
        self.monthly_volume += notional_value

        return commission

    def reset_monthly_volume(self):
        """Reset monthly volume counter."""
        self.monthly_volume = 0.0


class RebateCommission(CommissionModel):
    """
    Commission model with rebates for market makers.

    Market makers receive rebate instead of paying fees.
    """

    def __init__(self, maker_rebate: float = 0.0001, taker_fee: float = 0.0004):
        """
        Initialize rebate commission model.

        Args:
            maker_rebate: Rebate rate for maker orders (negative fee)
            taker_fee: Fee rate for taker orders
        """
        self.maker_rebate = maker_rebate
        self.taker_fee = taker_fee

    def calculate(
        self, quantity: float, price: float, is_maker: bool = False, contract_size: float = 1.0
    ) -> float:
        """Calculate commission with rebate for makers."""
        notional_value = quantity * price * contract_size

        if is_maker:
            # Negative commission = rebate
            return -(notional_value * self.maker_rebate)
        else:
            return notional_value * self.taker_fee


class PerLotCommission(CommissionModel):
    """
    Per-lot fixed commission model (MT5/TradFi instruments).

    Used for forex/metals/CFDs where brokers charge a fixed amount per lot.
    Commission is charged once per round-trip (at entry only).
    """

    # Round-trip: commission charged once (at entry), not at exit
    round_trip = True

    def __init__(self, commission_per_lot: float = 7.0):
        """
        Initialize per-lot commission model.

        Args:
            commission_per_lot: Fixed commission per round-trip lot (default $7)
        """
        self.commission_per_lot = commission_per_lot

    def calculate(
        self, quantity: float, price: float, is_maker: bool = False, contract_size: float = 1.0
    ) -> float:
        """Calculate commission as fixed amount per lot."""
        commission = self.commission_per_lot * quantity

        return commission


class ZeroCommission(CommissionModel):
    """Zero commission model for testing purposes."""

    def calculate(
        self, quantity: float, price: float, is_maker: bool = False, contract_size: float = 1.0
    ) -> float:
        """Return zero commission."""
        return 0.0


def create_commission_model(
    model_type: str = "fixed", maker_fee: float = 0.0002, taker_fee: float = 0.0004, **kwargs
) -> CommissionModel:
    """
    Factory function to create commission models.

    Args:
        model_type: Type of model ('fixed', 'tiered', 'rebate', 'per_lot', 'zero')
        maker_fee: Maker fee rate
        taker_fee: Taker fee rate
        **kwargs: Additional arguments for specific models

    Returns:
        CommissionModel instance
    """
    if model_type == "fixed":
        return FixedCommission(maker_fee, taker_fee)
    elif model_type == "tiered":
        tiers = kwargs.get(
            "tiers",
            [
                CommissionTier(0, 1_000_000, 0.0004, 0.0006),
                CommissionTier(1_000_000, 10_000_000, 0.0003, 0.0005),
                CommissionTier(10_000_000, float("inf"), 0.0002, 0.0004),
            ],
        )
        return TieredCommission(tiers)
    elif model_type == "rebate":
        rebate = kwargs.get("maker_rebate", 0.0001)
        return RebateCommission(rebate, taker_fee)
    elif model_type == "per_lot":
        commission_per_lot = kwargs.get("commission_per_lot", 7.0)
        return PerLotCommission(commission_per_lot)
    elif model_type == "zero":
        return ZeroCommission()
    else:
        raise ValueError(f"Unknown commission model type: {model_type}")
