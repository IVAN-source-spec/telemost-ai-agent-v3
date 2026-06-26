def should_leave(alone_for_seconds: int, threshold_seconds: int = 300) -> bool:
    return alone_for_seconds > threshold_seconds