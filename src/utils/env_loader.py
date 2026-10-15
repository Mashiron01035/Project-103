"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : env_loader
    Project     : Project-103
    Description : Environment variables loader utility. Safely loads and validates environment variables from .env files.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EnvLoader:
    """Environment variables loader with validation"""

    def __init__(self, env_file: str = ".env", required_vars: Optional[List[str]] = None):
        """
        Initialize environment loader

        Args:
            env_file: Path to .env file
            required_vars: List of required environment variables
        """
        self.env_file = Path(env_file)
        self.required_vars = required_vars or []
        self.loaded_vars: Dict[str, str] = {}
        self.missing_vars: List[str] = []

    def load(self, override: bool = False) -> Dict[str, str]:
        """
        Load environment variables from .env file

        Args:
            override: Whether to override existing environment variables

        Returns:
            Dict[str, str]: Loaded variables
        """
        if not self.env_file.exists():
            logger.warning(f"Environment file not found: {self.env_file}")
            return {}

        try:
            with open(self.env_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            for line_num, line in enumerate(lines, 1):
                # Skip comments and empty lines
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Parse key=value
                if "=" not in line:
                    logger.warning(f"Invalid line {line_num} in {self.env_file}: {line}")
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                # Remove quotes from value
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]

                # Set environment variable
                if override or key not in os.environ:
                    os.environ[key] = value
                    self.loaded_vars[key] = value

            logger.info(
                f"Loaded {len(self.loaded_vars)} environment variables from {self.env_file}"
            )
            return self.loaded_vars

        except Exception as e:
            logger.error(f"Failed to load environment file: {e}")
            return {}

    def validate(self) -> bool:
        """
        Validate that all required variables are present

        Returns:
            bool: True if all required variables are present
        """
        self.missing_vars = []

        for var in self.required_vars:
            if var not in os.environ:
                self.missing_vars.append(var)
                logger.error(f"Required environment variable missing: {var}")

        if self.missing_vars:
            logger.error(f"Missing {len(self.missing_vars)} required environment variables")
            return False

        logger.info("All required environment variables are present")
        return True

    def get(self, key: str, default: Any = None, required: bool = False) -> Optional[str]:
        """
        Get environment variable value

        Args:
            key: Variable name
            default: Default value if not found
            required: Whether variable is required

        Returns:
            Optional[str]: Variable value or default
        """
        value = os.getenv(key, default)

        if required and value is None:
            logger.error(f"Required environment variable not found: {key}")
            raise ValueError(f"Required environment variable not found: {key}")

        return value

    def get_int(self, key: str, default: int = 0) -> int:
        """Get environment variable as integer"""
        value = os.getenv(key)
        if value is None:
            return default

        try:
            return int(value)
        except ValueError:
            logger.warning(f"Invalid integer value for {key}: {value}, using default: {default}")
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get environment variable as float"""
        value = os.getenv(key)
        if value is None:
            return default

        try:
            return float(value)
        except ValueError:
            logger.warning(f"Invalid float value for {key}: {value}, using default: {default}")
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get environment variable as boolean"""
        value = os.getenv(key)
        if value is None:
            return default

        return value.lower() in ["true", "1", "yes", "on"]

    def get_list(
        self, key: str, separator: str = ",", default: Optional[List[str]] = None
    ) -> List[str]:
        """Get environment variable as list"""
        value = os.getenv(key)
        if value is None:
            return default or []

        return [item.strip() for item in value.split(separator) if item.strip()]

    def get_dict(self, key: str, default: Optional[Dict] = None) -> Dict:
        """Get environment variable as dictionary (from JSON string)"""
        import json

        value = os.getenv(key)
        if value is None:
            return default or {}

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON value for {key}: {value}, using default")
            return default or {}

    def print_summary(self):
        """Print summary of loaded environment variables"""
        print("\n" + "=" * 60)
        print("Environment Variables Summary")
        print("=" * 60)
        print(f"Loaded from: {self.env_file}")
        print(f"Total loaded: {len(self.loaded_vars)}")
        print(f"Required variables: {len(self.required_vars)}")
        print(f"Missing variables: {len(self.missing_vars)}")

        if self.missing_vars:
            print("\nMissing required variables:")
            for var in self.missing_vars:
                print(f"  - {var}")

        print("\nLoaded variables:")
        for key in sorted(self.loaded_vars.keys()):
            # Mask sensitive values
            if any(
                sensitive in key.lower() for sensitive in ["key", "secret", "password", "token"]
            ):
                masked_value = "*" * 8
            else:
                masked_value = self.loaded_vars[key][:50]

            print(f"  {key} = {masked_value}")

        print("=" * 60 + "\n")


