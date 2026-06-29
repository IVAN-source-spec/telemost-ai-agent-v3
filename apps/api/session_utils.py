import re
from pathlib import Path
 
def get_next_meeting_number(recordings_dir: Path) -> int:
    """
    Сканирует папку recordings и возвращает следующий номер встречи.
    """
    recordings_dir.mkdir(parents=True, exist_ok=True)
    existing = [d.name for d in recordings_dir.iterdir() if d.is_dir() and d.name.startswith("meeting-")]
    if not existing:
        return 1
 
    numbers = []
    for name in existing:
        match = re.search(r"meeting-(\d+)$", name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers) + 1 if numbers else 1
 
def generate_session_id() -> str:
    """
    Генерирует следующий session_id в формате meeting-<номер>.
    """
    recordings_dir = Path.cwd() / "recordings"
    next_num = get_next_meeting_number(recordings_dir)
    return f"meeting-{next_num}"