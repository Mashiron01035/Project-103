import logging
from typing import Any, Dict

from src.execution.account_manager import AccountManager
from src.models.signal import Signal

logger = logging.getLogger(__name__)


class SignalRouter:
    def __init__(self, account_manager: AccountManager):
        self.account_manager = account_manager
        self.signal_lifecycles: Dict[str, Any] = {}

    def set_lifecycle(self, account_id: str, lifecycle) -> None:
        self.signal_lifecycles[account_id] = lifecycle

    async def route_signal(self, signal: Signal) -> bool:
        strategy_name = getattr(signal, "strategy_name", "") or ""
        account_id = self.account_manager.get_account_for_strategy(strategy_name)

        if not account_id:
            logger.warning(f"No account found for strategy: {strategy_name}")
            return False

        lifecycle = self.signal_lifecycles.get(account_id)
        if not lifecycle:
            logger.error(f"No lifecycle manager for account: {account_id}")
            return False

        acc = self.account_manager.accounts.get(account_id)
        acc_name = acc.name if acc else account_id

        logger.info(f"Routing {signal.symbol} ({strategy_name}) -> {acc_name}")
        return await lifecycle.process_signal(signal)

    async def route_to_account(self, signal: Signal, account_id: str) -> bool:
        lifecycle = self.signal_lifecycles.get(account_id)
        if not lifecycle:
            logger.error(f"No lifecycle manager for account: {account_id}")
            return False
        return await lifecycle.process_signal(signal)

    def get_lifecycle(self, account_id: str):
        return self.signal_lifecycles.get(account_id)

    def get_all_stats(self) -> Dict[str, Dict]:
        result = {}
        for account_id, lifecycle in self.signal_lifecycles.items():
            if hasattr(lifecycle, "get_stats"):
                result[account_id] = lifecycle.get_stats()
        return result
