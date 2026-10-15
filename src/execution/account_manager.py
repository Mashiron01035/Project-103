import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.core.account_config import AccountConfig
from src.execution.connectors.bybit_connector import BybitConnector

logger = logging.getLogger(__name__)


class AccountManager:
    def __init__(self, config_path: str = "config/accounts.yaml"):
        self.config_path = Path(config_path)
        self.accounts: Dict[str, AccountConfig] = {}
        self.connectors: Dict[str, BybitConnector] = {}
        self.default_account: str = "default"

    async def load_accounts(self) -> bool:
        if not self.config_path.exists():
            logger.warning(f"accounts.yaml not found at {self.config_path}")
            return False

        with open(self.config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        accounts_data = data.get("accounts", {})
        self.default_account = data.get("default_account", "main")

        for account_id, account_data in accounts_data.items():
            self.accounts[account_id] = AccountConfig.from_yaml(account_id, account_data)
            logger.info(f"Loaded account: {account_id}, ({self.accounts[account_id].name})")

        return len(self.accounts) > 0

    def load_legacy_account(self, bybit_config, risk_config):
        acc = AccountConfig.from_legacy_config(bybit_config, risk_config)
        self.accounts["default"] = acc
        self.default_account = "default"
        logger.info("Loaded legacy account as 'default'")

    async def connect_account(self, account_id: str) -> bool:
        if account_id not in self.accounts:
            logger.error(f"Account not found: {account_id}")
            return False

        acc = self.accounts[account_id]
        if not acc.enabled:
            logger.info(f"Account {account_id} is disabled, skipping")
            return False

        if not acc.api_key or not acc.api_secret:
            logger.warning(f"Account {account_id} has no credentials, skipping")
            return False

        connector = BybitConnector(
            api_key=acc.api_key,
            api_secret=acc.api_secret,
            testnet=(acc.mode == "testnet"),
            demo=(acc.mode == "demo"),
            account_name=acc.name,
        )

        if await connector.connect():
            self.connectors[account_id] = connector
            logger.info(f"Connected account: {account_id} ({acc.mode})")
            return True

        logger.error(f"Failed to connect to account: {account_id}")
        return False

    async def connect_all(self) -> int:
        connected = 0
        for account_id in self.accounts:
            if await self.connect_account(account_id):
                connected += 1
        return connected

    def get_account_for_strategy(self, strategy_name: str) -> Optional[str]:
        for account_id, acc in self.accounts.items():
            if acc.enabled and acc.handles_strategy(strategy_name):
                return account_id
        return self.default_account

    def get_connector(self, account_id: str) -> Optional[BybitConnector]:
        return self.connectors.get(account_id)

    async def get_all_balances(self) -> Dict[str, Dict[str, float]]:
        result = {}
        for account_id, connector in self.connectors.items():
            try:
                result[account_id] = await connector.get_balance()
            except Exception as e:
                logger.error(f"Failed to get balance for {account_id}: {e}")
                result[account_id] = {"error": str(e)}
        return result

    async def get_all_positions(self) -> Dict[str, List[Dict]]:
        result = {}
        for account_id, connector in self.connectors.items():
            try:
                result[account_id] = await connector.get_positions()
            except Exception as e:
                logger.error(f"Failed to get positions for {account_id}: {e}")
                result[account_id] = {"error": str(e)}
        return result

    async def disconnect_all(self):
        for account_id, connector in self.connectors.items():
            try:
                await connector.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting {account_id}: {e}")
        self.connectors.clear()

    def list_accounts(self) -> List[Dict[str, Any]]:
        """List all accounts with their status."""
        result = []
        for account_id, acc in self.accounts.items():
            result.append(
                {
                    "id": account_id,
                    "name": acc.name,
                    "mode": acc.mode,
                    "enabled": acc.enabled,
                    "connected": account_id in self.connectors,
                    "strategies": acc.strategies if acc.strategies else ["all"],
                }
            )
        return result
