import asyncio
import os
from datetime import datetime, timezone
from core.orchestrator.contracts import IdleBotSelector, BotState


class MemoryBotSelector(IdleBotSelector):
    def __init__(self, pool_size: int | None = None):
        size = pool_size or int(os.getenv("TELEMOST_BOT_POOL_SIZE", "3"))
        self._bots = [BotState(bot_id=f"test-bot-{index}") for index in range(1, size + 1)]
        self._busy_bot_ids: set[str] = set()
        self._assignments: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def select_idle_bot(self) -> BotState | None:
        async with self._lock:
            for bot in self._bots:
                if bot.bot_id not in self._busy_bot_ids:
                    self._busy_bot_ids.add(bot.bot_id)
                    return bot
        return None

    async def select_bot(self, bot_id: str) -> BotState | None:
        async with self._lock:
            for bot in self._bots:
                if bot.bot_id == bot_id and bot.bot_id not in self._busy_bot_ids:
                    self._busy_bot_ids.add(bot.bot_id)
                    return bot
        return None

    async def release_bot(self, bot_id: str) -> None:
        async with self._lock:
            self._busy_bot_ids.discard(bot_id)
            self._assignments.pop(bot_id, None)

    async def assign_session(
        self,
        bot_id: str,
        session_id: str,
        meeting_url: str,
        title: str | None = None,
    ) -> None:
        async with self._lock:
            self._busy_bot_ids.add(bot_id)
            self._assignments[bot_id] = {
                "session_id": session_id,
                "title": title or session_id,
                "meeting_url": meeting_url,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }

    async def list_bots(self) -> list[dict]:
        async with self._lock:
            bots = []
            for bot in self._bots:
                assignment = self._assignments.get(bot.bot_id, {})
                is_busy = bot.bot_id in self._busy_bot_ids
                bots.append(
                    {
                        "bot_id": bot.bot_id,
                        "status": "busy" if is_busy else "idle",
                        "session_id": assignment.get("session_id"),
                        "title": assignment.get("title"),
                        "meeting_url": assignment.get("meeting_url"),
                        "started_at": assignment.get("started_at"),
                    }
                )
            return bots
