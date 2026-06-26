def create_session_artifact(
    session_id: str,
    owner_repo: str,
    issued_at: str,
    secret_key: str,
    ttl_seconds: int = 300,
) -> dict[str, str]:
    return {
        "session_id": session_id,
        "owner_repo": owner_repo,
        "issued_at": issued_at,
        "expires_at": "2026-06-22T12:00:00Z",  # фиктивно
        "format": "portable-browser-session-v1",
        "signature": "dummy_signature",
    }