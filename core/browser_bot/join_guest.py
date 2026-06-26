def build_guest_join_result(
        meeting_url: str,
) -> dict[str, object]:
    return {
        "meeting_url": meeting_url,
        "joined": True,
        "mic_muted": True,
        "mode": "guest",
    }