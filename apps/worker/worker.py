import asyncio
import os
from pathlib import Path

from apps.api.dependencies import bot_selector_instance, queue_publisher_instance
from apps.api.task_store import update_task_status
from core.browser_bot.client import TelemostBot


queue = queue_publisher_instance


async def process_task(task_data):
    session_id = task_data.session_id
    bot_id = task_data.bot_id
    meeting_url = task_data.meeting_url
    title = getattr(task_data, "title", None)
    agenda = getattr(task_data, "agenda", None)
    expected_participants = getattr(task_data, "expected_participants", None)
    source = getattr(task_data, "source", None)
    external_event_id = getattr(task_data, "external_event_id", None)
    scheduled_start_at = getattr(task_data, "scheduled_start_at", None)
    scheduled_end_at = getattr(task_data, "scheduled_end_at", None)
    organizer = getattr(task_data, "organizer", None)
    print(f"[Worker] Processing task: {session_id} ({bot_id}) -> {meeting_url}")

    update_task_status(session_id, "running")

    try:
        bot_headless = os.getenv("TELEMOST_BOT_HEADLESS", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        bot = TelemostBot(headless=bot_headless, bot_id=bot_id)
        config = {
            "alone_leave_threshold": int(os.getenv("TELEMOST_ALONE_LEAVE_THRESHOLD_SECONDS", "20")),
            "reconnect_enabled": os.getenv("TELEMOST_RECONNECT_ENABLED", "1").lower() in (
                "1",
                "true",
                "yes",
            ),
            "max_reconnect_attempts": int(os.getenv("TELEMOST_RECONNECT_MAX_ATTEMPTS", "5")),
            "reconnect_interval_sec": int(os.getenv("TELEMOST_RECONNECT_DELAY_SECONDS", "10")),
            "reconnect_total_limit_seconds": int(os.getenv("TELEMOST_RECONNECT_TOTAL_LIMIT_SECONDS", "300")),
            "reconnect_lost_checks": int(os.getenv("TELEMOST_RECONNECT_LOST_CHECKS", "2")),
            "session_id": session_id,
            "title": title,
            "agenda": agenda,
            "expected_participants": expected_participants,
            "source": source,
            "external_event_id": external_event_id,
            "scheduled_start_at": scheduled_start_at,
            "scheduled_end_at": scheduled_end_at,
            "organizer": organizer,
        }
        await bot.run(meeting_url, config)

        meeting_time_result = {
            "meeting_started_at": config.get("meeting_started_at"),
            "meeting_ended_at": config.get("meeting_ended_at"),
            "meeting_duration_seconds": config.get("meeting_duration_seconds", 0),
            "meeting_duration_formatted": config.get("meeting_duration_formatted", "00:00:00"),
            "meeting_dir": config.get("meeting_dir"),
            "audio_path": config.get("audio_path"),
            "source": source,
            "agenda": agenda,
            "expected_participants": expected_participants,
            "external_event_id": external_event_id,
            "scheduled_start_at": scheduled_start_at,
            "scheduled_end_at": scheduled_end_at,
            "organizer": organizer,
            "reconnects": config.get("reconnects", []),
        }

        audio_file = Path(config["audio_path"]) if config.get("audio_path") else None
        if audio_file and audio_file.exists():
            target_speakers = config.get("max_participants", 1)
            if target_speakers == 0:
                target_speakers = 1

            from core.transcription.status_manager import TranscriptionStatusManager

            status_manager = TranscriptionStatusManager(audio_file.parent)
            status_manager.create_status(
                audio_path=str(audio_file),
                job_id=None,
                target_speakers=target_speakers,
                status="queued",
            )
            update_task_status(session_id, "completed", result={
                "message": "Meeting finished, transcription queued",
                "status": "queued",
                **meeting_time_result,
            })
            print(f"[Worker] Transcription queued for {session_id}")
        else:
            update_task_status(session_id, "completed", result={
                "message": "Meeting finished successfully (no audio)",
                **meeting_time_result,
            })
            print(f"[Worker] Task {session_id} completed (no audio)")

    except Exception as e:
        update_task_status(session_id, "failed", result={"error": str(e)})
        print(f"[Worker] Task {session_id} failed: {e}")
    finally:
        await bot_selector_instance.release_bot(bot_id)
        print(f"[Worker] Released bot: {bot_id}")


async def worker_loop():
    while True:
        task = await queue.pop("meetings")
        if task:
            asyncio.create_task(process_task(task))
        else:
            await asyncio.sleep(0.1)


async def start_worker():
    try:
        await worker_loop()
    except asyncio.CancelledError:
        print("[Worker] Shutting down gracefully...")
