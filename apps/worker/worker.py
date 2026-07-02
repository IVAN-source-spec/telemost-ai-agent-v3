import asyncio
import os
from pathlib import Path
from apps.api.dependencies import queue_publisher_instance
from apps.api.task_store import update_task_status
from core.browser_bot.client import TelemostBot
 
queue = queue_publisher_instance
 
async def process_task(task_data):
    session_id = task_data.session_id
    meeting_url = task_data.meeting_url
    print(f"[Worker] Processing task: {session_id} -> {meeting_url}")
 
    update_task_status(session_id, "running")
 
    try:
        # === ЗАПУСК БОТА ===
        bot_headless = os.getenv("TELEMOST_BOT_HEADLESS", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        bot = TelemostBot(headless=bot_headless)
        config = {
            "alone_leave_threshold": 20,  # 2 минуты
            "max_reconnect_attempts": 3,
            "reconnect_interval_sec": 10,
            "session_id": session_id,
        }
        await bot.run(meeting_url, config)
        meeting_time_result = {
            "meeting_started_at": config.get("meeting_started_at"),
            "meeting_ended_at": config.get("meeting_ended_at"),
            "meeting_duration_seconds": config.get("meeting_duration_seconds", 0),
            "meeting_duration_formatted": config.get("meeting_duration_formatted", "00:00:00"),
        }
 
        # === АСИНХРОННАЯ ТРАНСКРИБАЦИЯ ===
        audio_file = Path.cwd() / "recordings" / session_id / f"recording_{session_id}.wav"
 
        if audio_file.exists():
            print(f"[Worker] Audio file found, submitting transcription...")
 
            # Получаем число участников из конфига
            target_speakers = config.get("max_participants", 1)
            if target_speakers == 0:
                target_speakers = 1
            print(f"[Worker] Target speakers for transcription: {target_speakers}")
 
            api_key = os.getenv("PYANNOTE_API_KEY")
            if not api_key:
                print("[Worker] PYANNOTE_API_KEY not set, skipping transcription")
                update_task_status(session_id, "completed", result={
                    "message": "Meeting finished, transcription skipped (no API key)",
                    **meeting_time_result,
                })
            else:
                try:
                    from core.transcription.adapter import TranscriptionAdapter
                    from core.transcription.status_manager import TranscriptionStatusManager
 
                    adapter = TranscriptionAdapter(
                        api_key=api_key,
                        similarity_mode=os.getenv("TRANSCRIPTION_SIMILARITY_MODE", "local")
                    )
 
                    # Отправляем задачу на транскрипцию (не ждём результата!)
                    job_id = adapter.submit_job(
                        audio_path=str(audio_file),
                        target_speakers=target_speakers,
                        timeout_seconds=14400  # 4 часа
                    )
 
                    # Сохраняем статус в папке встречи
                    status_manager = TranscriptionStatusManager(audio_file.parent)
                    status_manager.create_status(
                        audio_path=str(audio_file),
                        job_id=job_id,
                        target_speakers=target_speakers
                    )
 
                    update_task_status(session_id, "completed", result={
                        "message": "Meeting finished, transcription pending",
                        "transcription_job_id": job_id,
                        "status": "pending",
                        **meeting_time_result,
                    })
                    print(f"[Worker] Transcription submitted for {session_id}, job_id: {job_id}")
 
                except Exception as e:
                    print(f"[Worker] Failed to submit transcription: {e}")
                    update_task_status(session_id, "completed", result={
                        "message": "Meeting finished, transcription submission failed",
                        "error": str(e),
                        **meeting_time_result,
                    })
        else:
            update_task_status(session_id, "completed", result={
                "message": "Meeting finished successfully (no audio)",
                **meeting_time_result,
            })
            print(f"[Worker] Task {session_id} completed (no audio)")
 
    except Exception as e:
        # Любая ошибка на этапе выполнения бота
        update_task_status(session_id, "failed", result={"error": str(e)})
        print(f"[Worker] Task {session_id} failed: {e}")
 
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
