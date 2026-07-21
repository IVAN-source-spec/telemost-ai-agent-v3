import json
import re
from datetime import datetime, timezone
from pathlib import Path


class AgendaTracker:
    AGENDA_BLOCK_RE = re.compile(
        r"###\s*(?:\u041f\u043e\u0432\u0435\u0441\u0442\u043a\u0430\s*:)?(?P<body>.*?)###",
        re.IGNORECASE | re.DOTALL,
    )
    ITEM_MARKER_RE = re.compile(r"#\s*(?P<number>\d+)\s*\.")

    def __init__(self, raw_agenda: str, meeting_dir: str | Path, meeting_started_at: datetime, logger=print, bot_id: str = "unknown"):
        self.raw_agenda = raw_agenda or ""
        self.meeting_dir = Path(meeting_dir)
        self.meeting_started_at = meeting_started_at
        self.logger = logger
        self.bot_id = bot_id
        self.items = self.parse_agenda(self.raw_agenda)
        self.current_index = 0 if self.items else None
        self.started = False
        self.path = self.meeting_dir / "meeting_agenda.json"

    @classmethod
    def parse_agenda(cls, raw_agenda: str) -> list[dict]:
        raw = (raw_agenda or "").strip()
        if not raw:
            return []
        match = cls.AGENDA_BLOCK_RE.search(raw)
        body = match.group("body") if match else raw
        body = body.strip()
        if body.lower().startswith("\u043f\u043e\u0432\u0435\u0441\u0442\u043a\u0430:"):
            body = body.split(":", 1)[1].strip()

        markers = list(cls.ITEM_MARKER_RE.finditer(body))
        items = []
        for position, marker in enumerate(markers):
            start = marker.end()
            end = markers[position + 1].start() if position + 1 < len(markers) else len(body)
            title = " ".join(body[start:end].strip().split())
            if not title:
                continue
            try:
                number = int(marker.group("number"))
            except ValueError:
                number = len(items) + 1
            items.append({
                "index": len(items) + 1,
                "number": number,
                "title": title,
                "started_at": None,
                "ended_at": None,
                "duration_seconds": None,
            })
        return items

    @property
    def enabled(self) -> bool:
        return bool(self.items)

    def start(self) -> None:
        if not self.enabled or self.started:
            return
        self.started = True
        self.items[0]["started_at"] = datetime.now(timezone.utc).isoformat()
        self._write()
        self.logger(f"[Bot] Agenda tracker started: {len(self.items)} items")

    def finish(self) -> None:
        if not self.enabled or not self.started or self.current_index is None:
            return
        self._finish_current_item()
        self._write()

    def next_question(self) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        if not self.started:
            self.start()
        if self.current_index is None:
            return {"status": "completed", "message": "Agenda is already completed"}

        self._finish_current_item()
        next_index = self.current_index + 1
        if next_index >= len(self.items):
            self.current_index = None
            self._write()
            return {"status": "completed", "message": "Agenda completed"}

        self.current_index = next_index
        self.items[self.current_index]["started_at"] = datetime.now(timezone.utc).isoformat()
        self.items[self.current_index]["ended_at"] = None
        self.items[self.current_index]["duration_seconds"] = None
        self._write()
        current = self.items[self.current_index]
        return {
            "status": "switched",
            "index": current["index"],
            "total": len(self.items),
            "title": current["title"],
            "message": f"Agenda item {current['index']}/{len(self.items)}: {current['title']}",
        }

    def overlay_state(self) -> dict:
        if not self.enabled or self.current_index is None:
            return {"agendaEnabled": False}
        current = self.items[self.current_index]
        started_at = current.get("started_at")
        if not started_at:
            return {"agendaEnabled": False}
        try:
            started = datetime.fromisoformat(started_at)
            start_ms = int(started.timestamp() * 1000)
        except Exception:
            start_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return {
            "agendaEnabled": True,
            "agendaTitle": current["title"],
            "agendaIndex": current["index"],
            "agendaTotal": len(self.items),
            "agendaQuestionStartTimeMs": start_ms,
        }

    def _finish_current_item(self) -> None:
        if self.current_index is None:
            return
        current = self.items[self.current_index]
        if current.get("ended_at"):
            return
        ended = datetime.now(timezone.utc)
        current["ended_at"] = ended.isoformat()
        started_at = current.get("started_at")
        if started_at:
            try:
                started = datetime.fromisoformat(started_at)
                current["duration_seconds"] = max(0, int((ended - started).total_seconds()))
            except Exception:
                current["duration_seconds"] = None

    def _write(self) -> None:
        self.meeting_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": self.raw_agenda,
            "current_index": None if self.current_index is None else self.current_index + 1,
            "total": len(self.items),
            "items": self.items,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
