import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
 
load_dotenv()
 
def get_pending_transcriptions(recordings_dir: Path) -> list[tuple[Path, dict]]:
    """Находит все встречи с pending транскрипцией."""
    pending = []
    for meeting_dir in recordings_dir.iterdir():
        if not meeting_dir.is_dir():
            continue
        status_file = meeting_dir / "transcription_status.json"
        if not status_file.exists():
            continue
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            if data.get("status") in ["pending", "processing"]:
                pending.append((meeting_dir, data))
        except:
            continue
    return pending
 
def check_job_status(job_id: str, api_key: str) -> dict:
    """Проверяет статус задачи в pyannote API."""
    url = f"https://api.pyannote.ai/v1/jobs/{job_id}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}
 
def main():
    load_dotenv()
    api_key = os.getenv("PYANNOTE_API_KEY")
    if not api_key:
        print("❌ PYANNOTE_API_KEY not set")
        return
 
    recordings_dir = Path.cwd() / "recordings"
    print(f"🔍 Monitoring {recordings_dir} for pending transcriptions...")
 
    while True:
        pending = get_pending_transcriptions(recordings_dir)
        if pending:
            print(f"[{datetime.now().isoformat()}] Found {len(pending)} pending transcription(s)")
 
        for meeting_dir, status_data in pending:
            job_id = status_data.get("job_id")
            if not job_id:
                continue
 
            print(f"  📊 Checking job {job_id}...")
            result = check_job_status(job_id, api_key)
 
            job_status = result.get("status", "unknown")
 
            if job_status in ["succeeded", "completed"]:
                print(f"  ✅ Job {job_id} completed!")
                status_file = meeting_dir / "transcription_status.json"
                data = json.loads(status_file.read_text(encoding="utf-8"))
                data["status"] = "completed"
                data["completed_at"] = datetime.now(timezone.utc).isoformat()
                # Ищем файл транскрипции


                audio_path_obj = Path(status_data['audio_path'])
                txt_files = list(meeting_dir.glob(f"{audio_path_obj.stem}_pyannote_*.txt"))
                if txt_files:
                    data["transcript_path"] = str(txt_files[0])
                status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  ✅ Status updated for {meeting_dir.name}")
 
            elif job_status in ["failed", "error"]:
                print(f"  ❌ Job {job_id} failed!")
                status_file = meeting_dir / "transcription_status.json"
                data = json.loads(status_file.read_text(encoding="utf-8"))
                data["status"] = "failed"
                data["error"] = result.get("error") or "Unknown error"
                data["completed_at"] = datetime.now(timezone.utc).isoformat()
                status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
 
        time.sleep(30)  # Проверяем каждые 30 секунд
 
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Monitor stopped")