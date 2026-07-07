import datetime
from typing import Dict, Optional
from pathlib import Path
from core.storage.meeting_storage import get_meeting_storage

_task_store: Dict[str, dict] = {}
_external_event_index: Dict[str, str] = {}


def create_task(task_id: str, status: str = "queued", metadata: Optional[dict] = None) -> None:
    _task_store[task_id] = {
        "status": status,
        "created_at": datetime.datetime.now().isoformat(),
        "result": None,
        "metadata": metadata or {},
    }
    external_event_id = (metadata or {}).get("external_event_id")
    if external_event_id:
        _external_event_index[external_event_id] = task_id


def update_task_status(task_id: str, status: str, result: Optional[dict] = None) -> None:
    if task_id in _task_store:
        _task_store[task_id]["status"] = status
        if result is not None:
            _task_store[task_id]["result"] = result


def get_task(task_id: str) -> Optional[dict]:
    return _task_store.get(task_id)


def list_tasks() -> list[dict]:
    return [
        {
            "task_id": task_id,
            **data,
        }
        for task_id, data in sorted(_task_store.items())
    ]


def get_task_by_external_event_id(external_event_id: str | None) -> Optional[dict]:
    if not external_event_id:
        return None
    task_id = _external_event_index.get(external_event_id)
    if not task_id:
        return None
    data = _task_store.get(task_id)
    if data is None:
        return None
    return {
        "task_id": task_id,
        **data,
    }

def get_meeting_dir(session_id: str) -> Path:
    recordings_dir = get_meeting_storage().recordings_dir
    candidates = sorted(recordings_dir.glob(f"*/*/*/*{session_id}*"))
    if candidates:
        return candidates[-1]
    raise FileNotFoundError(f"Meeting directory not found for {session_id}")
