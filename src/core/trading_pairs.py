"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 11.01.2026.
    File        : trading_pairs.py
    Project     : Project-103
    Description : Trading pairs configuration — loads per-pair settings (lot step, min/max quantity, price/quantity precision) from config/trading_pairs.yaml.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PairInfo:
    """Trading pair information"""

    symbol: str
    base_asset: str = ""
    quote_asset: str = "USDT"
    min_quantity: float = 0.001
    max_quantity: float = 100000.0
    quantity_step: float = 0.001
    price_precision: int = 2
    quantity_precision: int = 3
    contract_size: float = 1.0  # 1.0 for crypto, 100 for XAUUSD (1 lot = 100 oz)
    commission_type: str = "percentage"  # "percentage" (crypto) or "per_lot" (MT5)
    commission_per_lot: float = 0.0  # fixed $ per lot, only for per_lot type
    is_active: bool = True
    is_shitcoin: bool = False

    def round_quantity(self, raw_quantity: float) -> float:
        """Round quantity to valid step size"""
        if self.quantity_step <= 0:
            return raw_quantity

        # Calculate decimals from step
        if self.quantity_step >= 1:
            decimals = 0
        else:
            decimals = int(-math.log10(self.quantity_step))

        # Round to the NEAREST step.
        # Flooring threw away up to a full step, and near the minimum lot a step
        # is the whole position: at a $1.9k balance a grid strategy computed
        # 0.0194 lot and traded 0.01 (-48.5%, see the live log 2026-09-08 03:56).
        # That halves the position's weight against the account — and since the
        # fan-batch thresholds are a share of the account, it moves every batch
        # exit, not just the trade size.
        quantity = math.floor(raw_quantity / self.quantity_step + 0.5) * self.quantity_step
        quantity = round(quantity, decimals)

        # Ensure at least minimum
        return max(quantity, self.min_quantity)

    def round_price(self, raw_price: float) -> float:
        """Round price to valid precision"""
        return round(raw_price, self.price_precision)


