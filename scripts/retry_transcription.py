import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.storage.meeting_storage import get_meeting_storage
from core.transcription.speaker_count import target_speakers_for_audio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Queue transcription retry for an existing Telemost recording via remote service."
    )
    parser.add_argument(
        "meeting",
        help=(
            "Meeting id like meeting-5, path to a meeting directory, "
            "or path to recording_meeting.wav."
        ),
    )
    parser.add_argument(
        "--speakers",
        type=int,
        default=None,
        help="Override target speaker count. By default it is derived from participant snapshots.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Move existing *_pyannote_* files to old_transcription_results before retrying.",
    )
    return parser


def resolve_audio_path(value: str) -> Path:
    return get_meeting_storage(PROJECT_ROOT).resolve_retry_audio_path(value)


def move_existing_results(audio_path: Path) -> None:
    existing = sorted(audio_path.parent.glob(f"{audio_path.stem}_pyannote_*"))
    if not existing:
        return

    archive_dir = audio_path.parent / "old_transcription_results"
    archive_dir.mkdir(exist_ok=True)
    for path in existing:
        target = archive_dir / path.name
        counter = 1
        while target.exists():
            target = archive_dir / f"{path.stem}_{counter}{path.suffix}"
            counter += 1
        path.rename(target)
        print(f"[RetryTranscription] Moved existing result: {path.name} -> {target}")


def queue_retry(audio_path: Path, speakers: int | None) -> Path:
    status_file = audio_path.parent / "transcription_status.json"
    target_speakers = speakers or target_speakers_for_audio(audio_path, fallback=1)
    payload = {
        "audio_path": str(audio_path),
        "job_id": None,
        "target_speakers": target_speakers,
        "status": "queued",
        "transcription_backend": "remote",
        "sent_at": None,
        "completed_at": None,
        "transcript_path": None,
        "error": None,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "queued_by": "scripts/retry_transcription.py",
    }
    status_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return status_file


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(PROJECT_ROOT / ".env")

    audio_path = resolve_audio_path(args.meeting)
    print(f"[RetryTranscription] Audio: {audio_path}")

    if args.force:
        move_existing_results(audio_path)

    status_file = queue_retry(audio_path, args.speakers)
    print(f"[RetryTranscription] Queued remote transcription: {status_file}")

    result_files = sorted(glob.glob(str(audio_path.parent / f"{audio_path.stem}_pyannote_*")))
    if result_files:
        print("[RetryTranscription] Existing result files:")
        for path in result_files:
            print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
