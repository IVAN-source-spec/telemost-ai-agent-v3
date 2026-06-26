from collections.abc import Mapping
from .decision_engine import RuntimeActionName, RuntimeConfig, RuntimeState, decide_action
from ..browser_bot.join_guest import build_guest_join_result
from ..browser_bot.join_mode import resolve_join_mode
from .timer_overlay import build_overlay_state

def build_runtime_snapshot(
    payload: Mapping[str, object] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    data = dict(payload or {})
    data.update(kwargs)

    meeting_url = str(data.get("meeting_url", "https://telemost.yandex.ru"))
    authorized_available = bool(data.get("authorized_available", False))
    auth_ok = bool(data.get("auth_ok", False))
    elapsed_seconds = int(data.get("elapsed_seconds", 0))
    participants = int(data.get("participants", 1))
    disconnected = bool(data.get("disconnected", False))
    attempt = int(data.get("attempt", 0))
    max_attempts = int(data.get("max_attempts", 3))
    interval_sec = int(data.get("interval_sec", 10))
    alone_for_seconds = int(data.get("alone_for_seconds", 0))
    dry_run = bool(data.get("dry_run", False))

    action = decide_action(
        RuntimeState(participants, alone_for_seconds, attempt),
        RuntimeConfig(300, max_attempts, interval_sec),
        disconnected,
    )
    decision_payload = {
        "action": action.name.value,
        "leave": action.name in (RuntimeActionName.LEAVE, RuntimeActionName.GIVE_UP),
        "reconnect_in_sec": action.delay_seconds,
    }
    return {
        "dry_run": dry_run,
        "join_mode": resolve_join_mode(authorized_available, auth_ok),
        "guest_join_defaults": build_guest_join_result(meeting_url),
        "timer_overlay": build_overlay_state(elapsed_seconds),
        "decision": decision_payload,
    }