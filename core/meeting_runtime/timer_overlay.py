def _format_hhmmss(
        total_seconds: int,
) -> str:
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_overlay_state(elapsed_seconds: int) -> dict[str, str]:
    safe_elapsed = max(0, elapsed_seconds)
    return {
        "title": "с начала встречи прошло",
        "time": _format_hhmmss(safe_elapsed),
    }