import re
from pathlib import Path
from core.storage.meeting_storage import get_meeting_storage
 
def get_next_meeting_number(recordings_dir: Path) -> int:
    """
    Сканирует папку recordings и возвращает следующий номер встречи.
    """
    recordings_dir.mkdir(parents=True, exist_ok=True)
    numbers = []
    for directory in recordings_dir.rglob("*"):
        if not directory.is_dir():
            continue
        name = directory.name
        match = re.search(r"meeting-(\d+)$", name)
        if not match:
            match = re.search(r"(?:^|__)meeting-(\d+)(?:__|$)", name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers) + 1 if numbers else 1
 
def generate_session_id() -> str:
    """
    Генерирует следующий session_id в формате meeting-<номер>.
    """
    next_num = get_meeting_storage().next_meeting_number_for_day()
    return f"meeting-{next_num}"
