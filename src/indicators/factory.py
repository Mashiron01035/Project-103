"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : factory
    Project     : Project-103
    Description : Indicator factory. Manages indicator registration, creation, and batch calculation of multiple indicators.

    SPDX-License-Identifier: AGPL-3.0

"""

import re
from typing import Any, Callable, Dict, List, Tuple, Type

from src.indicators.base_indicator import BaseIndicator, Candle, IndicatorResult

# Regex pattern for parsing indicator format: type{param} or type{param1,param2} or type{}
# Examples: ema{12}, rsi{14}, macd{12,26,9}, bollinger{20,2.0}, vwap{}
INDICATOR_PATTERN = re.compile(r"^([a-zA-Z_]+)\{([^}]*)\}$")


def parse_indicator_key(key: str) -> Tuple[str, Dict[str, Any]]:
    """
    Parse indicator key in format 'type{params}' into type and parameters.

    Supported formats:
        - ema{12}           -> ("ema", {"period": 12})
        - rsi{14}           -> ("rsi", {"period": 14})
        - macd{12,26,9}     -> ("macd", {"fast": 12, "slow": 26, "signal": 9})
        - bollinger{20,2.0} -> ("bollinger", {"period": 20, "std_dev": 2.0})
        - stochastic{14,3}  -> ("stochastic", {"k_period": 14, "d_period": 3})
        - atr{14}           -> ("atr", {"period": 14})

    Args:
        key: Indicator key string (e.g., "ema{12}" or just "rsi")

    Returns:
        Tuple of (indicator_type, params_dict)

    Example:
        >>> parse_indicator_key("ema{21}")
        ('ema', {'period': 21})
        >>> parse_indicator_key("macd{12,26,9}")
        ('macd', {'fast': 12, 'slow': 26, 'signal': 9})
    """
    match = INDICATOR_PATTERN.match(key)

    if not match:
        # No params in key, return as-is with empty params
        return key, {}

    indicator_type = match.group(1).lower()
    params_str = match.group(2).strip()

    # Handle empty params (e.g., "vwap{}")
    if not params_str:
        return indicator_type, {}

    # Parse comma-separated params
    raw_params = [p.strip() for p in params_str.split(",")]

    # Convert to appropriate types
    parsed_params = []
    for p in raw_params:
        try:
            if "." in p:
                parsed_params.append(float(p))
            else:
                parsed_params.append(int(p))
        except ValueError:
            parsed_params.append(p)

    # Map params to indicator-specific parameter names
    params = _map_params_to_dict(indicator_type, parsed_params)

    return indicator_type, params


def _map_params_to_dict(indicator_type: str, params: List[Any]) -> Dict[str, Any]:
    """
    Map positional parameters to named parameters for each indicator type.

    Args:
        indicator_type: Type of indicator (ema, rsi, macd, etc.)
        params: List of positional parameters

    Returns:
        Dict with named parameters
    """
    if not params:
        return {}

    # Define parameter mappings for each indicator type
    param_mappings = {
        # Single period indicators
        "ema": ["period"],
        "sma": ["period"],
        "rsi": ["period"],
        "atr": ["period"],
        "std": ["period"],
        "obv": [],  # No params
        # Multi-param indicators
        "macd": ["fast", "slow", "signal"],
        "bollinger": ["period", "std_dev"],
        "stochastic": ["k_period", "d_period", "smooth"],
        "ao": ["fast_period", "slow_period"],
        "adx": ["period"],
        "vwap": [],
        "volume_profile": ["bins"],
        "asgma": ["smooth", "alma_length", "gma_length", "vol_period"],
    }

    mapping = param_mappings.get(indicator_type, ["period"])  # Default to period

    result = {}
    for i, param_name in enumerate(mapping):
        if i < len(params):
            result[param_name] = params[i]

    return result


class IndicatorFactory:
    """
    Factory for creating and registering indicators

    Allows you to:
    - Register new indicators
    - Create indicator instances
    - Calculate several indicators in parallel
    """

    def __init__(self):
        """Initialize the factory"""
        self._indicators: Dict[str, Type[BaseIndicator]] = {}
        self._custom_calculators: Dict[str, Callable] = {}

    def register_indicator(self, name: str, indicator_class: Type[BaseIndicator]) -> None:
        """
        Register an indicator class

        Args:
            name: Unique indicator name
            indicator_class: Indicator class (a BaseIndicator subclass)

        Raises:
            ValueError: If an indicator with this name is already registered
        """
        if name in self._indicators:
            raise ValueError(f"Indicator '{name}' is already registered")

        if not issubclass(indicator_class, BaseIndicator):
            raise ValueError("Indicator class must be a subclass of BaseIndicator")

        self._indicators[name] = indicator_class

    def register_custom_calculator(
        self, name: str, calculator: Callable[[List[Candle], Any], IndicatorResult]
    ) -> None:
        """
        Register a custom indicator calculation function

        Args:
            name: Unique indicator name
            calculator: Function that calculates the indicator

        Example:
            >>> def custom_rsi(candles, period=14):
            ...     # Calculation logic
            ...     return IndicatorResult(...)
            >>> factory.register_custom_calculator("custom_rsi", custom_rsi)
        """
        if name in self._custom_calculators:
            raise ValueError(f"Custom calculator '{name}' is already registered")

        self._custom_calculators[name] = calculator

    def create_indicator(self, name: str) -> BaseIndicator:
        """
        Create an indicator instance by name

        Args:
            name: Name of a registered indicator

        Returns:
            Indicator instance

        Raises:
            ValueError: If the indicator is not found
        """
        if name not in self._indicators:
            raise ValueError(
                f"Indicator '{name}' not found. " f"Available: {list(self._indicators.keys())}"
            )

        return self._indicators[name](name)

    def calculate(self, name: str, candles: List[Candle], **params) -> IndicatorResult:
        """
        Calculate an indicator by name

        Args:
            name: Indicator name
            candles: List of candles
            **params: Indicator parameters

        Returns:
            IndicatorResult

        Raises:
            ValueError: If the indicator is not found
        """
        # Check custom calculators
        if name in self._custom_calculators:
            return self._custom_calculators[name](candles, **params)

        # Create and calculate the indicator
        indicator = self.create_indicator(name)
        return indicator.calculate(candles, **params)

    def calculate_multiple(
        self, candles: List[Candle], indicators_config: List[str] | Dict[str, Dict[str, Any]]
    ) -> Dict[str, IndicatorResult]:
        """
        Calculate several indicators at once

        Supports two configuration formats:

        1. New format (list of strings):
            ["ema{9}", "ema{21}", "rsi{14}", "macd{12,26,9}"]

        2. Old format (dict):
            {
                "rsi": {"period": 14},
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "ema": {"period": 20}
            }

        Args:
            candles: List of candles
            indicators_config: Indicator configuration (list or dict)

        Returns:
            Dict of results {indicator_name: IndicatorResult}

        Example:
            # New format (recommended)
            >>> config = ["ema{9}", "ema{21}", "rsi{14}", "macd{12,26,9}"]
            >>> results = factory.calculate_multiple(candles, config)
            >>> ema9_value = results["ema{9}"].values["value"]

            # Old format (backward compatibility)
            >>> config = {"rsi": {"period": 14}, "ema": {"period": 20}}
            >>> results = factory.calculate_multiple(candles, config)
        """
        import logging

        results = {}

        # Handle new list format
        if isinstance(indicators_config, list):
            for indicator_key in indicators_config:
                try:
                    indicator_type, params = parse_indicator_key(indicator_key)
                    result = self.calculate(indicator_type, candles, **params)
                    # Store with original key (e.g., "ema{9}")
                    results[indicator_key] = result
                except Exception as e:
                    logging.error(f"Error calculating {indicator_key}: {e}")
            return results

        # Handle old dict format for backward compatibility
        for name, params in indicators_config.items():
            try:
                # Check if name contains params (new format in dict)
                indicator_type, parsed_params = parse_indicator_key(name)

                if parsed_params:
                    # New format: "ema{9}" as key
                    final_params = parsed_params
                    final_params.update(params)  # Merge with any additional params
                    result = self.calculate(indicator_type, candles, **final_params)
                else:
                    # Old format: "ema" as key with params in value
                    result = self.calculate(name, candles, **params)

                results[name] = result
            except Exception as e:
                logging.error(f"Error calculating {name}: {e}")

        return results

    def list_indicators(self) -> List[str]:
        """
        Return the list of registered indicators

        Returns:
            List of indicator names
        """
        all_indicators = list(self._indicators.keys()) + list(self._custom_calculators.keys())
        return sorted(all_indicators)

    def is_registered(self, name: str) -> bool:
        """
        Check whether an indicator is registered

        Args:
            name: Indicator name

        Returns:
            True if registered
        """
        return name in self._indicators or name in self._custom_calculators

    def unregister_indicator(self, name: str) -> None:
        """
        Remove an indicator from the registry

        Args:
            name: Indicator name
        """
        if name in self._indicators:
            del self._indicators[name]
        elif name in self._custom_calculators:
            del self._custom_calculators[name]

    def clear(self) -> None:
        """Clear all registered indicators"""
        self._indicators.clear()
        self._custom_calculators.clear()


# Global factory instance
_global_factory: IndicatorFactory = None


def get_indicator_factory() -> IndicatorFactory:
    """
    Get the global indicator factory instance

    Returns:
        IndicatorFactory
    """
    global _global_factory
    if _global_factory is None:
        _global_factory = IndicatorFactory()
        # Auto-register all standard indicators
        _register_default_indicators(_global_factory)
    return _global_factory


def _register_default_indicators(factory: IndicatorFactory) -> None:
    """
    Register all standard indicators

    Args:
        factory: Indicator factory
    """
    # Import indicators only on first use
    try:
        # Momentum indicators
        from src.indicators.momentum.asgma import ASGMA
        from src.indicators.momentum.awesome import AwesomeOscillator
        from src.indicators.momentum.macd import MACD
        from src.indicators.momentum.rsi import RSI
        from src.indicators.momentum.stochastic import Stochastic

        factory.register_indicator("rsi", RSI)
        factory.register_indicator("stochastic", Stochastic)
        factory.register_indicator("macd", MACD)
        factory.register_indicator("ao", AwesomeOscillator)
        factory.register_indicator("asgma", ASGMA)
    except ImportError:
        pass

    try:
        # Volume indicators
        from src.indicators.volume.obv import OBV
        from src.indicators.volume.order_flow import OrderFlowImbalance
        from src.indicators.volume.volume_profile import VolumeProfile
        from src.indicators.volume.vwap import VWAP

        factory.register_indicator("vwap", VWAP)
        factory.register_indicator("volume_profile", VolumeProfile)
        factory.register_indicator("obv", OBV)
        factory.register_indicator("order_flow", OrderFlowImbalance)
    except ImportError:
        pass

    try:
        # Trend indicators
        from src.indicators.trend.adx import ADX
        from src.indicators.trend.bollinger import BollingerBands
        from src.indicators.trend.ema import EMA
        from src.indicators.trend.sma import SMA

        factory.register_indicator("ema", EMA)
        factory.register_indicator("sma", SMA)
        factory.register_indicator("bollinger", BollingerBands)
        factory.register_indicator("adx", ADX)
    except ImportError:
        pass

    try:
        # Volatility indicators
        from src.indicators.volatility.atr import ATR
        from src.indicators.volatility.std import StandardDeviation

        factory.register_indicator("atr", ATR)
        factory.register_indicator("std", StandardDeviation)
    except ImportError:
        pass
