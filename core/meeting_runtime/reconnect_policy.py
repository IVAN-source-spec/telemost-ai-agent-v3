def next_retry(attempt: int, max_attempts: int, interval_sec: int) -> int | None:
    if attempt >= max_attempts:
        return None
    return interval_sec