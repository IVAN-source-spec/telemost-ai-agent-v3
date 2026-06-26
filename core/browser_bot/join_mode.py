def resolve_join_mode(
        authorized_available: bool,
        auth_ok: bool
) -> dict[str, str]:
    if not authorized_available:
        return {"mode": "guest", "reason": "auth_unavailable"}
    if auth_ok:
        return {"mode": "authorized"}
    return {"mode": "guest", "reason": "auth_failed"}