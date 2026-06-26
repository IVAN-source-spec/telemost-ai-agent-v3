from core.orchestrator.contracts import IdleBotSelector, BotState


class MemoryBotSelector(IdleBotSelector):
    async def select_idle_bot(self) -> BotState | None:
        return BotState(bot_id="test-bot-1")