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
        bot = TelemostBot(headless=False)
        config = {
            "alone_leave_threshold": 20,  # 2 минуты
            "max_reconnect_attempts": 3,
            "reconnect_interval_sec": 10,
            "session_id": session_id,
        }
        await bot.run(meeting_url, config)
 
        # === ТРАНСКРИБАЦИЯ ===
        recordings_dir = Path.cwd() / "recordings"
        audio_file = recordings_dir / f"recording_{session_id}.wav"
 
        if audio_file.exists():
            print(f"[Worker] Audio file found, starting transcription...")
 
            api_key = os.getenv("PYANNOTE_API_KEY")
            if not api_key:
                print("[Worker] PYANNOTE_API_KEY not set, skipping transcription")
                update_task_status(session_id, "completed", result={
                    "message": "Meeting finished, transcription skipped (no API key)"
                })
            else:
                try:
                    from core.transcription.adapter import TranscriptionAdapter
                    adapter = TranscriptionAdapter(
                        api_key=api_key,
                        similarity_mode=os.getenv("TRANSCRIPTION_SIMILARITY_MODE", "local")
                    )
                    result = adapter.transcribe(
                        audio_path=str(audio_file),
                        target_speakers=2,  # можно передавать из config
                    )
 
                    # Читаем стабилизированный транскрипт
                    transcript_path = result.get("stabilized_transcript_path")
                    if transcript_path:
                        with open(transcript_path, 'r', encoding='utf-8') as f:
                            transcript_text = f.read()
                    else:
                        transcript_text = "Transcription completed, but no text found"
 
                    update_task_status(session_id, "completed", result={
                        "message": "Meeting finished with transcription",
                        "transcription": transcript_text,
                        "pipeline_report": result,
                    })
                    print(f"[Worker] Transcription completed for {session_id}")
 
                except Exception as e:
                    print(f"[Worker] Transcription failed: {e}")
                    update_task_status(session_id, "completed", result={
                        "message": "Meeting finished, transcription failed",
                        "error": str(e)
                    })
        else:
            update_task_status(session_id, "completed", result={"message": "Meeting finished successfully"})
            print(f"[Worker] Task {session_id} completed")
 
    except Exception as e:
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


# import asyncio
# from apps.api.dependencies import queue_publisher_instance
# from apps.api.task_store import update_task_status
# from core.browser_bot.client import TelemostBot

# queue = queue_publisher_instance


# async def process_task(task_data):
#     session_id = task_data.session_id
#     meeting_url = task_data.meeting_url
#     print(f"[Worker] Processing task: {session_id} -> {meeting_url}")

#     update_task_status(session_id, "running")
#     try:
#         bot = TelemostBot(headless=False)
#         config = {
#             "alone_leave_threshold": 20,  # 2 минуты
#             "max_reconnect_attempts": 3,
#             "reconnect_interval_sec": 10,
#             "session_id": session_id,  # передаём для имени файла
#         }
#         await bot.run(meeting_url, config)
#         update_task_status(session_id, "completed", result={"message": "Meeting finished successfully"})
#         print(f"[Worker] Task {session_id} completed")
#     except Exception as e:
#         update_task_status(session_id, "failed", result={"error": str(e)})
#         print(f"[Worker] Task {session_id} failed: {e}")


# async def worker_loop():
#     while True:
#         task = await queue.pop("meetings")
#         if task:
#             asyncio.create_task(process_task(task))
#         else:
#             await asyncio.sleep(0.1)


# async def start_worker():
#     try:
#         await worker_loop()
#     except asyncio.CancelledError:
#         print("[Worker] Shutting down gracefully...")