def plan_reconnect(
    previous_participants: int,
    attempt: int,
    max_attempts: int,
    interval_sec: int,
) -> dict[str, object]:
    if previous_participants <= 1:
        return {"action": "leave", "reason": "no_other_participants"}
    if attempt < max_attempts:
        return {"action": "reconnect", "delay_sec": interval_sec}
    return {"action": "leave", "reason": "retries_exhausted"}