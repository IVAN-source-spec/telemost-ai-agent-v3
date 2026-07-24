import json
import re
from datetime import datetime, timezone
from pathlib import Path


class AgendaTracker:
    AGENDA_BLOCK_RE = re.compile(
        r"###\s*(?:Повестка\s*:)?(?P<body>.*?)###",
        re.IGNORECASE | re.DOTALL,
    )
    ITEM_MARKER_RE = re.compile(r"#\s*(?P<number>\d+)\s*\.")
    PLANNED_TIME_SEPARATOR = " - "

    def __init__(self, raw_agenda: str, meeting_dir: str | Path, meeting_started_at: datetime, logger=print, bot_id: str = "unknown"):
        self.raw_agenda = raw_agenda or ""
        self.meeting_dir = Path(meeting_dir)
        self.meeting_started_at = meeting_started_at
        self.logger = logger
        self.bot_id = bot_id
        self.items = self.parse_agenda(self.raw_agenda)
        self.current_index = 0 if self.items else None
        self.started = False
        self.planned_time_mode = bool(self.items) and all(item.get("planned_seconds") is not None for item in self.items)
        self.completed = False
        self.path = self.meeting_dir / "meeting_agenda.json"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _parse_planned_seconds(value: str) -> int | None:
        text = " ".join((value or "").strip().split())
        if not text:
            return None
        parts = text.split(":")
        if len(parts) > 3 or any(not part.isdigit() for part in parts):
            return None
        numbers = [int(part) for part in parts]
        if len(numbers) == 1:
            return numbers[0]
        if len(numbers) == 2:
            minutes, seconds = numbers
            if seconds >= 60:
                return None
            return minutes * 60 + seconds
        hours, minutes, seconds = numbers
        if minutes >= 60 or seconds >= 60:
            return None
        return hours * 3600 + minutes * 60 + seconds

    @classmethod
    def _split_title_and_planned_seconds(cls, title: str) -> tuple[str, int | None, bool]:
        if cls.PLANNED_TIME_SEPARATOR not in title:
            return title, None, False
        name, raw_time = title.rsplit(cls.PLANNED_TIME_SEPARATOR, 1)
        clean_name = " ".join(name.strip().split())
        planned_seconds = cls._parse_planned_seconds(raw_time)
        if not clean_name or planned_seconds is None:
            return title, None, False
        return clean_name, planned_seconds, True

    @classmethod
    def parse_agenda(cls, raw_agenda: str) -> list[dict]:
        raw = (raw_agenda or "").strip()
        if not raw:
            return []
        match = cls.AGENDA_BLOCK_RE.search(raw)
        body = match.group("body") if match else raw
        body = body.strip()
        if body.lower().startswith("повестка:"):
            body = body.split(":", 1)[1].strip()

        markers = list(cls.ITEM_MARKER_RE.finditer(body))
        parsed_items = []
        for position, marker in enumerate(markers):
            start = marker.end()
            end = markers[position + 1].start() if position + 1 < len(markers) else len(body)
            raw_title = " ".join(body[start:end].strip().split())
            if not raw_title:
                continue
            try:
                number = int(marker.group("number"))
            except ValueError:
                number = len(parsed_items) + 1
            planned_title, planned_seconds, planned_ok = cls._split_title_and_planned_seconds(raw_title)
            parsed_items.append({
                "number": number,
                "raw_title": raw_title,
                "planned_title": planned_title,
                "planned_seconds": planned_seconds,
                "planned_ok": planned_ok,
            })

        if not parsed_items:
            return []

        use_planned_time = all(item["planned_ok"] for item in parsed_items)
        items = []
        for parsed in parsed_items:
            title = parsed["planned_title"] if use_planned_time else parsed["raw_title"]
            planned_seconds = parsed["planned_seconds"] if use_planned_time else None
            items.append({
                "index": len(items) + 1,
                "number": parsed["number"],
                "title": title,
                "raw_title": parsed["raw_title"],
                "planned_seconds": planned_seconds,
                "started_at": None,
                "ended_at": None,
                "duration_seconds": None,
                "actual_seconds": 0,
                "segments": [],
                "status": "not_started",
                "locked": False,
                "locked_at": None,
            })
        return items

    @property
    def enabled(self) -> bool:
        return bool(self.items)

    def start(self) -> None:
        if not self.enabled or self.started:
            return
        self.started = True
        self.completed = False
        self._start_segment(0)
        self._write()
        mode = "planned countdown" if self.planned_time_mode else "elapsed timer"
        self.logger(f"[Bot] Agenda tracker started: {len(self.items)} items, mode={mode}")

    def finish(self) -> None:
        if not self.enabled or not self.started:
            return
        if self.current_index is not None:
            self._close_current_segment(lock=True)
            self.current_index = None
        self.completed = self._all_items_locked()
        self._write()

    def next_question(self) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        if not self.started:
            self.start()
        if self.current_index is None:
            return self._completed_or_waiting_result()

        closed = self._close_current_segment(lock=True)
        next_index = self._find_next_unlocked_after(closed)
        if next_index is None:
            self.current_index = None
            self.completed = self._all_items_locked()
            self._write()
            if self.completed:
                return {"status": "completed", "message": "Agenda completed"}
            return {"status": "no_next", "message": "No next unlocked agenda item"}

        self._start_segment(next_index)
        self._write()
        return self._switched_result(self.items[next_index])

    def switch_to_question(self, number: int) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        if not self.started:
            self.start()

        target_index = self._index_by_number(number)
        if target_index is None:
            return {"status": "not_found", "number": number, "message": f"Agenda item #{number} was not found"}
        target = self.items[target_index]
        if target.get("locked"):
            return {"status": "locked", "number": number, "title": target["title"], "message": f"Agenda item #{number} is already closed"}
        if self.current_index == target_index:
            return {"status": "already_active", "index": target["index"], "total": len(self.items), "title": target["title"]}

        if self.current_index is not None:
            self._close_current_segment(lock=False)
        self._start_segment(target_index)
        self.completed = False
        self._write()
        return self._switched_result(target)

    def end_question(self) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        if not self.started:
            self.start()
        if self.current_index is None:
            return self._completed_or_waiting_result()

        closed = self._close_current_segment(lock=True)
        next_index = self._find_next_unlocked_after(closed)
        if next_index is None:
            self.current_index = None
            self.completed = self._all_items_locked()
            self._write()
            if self.completed:
                return {"status": "completed", "message": "Agenda completed"}
            return {"status": "closed", "message": "Agenda item closed; no next unlocked item"}

        self._start_segment(next_index)
        self._write()
        result = self._switched_result(self.items[next_index])
        result["closed_previous"] = True
        return result

    def overlay_state(self) -> dict:
        if not self.enabled or self.current_index is None:
            return {"agendaEnabled": False, "agendaCompleted": self.completed}
        current = self.items[self.current_index]
        active = self._active_segment(current)
        started_at = active.get("started_at") if active else current.get("started_at")
        if not started_at:
            return {"agendaEnabled": False, "agendaCompleted": self.completed}
        started = self._parse_dt(started_at) or self._now()
        start_ms = int(started.timestamp() * 1000)
        return {
            "agendaEnabled": True,
            "agendaTitle": current["title"],
            "agendaIndex": current["index"],
            "agendaTotal": len(self.items),
            "agendaQuestionStartTimeMs": start_ms,
            "agendaAccumulatedSeconds": int(current.get("actual_seconds") or 0),
            "agendaCountdownEnabled": current.get("planned_seconds") is not None,
            "agendaPlannedSeconds": current.get("planned_seconds"),
            "agendaCompleted": False,
        }

    def _completed_or_waiting_result(self) -> dict:
        if self._all_items_locked() or self.completed:
            self.completed = True
            return {"status": "completed", "message": "Agenda is already completed"}
        return {"status": "inactive", "message": "Agenda has no active item"}

    def _switched_result(self, current: dict) -> dict:
        return {
            "status": "switched",
            "index": current["index"],
            "number": current["number"],
            "total": len(self.items),
            "title": current["title"],
            "message": f"Agenda item {current['index']}/{len(self.items)}: {current['title']}",
        }

    def _index_by_number(self, number: int) -> int | None:
        for index, item in enumerate(self.items):
            if int(item.get("number") or item.get("index") or 0) == number:
                return index
        return None

    def _find_next_unlocked_after(self, index: int) -> int | None:
        for next_index in range(index + 1, len(self.items)):
            if not self.items[next_index].get("locked"):
                return next_index
        for next_index in range(0, min(index + 1, len(self.items))):
            if not self.items[next_index].get("locked"):
                return next_index
        return None

    def _all_items_locked(self) -> bool:
        return bool(self.items) and all(item.get("locked") for item in self.items)

    @staticmethod
    def _active_segment(item: dict) -> dict | None:
        segments = item.get("segments") or []
        if not segments:
            return None
        latest = segments[-1]
        if latest.get("ended_at"):
            return None
        return latest

    def _start_segment(self, index: int) -> None:
        now = self._now().isoformat()
        item = self.items[index]
        item["segments"].append({
            "started_at": now,
            "ended_at": None,
            "duration_seconds": None,
        })
        item["started_at"] = item.get("started_at") or now
        item["ended_at"] = None
        item["status"] = "in_progress"
        self.current_index = index
        self.completed = False

    def _close_current_segment(self, lock: bool) -> int:
        if self.current_index is None:
            return -1
        index = self.current_index
        current = self.items[index]
        active = self._active_segment(current)
        ended = self._now()
        if active is not None:
            active["ended_at"] = ended.isoformat()
            started = self._parse_dt(active.get("started_at"))
            if started:
                active["duration_seconds"] = max(0, int((ended - started).total_seconds()))
        self._recalculate_item(current)
        current["ended_at"] = ended.isoformat()
        if lock:
            current["locked"] = True
            current["locked_at"] = ended.isoformat()
            self._set_completed_status(current)
        else:
            current["status"] = "paused"
        return index

    @staticmethod
    def _recalculate_item(item: dict) -> None:
        total = 0
        for segment in item.get("segments") or []:
            duration = segment.get("duration_seconds")
            if duration is not None:
                total += int(duration)
        item["actual_seconds"] = total
        item["duration_seconds"] = total

    @staticmethod
    def _set_completed_status(item: dict) -> None:
        planned_seconds = item.get("planned_seconds")
        actual_seconds = item.get("actual_seconds")
        if planned_seconds is None:
            item["status"] = "completed_without_plan"
        elif actual_seconds is None:
            item["status"] = "completed_unknown"
        elif int(actual_seconds) <= int(planned_seconds):
            item["status"] = "within_time"
        else:
            item["status"] = "over_time"

    def _write(self) -> None:
        self.meeting_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": self.raw_agenda,
            "planned_time_mode": self.planned_time_mode,
            "completed": self.completed,
            "current_index": None if self.current_index is None else self.current_index + 1,
            "total": len(self.items),
            "items": self.items,
            "updated_at": self._now().isoformat(),
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
