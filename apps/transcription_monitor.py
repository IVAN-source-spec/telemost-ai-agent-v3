import asyncio
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from core.storage.meeting_storage import get_meeting_storage


load_dotenv()


storage = get_meeting_storage()


def read_status(status_file: Path) -> dict | None:
    if not status_file.exists():
        return None
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as error:
        print(f"[TranscriptionMonitor] Could not read {status_file}: {error}")
        return None


def write_status(status_file: Path, data: dict) -> None:
    status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_audio_files(recordings_dir: Path) -> list[Path]:
    return get_meeting_storage(recordings_dir.parent).find_recording_audio_files()


async def finalize_meeting_folder(meeting_dir: Path) -> None:
    uploaded = await asyncio.to_thread(storage.finalize_meeting_folder, meeting_dir)
    if uploaded:
        print(f"[TranscriptionMonitor] Meeting folder finalized: {meeting_dir}")


def ensure_status_for_audio(audio_path: Path) -> dict:
    status_file = audio_path.parent / "transcription_status.json"
    data = read_status(status_file)
    if data is not None:
        return data

    target_speakers = int(os.getenv("TRANSCRIPTION_DEFAULT_SPEAKERS", "1"))
    data = {
        "audio_path": str(audio_path),
        "job_id": None,
        "target_speakers": target_speakers,
        "status": "queued",
        "sent_at": None,
        "completed_at": None,
        "transcript_path": None,
        "error": None,
    }
    write_status(status_file, data)
    print(f"[TranscriptionMonitor] Queued new audio: {audio_path}")
    return data


def check_job_status(job_id: str, api_key: str) -> dict:
    url = f"https://api.pyannote.ai/v1/jobs/{job_id}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"status": "error", "error": str(error)}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def find_transcript_path(meeting_dir: Path, audio_path: Path) -> str | None:
    txt_files = sorted(meeting_dir.glob(f"{audio_path.stem}_pyannote_*.txt"))
    return str(txt_files[0]) if txt_files else None


async def submit_transcription(meeting_dir: Path, status_data: dict, api_key: str) -> None:
    status_file = meeting_dir / "transcription_status.json"
    audio_path = Path(status_data["audio_path"])
    target_speakers = int(status_data.get("target_speakers") or 1)

    data = read_status(status_file) or status_data
    data["status"] = "processing"
    data["sent_at"] = datetime.now(timezone.utc).isoformat()
    data["error"] = None
    write_status(status_file, data)

    print(f"[TranscriptionMonitor] Submitting transcription: {audio_path}")
    try:
        from core.transcription.adapter import TranscriptionAdapter

        adapter = TranscriptionAdapter(
            api_key=api_key,
            similarity_mode=os.getenv("TRANSCRIPTION_SIMILARITY_MODE", "local"),
        )
        job_id = await asyncio.to_thread(
            adapter.submit_job,
            str(audio_path),
            target_speakers,
            int(os.getenv("PYANNOTE_JOB_TIMEOUT_SECONDS", "14400")),
        )

        data = read_status(status_file) or status_data
        data["job_id"] = job_id
        data["status"] = "completed"
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
        data["transcript_path"] = find_transcript_path(meeting_dir, audio_path)
        data["error"] = None
        write_status(status_file, data)
        print(f"[TranscriptionMonitor] Completed {meeting_dir.name}, job_id={job_id}")
        await finalize_meeting_folder(meeting_dir)
    except Exception as error:
        data = read_status(status_file) or status_data
        data["status"] = "failed"
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
        data["error"] = str(error)
        write_status(status_file, data)
        print(f"[TranscriptionMonitor] Failed {meeting_dir.name}: {error}")


async def refresh_pending_status(meeting_dir: Path, status_data: dict, api_key: str) -> None:
    job_id = status_data.get("job_id")
    if not job_id:
        return

    result = await asyncio.to_thread(check_job_status, job_id, api_key)
    job_status = str(result.get("status", "unknown")).lower()
    status_file = meeting_dir / "transcription_status.json"

    if job_status in {"succeeded", "completed"}:
        data = read_status(status_file) or status_data
        audio_path = Path(data["audio_path"])
        data["status"] = "completed"
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
        data["transcript_path"] = find_transcript_path(meeting_dir, audio_path)
        data["error"] = None
        write_status(status_file, data)
        print(f"[TranscriptionMonitor] Status updated for {meeting_dir.name}")
        await finalize_meeting_folder(meeting_dir)
    elif job_status in {"failed", "error"}:
        data = read_status(status_file) or status_data
        data["status"] = "failed"
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
        data["error"] = result.get("error") or "Unknown error"
        write_status(status_file, data)
        print(f"[TranscriptionMonitor] Job failed for {meeting_dir.name}")


async def monitor_loop() -> None:
    api_key = os.getenv("PYANNOTE_API_KEY")
    if not api_key:
        print("[TranscriptionMonitor] PYANNOTE_API_KEY not set")
        return

    recordings_dir = Path.cwd() / "recordings"
    recordings_dir.mkdir(exist_ok=True)

    interval_seconds = int(os.getenv("TRANSCRIPTION_MONITOR_INTERVAL_SECONDS", "10"))
    max_parallel = int(os.getenv("TRANSCRIPTION_MONITOR_MAX_PARALLEL", "3"))
    semaphore = asyncio.Semaphore(max_parallel)
    running: dict[str, asyncio.Task] = {}

    print(f"[TranscriptionMonitor] Monitoring {recordings_dir}")
    print(f"[TranscriptionMonitor] Max parallel submissions: {max_parallel}")

    async def run_limited(meeting_dir: Path, status_data: dict) -> None:
        async with semaphore:
            await submit_transcription(meeting_dir, status_data, api_key)

    while True:
        for key, task in list(running.items()):
            if task.done():
                running.pop(key, None)
                try:
                    task.result()
                except Exception as error:
                    print(f"[TranscriptionMonitor] Background task failed: {error}")

        for audio_path in find_audio_files(recordings_dir):
            meeting_dir = audio_path.parent
            status_data = ensure_status_for_audio(audio_path)
            status = status_data.get("status")
            key = str(meeting_dir)

            if status == "queued" and key not in running:
                running[key] = asyncio.create_task(run_limited(meeting_dir, status_data))
            elif status in {"pending", "processing"} and key not in running:
                await refresh_pending_status(meeting_dir, status_data, api_key)

        await asyncio.sleep(interval_seconds)


def main() -> None:
    asyncio.run(monitor_loop())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[TranscriptionMonitor] Stopped")
