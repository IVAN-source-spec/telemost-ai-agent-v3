import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
 
class TranscriptionStatusManager:
    def __init__(self, meeting_dir: Path):
        self.meeting_dir = meeting_dir
        self.status_file = meeting_dir / "transcription_status.json"
 
    def create_status(self, audio_path: str, job_id: str, target_speakers: int = 1) -> dict:
        status = {
            "audio_path": audio_path,
            "job_id": job_id,
            "target_speakers": target_speakers,
            "status": "pending",
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "transcript_path": None,
            "error": None
        }
        self.status_file.write_text(json.dumps(status, ensure_ascii=True, indent=2), encoding='utf-8')
        return status
 
    def update_status(self, job_id: str, status: str, transcript_path: Optional[str] = None, error: Optional[str] = None) -> dict | None:
        if not self.status_file.exists():
            return None
        data = json.loads(self.status_file.read_text(encoding='utf-8'))
        if data.get("job_id") != job_id:
            return None
 
        data["status"] = status
        if status in ["completed", "failed"]:
            data["completed_at"] = datetime.now(timezone.utc).isoformat()
        if transcript_path:
            data["transcript_path"] = transcript_path
        if error:
            data["error"] = error
 
        self.status_file.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding='utf-8')
        return data
 
    def get_status(self) -> Optional[dict]:
        if not self.status_file.exists():
            return None
        return json.loads(self.status_file.read_text(encoding='utf-8'))