def build_dashboard_state(
    active_bots: int,
    active_meetings: int,
    blocked_tasks: int,
    reconnect_max_attempts: int,
    reconnect_interval_sec: int,
) -> dict[str, object]:
    return {
        "summary": {
            "active_bots": active_bots,
            "active_meetings": active_meetings,
            "blocked_tasks": blocked_tasks,
        },
        "reconnect_policy": {
            "max_attempts": reconnect_max_attempts,
            "interval_sec": reconnect_interval_sec,
        },
    }