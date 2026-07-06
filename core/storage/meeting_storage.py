import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


try:
    ASTRAKHAN_TZ = ZoneInfo("Europe/Astrakhan")
except ZoneInfoNotFoundError:
    ASTRAKHAN_TZ = timezone(timedelta(hours=4), name="Europe/Astrakhan")
DEFAULT_RECORDINGS_DIR = "recordings"
RECORDING_FILE_NAME = "recording_meeting.wav"


@dataclass(frozen=True)
class MeetingArtifacts:
    session_id: str
    title: str
    started_at_utc: datetime
    started_at_local: datetime
    meeting_dir: Path
    audio_path: Path
    meeting_time_path: Path
    transcription_status_path: Path


class MeetingStorage:
    def __init__(self, root_dir: Path | None = None):
        self.root_dir = root_dir or Path.cwd()
        self.recordings_dir = self.root_dir / DEFAULT_RECORDINGS_DIR

    def sanitize_title(self, title: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = cleaned.rstrip(". ")
        if not cleaned:
            return "meeting"
        return cleaned[:90].rstrip(". ")

    def prepare_meeting(
        self,
        *,
        session_id: str,
        title: str | None,
        started_at_utc: datetime,
    ) -> MeetingArtifacts:
        if started_at_utc.tzinfo is None:
            started_at_utc = started_at_utc.replace(tzinfo=timezone.utc)
        started_at_utc = started_at_utc.astimezone(timezone.utc)
        started_at_local = started_at_utc.astimezone(ASTRAKHAN_TZ)

        raw_title = title.strip() if title and title.strip() else session_id
        safe_title = self.sanitize_title(raw_title)
        local_time = started_at_local.strftime("%H-%M")

        if safe_title == session_id:
            folder_name = f"{session_id}__{local_time}"
        else:
            folder_name = f"{safe_title}__{session_id}__{local_time}"

        meeting_dir = (
            self.recordings_dir
            / started_at_local.strftime("%Y")
            / started_at_local.strftime("%m")
            / started_at_local.strftime("%d")
            / folder_name
        )
        meeting_dir.mkdir(parents=True, exist_ok=True)

        return MeetingArtifacts(
            session_id=session_id,
            title=safe_title,
            started_at_utc=started_at_utc,
            started_at_local=started_at_local,
            meeting_dir=meeting_dir,
            audio_path=meeting_dir / RECORDING_FILE_NAME,
            meeting_time_path=meeting_dir / "meeting_time.json",
            transcription_status_path=meeting_dir / "transcription_status.json",
        )

    def find_recording_audio_files(self) -> list[Path]:
        if not self.recordings_dir.exists():
            return []
        return sorted(self.recordings_dir.glob("*/*/*/*/" + RECORDING_FILE_NAME))

    def resolve_retry_audio_path(self, value: str) -> Path:
        raw_path = Path(value).expanduser()
        if raw_path.is_file():
            return raw_path.resolve()
        if raw_path.is_dir():
            audio_path = raw_path.resolve() / RECORDING_FILE_NAME
            if audio_path.exists():
                return audio_path
            raise FileNotFoundError(f"Recording not found: {audio_path}")

        candidates = sorted(
            path
            for path in self.recordings_dir.glob(f"*/*/*/*{value}*/{RECORDING_FILE_NAME}")
            if path.is_file()
        )
        if not candidates:
            raise FileNotFoundError(f"Recording not found for meeting: {value}")
        if len(candidates) > 1:
            formatted = "\n".join(str(path) for path in candidates)
            raise RuntimeError(f"Multiple recordings found for {value}:\n{formatted}")
        return candidates[0].resolve()


def get_meeting_storage(root_dir: Path | None = None) -> MeetingStorage:
    return MeetingStorage(root_dir=root_dir)
