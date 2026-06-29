import datetime
from typing import Dict, Optional
from pathlib import Path

_task_store: Dict[str, dict] = {}


def create_task(task_id: str, status: str = "queued") -> None:
    _task_store[task_id] = {
        "status": status,
        "created_at": datetime.datetime.now().isoformat(),
        "result": None,
    }


def update_task_status(task_id: str, status: str, result: Optional[dict] = None) -> None:
    if task_id in _task_store:
        _task_store[task_id]["status"] = status
        if result is not None:
            _task_store[task_id]["result"] = result


def get_task(task_id: str) -> Optional[dict]:
    return _task_store.get(task_id)

def get_meeting_dir(session_id: str) -> Path:
    recordings_dir = Path.cwd() / "recordings"
    meeting_dir = recordings_dir / session_id
    meeting_dir.mkdir(parents=True, exist_ok=True)
    return meeting_dir