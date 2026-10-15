"""

    -*- coding: utf-8 -*-
    File        : __init__
    Project     : Project-103
    Description : Strategies module. Core trading strategy infrastructure for the bot.

                  Public reference build: ships two structurally different demo
                  strategies (session_breakout — momentum/market-entry; swing_fade —
                  mean-reversion/limit-entry). Proprietary live strategies are not
                  part of this repository.

"""

from typing import Any, Dict, Optional, Type

from src.strategies.base_strategy import BaseStrategy, OrderSide, Signal

# Session Breakout — opening-range breakout (market entry, momentum)
from src.strategies.session_breakout.session_breakout_strategy import SessionBreakoutStrategy
from src.strategies.strategy_manager import StrategyManager, StrategyMetrics
from src.strategies.strategy_selector import MarketCondition, MarketRegime, StrategySelector

# Swing Fade — fade first ZigZag swing of the session (limit-at-pivot entry)
from src.strategies.swing_fade import SwingFadeStrategy

# =============================================================================
# Strategy Registry — name (config/strategies.yaml key) -> strategy class
# =============================================================================

STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "session_breakout": SessionBreakoutStrategy,
    "swing_fade": SwingFadeStrategy,
}


def get_strategy_class(name: str) -> Optional[Type[BaseStrategy]]:
    """Get a strategy class by its registry name (key from strategies.yaml)."""
    return STRATEGY_REGISTRY.get(name)


def create_strategy(name: str, config: Optional[Dict[str, Any]] = None) -> Optional[BaseStrategy]:
    """Instantiate a strategy by name, passing the registry name when supported."""
    strategy_class = get_strategy_class(name)
    if strategy_class is None:
        return None
    # Pass registry name to strategies that accept it (multi-instance support)
    import inspect

    sig = inspect.signature(strategy_class.__init__)
    if "name" in sig.parameters:
        return strategy_class(config, name=name)
    return strategy_class(config)


def list_available_strategies() -> list:
    """Return the list of registered strategy names."""
    return list(STRATEGY_REGISTRY.keys())


def register_strategy(name: str, strategy_class: Type[BaseStrategy]) -> None:
    """Register a new strategy in the registry."""
    STRATEGY_REGISTRY[name] = strategy_class


__all__ = [
    # Base classes
    "BaseStrategy",
    "Signal",
    "OrderSide",
    # Manager
    "StrategyManager",
    "StrategyMetrics",
    # Selector
    "StrategySelector",
    "MarketRegime",
    "MarketCondition",
    # Strategies
    "SessionBreakoutStrategy",
    "SwingFadeStrategy",
    # Registry
    "STRATEGY_REGISTRY",
    "get_strategy_class",
    "create_strategy",
    "list_available_strategies",
    "register_strategy",
]
