def evaluate_recording_start(
        mode: str,
) -> dict[str, str|bool]:
    if mode == "authorized":
        return {
            "can_start": True,
            "target": "yandex_disk",
        }
    if mode == "guest":
        return {
            "can_start": False,
            "reason": "requires_authorized_mode",
        }
    raise ValueError(f"Unknown recording mode: {mode}")