class TradingPairsConfig:
    """
    Singleton configuration for trading pairs.

    Usage:
        from src.core.trading_pairs import trading_pairs

        step = trading_pairs.get_quantity_step("BTCUSDT")
        qty = trading_pairs.round_quantity("BTCUSDT", 0.12345)
    """

    _instance: Optional["TradingPairsConfig"] = None
    _pairs: Dict[str, PairInfo] = {}
    _loaded: bool = False
    _warned_cross: set = set()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, config_path: Optional[str] = None) -> None:
        """Load trading pairs from YAML file"""
        if config_path is None:
            # Try common paths
            possible_paths = [
                Path("config/trading_pairs.yaml"),
                Path("../config/trading_pairs.yaml"),
                Path(__file__).parent.parent.parent / "config" / "trading_pairs.yaml",
            ]
            for path in possible_paths:
                if path.exists():
                    config_path = str(path)
                    break

        if config_path is None or not Path(config_path).exists():
            logger.warning("Trading pairs config not found, using defaults")
            self._loaded = True
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            for symbol, config in data.items():
                if isinstance(config, dict):
                    self._pairs[symbol] = PairInfo(
                        symbol=symbol,
                        base_asset=config.get("base_asset", ""),
                        quote_asset=config.get("quote_asset", "USDT"),
                        min_quantity=float(config.get("min_quantity", 0.001)),
                        max_quantity=float(config.get("max_quantity", 100000.0)),
                        quantity_step=float(config.get("quantity_step", 0.001)),
                        price_precision=int(config.get("price_precision", 2)),
                        quantity_precision=int(config.get("quantity_precision", 3)),
                        contract_size=float(config.get("contract_size", 1.0)),
                        commission_type=config.get("commission_type", "percentage"),
                        commission_per_lot=float(config.get("commission_per_lot", 0.0)),
                        is_active=config.get("is_active", True),
                        is_shitcoin=config.get("is_shitcoin", False),
                    )

            self._loaded = True
            logger.info(f"Loaded {len(self._pairs)} trading pairs from {config_path}")

        except Exception as e:
            logger.error(f"Failed to load trading pairs config: {e}")
            self._loaded = True

    def get(self, symbol: str) -> Optional[PairInfo]:
        """Get pair info by symbol"""
        if not self._loaded:
            self.load()
        return self._pairs.get(symbol)

    def get_quantity_step(self, symbol: str) -> float:
        """Get quantity step for symbol, with smart fallback"""
        pair = self.get(symbol)
        if pair:
            return pair.quantity_step

        # Fallback based on common patterns
        return self._estimate_quantity_step(symbol)

    def _estimate_quantity_step(self, symbol: str) -> float:
        """Estimate quantity step based on symbol name"""
        # Major coins
        if symbol in ("BTCUSDT", "BTCPERP"):
            return 0.001
        if symbol in ("ETHUSDT", "ETHPERP"):
            return 0.01

        # Check for known patterns
        base = symbol.replace("USDT", "").replace("PERP", "")

        # Large caps typically have smaller steps
        large_caps = {"BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "AVAX", "DOT", "LINK", "LTC"}
        if base in large_caps:
            return 0.01

        # Meme/small coins typically need larger steps
        meme_coins = {"DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "MEME"}
        if base in meme_coins:
            return 1.0

        # Default for unknown
        return 0.1

    def round_quantity(self, symbol: str, raw_quantity: float, price: float = 0) -> float:
        """
        Round quantity to valid step size for symbol.

        Args:
            symbol: Trading pair symbol
            raw_quantity: Raw quantity to round
            price: Current price (used for fallback estimation if pair not in config)
        """
        pair = self.get(symbol)
        if pair:
            return pair.round_quantity(raw_quantity)

        # Fallback: estimate step from price if pair not configured
        step = self.get_quantity_step(symbol)

        # Additional price-based estimation if step seems wrong
        if price > 0 and step == 0.1:  # Default fallback
            if price > 10000:
                step = 0.001  # High-price assets (BTC)
            elif price > 1000:
                step = 0.01  # Mid-price (ETH, BNB)
            elif price > 100:
                step = 0.1  # Lower price (SOL, etc)
            elif price > 1:
                step = 1.0  # Low price
            elif price > 0.01:
                step = 10.0  # Very low price
            else:
                step = 100.0  # Micro price (meme coins)

        # Round to step
        if step >= 1:
            decimals = 0
        else:
            decimals = int(-math.log10(step))

        quantity = math.floor(raw_quantity / step) * step
        quantity = round(quantity, decimals)
        return max(quantity, step)

    def is_shitcoin(self, symbol: str) -> bool:
        """Check if symbol is marked as shitcoin (higher risk)"""
        pair = self.get(symbol)
        return pair.is_shitcoin if pair else False

    def get_max_quantity(self, symbol: str) -> float:
        """Get maximum order quantity for symbol."""
        pair = self.get(symbol)
        if pair:
            return pair.max_quantity
        # Default fallback - very high limit
        return 100_000_000.0

    def get_contract_size(self, symbol: str) -> float:
        """Get contract size for symbol (1.0 for crypto, 100 for XAUUSD etc)."""
        pair = self.get(symbol)
        if pair:
            return pair.contract_size
        return 1.0

    def usd_is_base(self, symbol: str) -> bool:
        """True for USDxxx pairs quoted in a non-dollar currency (USDJPY+, USDCAD+ ...).

        Their PnL is computed in the quote currency (JPY) and converted to USD by
        dividing by the rate — the same way MT5 converts profit to the account currency.
        """
        pair = self.get(symbol)
        if not pair:
            return False
        quote = (pair.quote_asset or "").upper()
        return quote not in ("USD", "USDT", "USDC", "") and (pair.base_asset or "").upper() == "USD"

    def usd_price(self, symbol: str, price: float) -> float:
        """Price of one unit of the base in USD: the divisor "notional in $ -> base units".

        XAUUSD+ -> price (1 ounce costs price $); USDJPY+ -> 1.0 (the base is the dollar).
        Lots = notional_usd / (usd_price x contract_size). Live trading used to divide by
        the price in yen, making the USDJPY lot ~158x too small.
        """
        return price * self.quote_to_usd(symbol, price)

    def quote_to_usd(self, symbol: str, price: float) -> float:
        """Multiplier "1 unit of quote currency -> USD" at the given price.

        USD quote (XAUUSD+, EURUSD, crypto) -> 1.0; USDxxx -> 1/price.
        Crosses without USD (EURJPY, Nikkei225 in JPY) are not supported: they need a third
        pair's rate — 1.0 is returned with a warning (as before currency support).
        """
        pair = self.get(symbol)
        if not pair:
            return 1.0
        quote = (pair.quote_asset or "").upper()
        if quote in ("USD", "USDT", "USDC", ""):
            return 1.0
        if (pair.base_asset or "").upper() == "USD" and price > 0:
            return 1.0 / price
        if symbol not in self._warned_cross:
            self._warned_cross.add(symbol)
            logger.warning(
                f"{symbol}: quote currency {quote} without USD as base — PnL is NOT converted to USD"
            )
        return 1.0

    def get_min_quantity(self, symbol: str) -> float:
        """Get minimum order quantity for symbol."""
        pair = self.get(symbol)
        if pair:
            return pair.min_quantity
        return 0.001

    def get_all_symbols(self) -> list:
        """Get list of all configured symbols"""
        if not self._loaded:
            self.load()
        return list(self._pairs.keys())

    def get_active_symbols(self) -> list:
        """Get list of active symbols only"""
        if not self._loaded:
            self.load()
        return [s for s, p in self._pairs.items() if p.is_active]


# Global singleton instance
trading_pairs = TradingPairsConfig()
