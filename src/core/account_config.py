import logging
import os
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class AccountConfig:
    """
    Account configuration.

    API keys are loaded from environment variables for security.
    Format: {ACCOUNT_ID}_API_KEY and {ACCOUNT_ID}_API_SECRET
    Example: MASHIRON_API_KEY, MASHIRON_API_SECRET
    """

    account_id: str
    name: str
    api_key: str = ""
    api_secret: str = ""
    mode: str = "paper"  # paper, demo, testnet, production
    strategies: List[str] = field(default_factory=list)
    enabled: bool = True

    @classmethod
    def from_yaml(cls, account_id: str, data: dict) -> "AccountConfig":
        """
        Load account from yaml config.

        API keys are loaded from environment variables:
        - {ACCOUNT_ID}_API_KEY or {account_id}_api_key
        - {ACCOUNT_ID}_API_SECRET or {account_id}_api_secret

        Fallback to yaml values if env vars not found (for backwards compatibility).
        """
        # Try to get API keys from environment (preferred)
        env_prefix = account_id.upper().replace("-", "_")
        api_key = (
            os.getenv(f"{env_prefix}_API_KEY")
            or os.getenv(f"{env_prefix}_API_SECRET".replace("_SECRET", "_KEY"))
            or data.get("api_key", "")
        )
        api_secret = os.getenv(f"{env_prefix}_API_SECRET") or data.get("api_secret", "")

        # Parse strategies - handle empty/invalid values
        strategies_raw = data.get("strategies", [])
        if isinstance(strategies_raw, list):
            strategies = [s for s in strategies_raw if s and s != "{none}" and isinstance(s, str)]
        else:
            strategies = []

        return cls(
            account_id=account_id,
            name=data.get("name", account_id),
            api_key=api_key,
            api_secret=api_secret,
            mode=data.get("mode", "paper"),
            strategies=strategies,
            enabled=data.get("enabled", True),
        )

    @classmethod
    def from_legacy_config(cls, bybit_config, risk_config) -> "AccountConfig":
        """Create account from legacy config.py settings."""
        if bybit_config.demo:
            mode = "demo"
            api_key = bybit_config.demo_api_key
            api_secret = bybit_config.demo_api_secret
        elif bybit_config.testnet:
            mode = "testnet"
            api_key = bybit_config.testnet_api_key
            api_secret = bybit_config.testnet_api_secret
        else:
            mode = "production"
            api_key = bybit_config.api_key
            api_secret = bybit_config.api_secret

        return cls(
            account_id="default",
            name="Default Account",
            api_key=api_key,
            api_secret=api_secret,
            mode=mode,
            strategies=[],
            enabled=True,
        )

    def handles_strategy(self, strategy_name: str) -> bool:
        """Check if this account handles the given strategy."""
        if not self.strategies:
            return True  # Empty list = handles all strategies
        return strategy_name in self.strategies
