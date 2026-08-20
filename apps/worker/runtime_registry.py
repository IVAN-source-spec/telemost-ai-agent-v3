from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ActiveBotRecord:
    bot_id: str
    session_id: str
    bot: Any


_active_bots: dict[str, ActiveBotRecord] = {}


def register_active_bot(bot_id: str, session_id: str, bot: Any) -> None:
    _active_bots[bot_id] = ActiveBotRecord(bot_id=bot_id, session_id=session_id, bot=bot)


def unregister_active_bot(bot_id: str, bot: Any | None = None) -> None:
    record = _active_bots.get(bot_id)
    if record is None:
        return
    if bot is not None and record.bot is not bot:
        return
    _active_bots.pop(bot_id, None)


def get_active_bot(bot_id: str) -> ActiveBotRecord | None:
    return _active_bots.get(bot_id)
