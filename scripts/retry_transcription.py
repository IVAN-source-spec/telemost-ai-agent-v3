import argparse
import glob
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.transcription.adapter import TranscriptionAdapter
from core.storage.meeting_storage import get_meeting_storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retry transcription for an existing Telemost recording."
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
        default=1,
        help="Fixed number of speakers for pyannote.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=14400,
        help="Overall transcription timeout.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Move existing *_pyannote_* files to old_pyannote_results before retrying.",
    )
    return parser


def resolve_audio_path(value: str) -> Path:
    return get_meeting_storage(PROJECT_ROOT).resolve_retry_audio_path(value)


def move_existing_results(audio_path: Path) -> None:
    existing = sorted(audio_path.parent.glob(f"{audio_path.stem}_pyannote_*"))
    if not existing:
        return

    archive_dir = audio_path.parent / "old_pyannote_results"
    archive_dir.mkdir(exist_ok=True)
    for path in existing:
        target = archive_dir / path.name
        counter = 1
        while target.exists():
            target = archive_dir / f"{path.stem}_{counter}{path.suffix}"
            counter += 1
        path.rename(target)
        print(f"[RetryTranscription] Moved existing result: {path.name} -> {target}")


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("PYANNOTE_API_KEY")
    if not api_key:
        raise SystemExit("PYANNOTE_API_KEY is not set. Add it to .env first.")

    audio_path = resolve_audio_path(args.meeting)
    print(f"[RetryTranscription] Audio: {audio_path}")

    if args.force:
        move_existing_results(audio_path)

    adapter = TranscriptionAdapter(
        api_key=api_key,
        similarity_mode=os.getenv("TRANSCRIPTION_SIMILARITY_MODE", "local"),
    )
    job_id = adapter.submit_job(
        audio_path=str(audio_path),
        target_speakers=args.speakers,
        timeout_seconds=args.timeout_seconds,
    )
    print(f"[RetryTranscription] job_id: {job_id}")

    result_files = sorted(glob.glob(str(audio_path.parent / f"{audio_path.stem}_pyannote_*")))
    if result_files:
        print("[RetryTranscription] Result files:")
        for path in result_files:
            print(f"  - {path}")
    else:
        print("[RetryTranscription] No result files found.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
