import asyncio
import os
from core.orchestrator.contracts import IdleBotSelector, BotState


class MemoryBotSelector(IdleBotSelector):
    def __init__(self, pool_size: int | None = None):
        size = pool_size or int(os.getenv("TELEMOST_BOT_POOL_SIZE", "3"))
        self._bots = [BotState(bot_id=f"test-bot-{index}") for index in range(1, size + 1)]
        self._busy_bot_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def select_idle_bot(self) -> BotState | None:
        async with self._lock:
            for bot in self._bots:
                if bot.bot_id not in self._busy_bot_ids:
                    self._busy_bot_ids.add(bot.bot_id)
                    return bot
        return None

    async def release_bot(self, bot_id: str) -> None:
        async with self._lock:
            self._busy_bot_ids.discard(bot_id)
