def choose_transcription_result(
    local_result: str | None,
    local_error: bool,
    cloud_result: str | None,
) -> dict[str, str]:
    if not local_error and local_result:
        return {"source": "local", "text": local_result}
    if cloud_result:
        return {"source": "cloud", "text": cloud_result}
    raise ValueError("No transcription available")