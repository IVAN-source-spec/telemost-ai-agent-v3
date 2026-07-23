import json
import os
from pathlib import Path


def _clean_name(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def load_snapshot_participant_names(snapshot_path: Path) -> list[str]:
    if not snapshot_path.exists():
        return []
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []

    if isinstance(data, dict):
        raw_snapshots = data.get("snapshots")
        snapshots = raw_snapshots if isinstance(raw_snapshots, list) else [data]
    elif isinstance(data, list):
        snapshots = data
    else:
        snapshots = []

    result: list[str] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        for name in snapshot.get("participants") or []:
            clean_name = _clean_name(name)
            if not clean_name:
                continue
            key = clean_name.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(clean_name)
    return result


def target_speakers_for_audio(audio_path: Path, fallback: int | None = None) -> int:
    audio_path = Path(audio_path)
    snapshot_count = len(load_snapshot_participant_names(audio_path.parent / "participants_snapshot.json"))
    if snapshot_count > 0:
        return snapshot_count

    meeting_time_path = audio_path.parent / "meeting_time.json"
    if meeting_time_path.exists():
        try:
            meeting_time = json.loads(meeting_time_path.read_text(encoding="utf-8-sig"))
            max_participants = int(meeting_time.get("max_participants") or 0)
            if max_participants > 0:
                return max_participants
        except Exception:
            pass

    if fallback is not None and fallback > 0:
        return int(fallback)

    try:
        default_value = int(os.getenv("TRANSCRIPTION_DEFAULT_SPEAKERS", "1"))
    except ValueError:
        default_value = 1
    return max(1, default_value)