def load_env(
    env_file: str = ".env",
    required_vars: Optional[List[str]] = None,
    validate: bool = True,
    override: bool = False,
) -> EnvLoader:
    """
    Convenience function to load environment variables

    Args:
        env_file: Path to .env file
        required_vars: List of required environment variables
        validate: Whether to validate required variables
        override: Whether to override existing environment variables

    Returns:
        EnvLoader: Loader instance
    """
    loader = EnvLoader(env_file, required_vars)
    loader.load(override=override)

    if validate and required_vars:
        if not loader.validate():
            logger.warning("Some required environment variables are missing")

    return loader


# Predefined required variables for different components
# NOTE: Only secrets should be required here!
# Settings like ENVIRONMENT, MODE, LOG_LEVEL are in config/config.yaml

REQUIRED_VARS_BYBIT = ["BYBIT_API_KEY", "BYBIT_API_SECRET"]

REQUIRED_VARS_BYBIT_TESTNET = ["BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET"]

REQUIRED_VARS_BINANCE = ["BINANCE_API_KEY", "BINANCE_API_SECRET"]

REQUIRED_VARS_MT5 = ["MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"]

REQUIRED_VARS_TELEGRAM = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]

REQUIRED_VARS_DATABASE = ["DATABASE_URL"]

# Minimal required vars - just database for persistence
REQUIRED_VARS_MINIMAL = ["DATABASE_URL"]


def load_env_for_trading(exchange: str = "bybit", testnet: bool = False) -> EnvLoader:
    """
    Load environment variables for trading

    Args:
        exchange: Exchange name (bybit, mt5, binance, mock)
        testnet: Whether to use testnet credentials

    Returns:
        EnvLoader: Configured loader
    """
    required_vars = []

    if exchange.lower() == "bybit":
        if testnet:
            required_vars.extend(REQUIRED_VARS_BYBIT_TESTNET)
        else:
            required_vars.extend(REQUIRED_VARS_BYBIT)
    elif exchange.lower() == "binance":
        required_vars.extend(REQUIRED_VARS_BINANCE)
    elif exchange.lower() == "mt5":
        required_vars.extend(REQUIRED_VARS_MT5)
    elif exchange.lower() == "mock":
        # Mock exchange doesn't need any credentials
        pass

    project_root = Path(__file__).resolve().parent.parent.parent
    env_file = project_root / ".env"
    # validate=False because caller (main.py) will call validate() explicitly
    return load_env(env_file=str(env_file), required_vars=required_vars, validate=False)


def load_env_with_monitoring() -> EnvLoader:
    """Load environment variables with monitoring support"""
    required_vars = REQUIRED_VARS_MINIMAL.copy()
    required_vars.extend(REQUIRED_VARS_TELEGRAM)

    return load_env(required_vars=required_vars, validate=False)


# Example usage
if __name__ == "__main__":
    # Basic usage
    loader = load_env()
    loader.print_summary()

    # Get specific values
    api_key = loader.get("BYBIT_API_KEY", required=False)
    debug = loader.get_bool("DEBUG", default=False)
    pairs = loader.get_list("ACTIVE_PAIRS", default=["BTCUSDT"])

    print(f"\nDebug mode: {debug}")
    print(f"Active pairs: {pairs}")
