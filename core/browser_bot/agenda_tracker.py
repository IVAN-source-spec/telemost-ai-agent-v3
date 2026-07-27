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
        self.planned_time_mode = bool(self.items) and any(item.get("planned_seconds") is not None for item in self.items)
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

        items = []
        for parsed in parsed_items:
            title = parsed["planned_title"] if parsed["planned_ok"] else parsed["raw_title"]
            planned_seconds = parsed["planned_seconds"] if parsed["planned_ok"] else None
            items.append(cls._new_item(
                index=len(items) + 1,
                number=parsed["number"],
                title=title,
                raw_title=parsed["raw_title"],
                planned_seconds=planned_seconds,
            ))
        return items

    @staticmethod
    def _new_item(index: int, number: int, title: str, raw_title: str, planned_seconds: int | None) -> dict:
        return {
            "index": index,
            "number": number,
            "title": title,
            "raw_title": raw_title,
            "planned_seconds": planned_seconds,
            "started_at": None,
            "ended_at": None,
            "duration_seconds": None,
            "actual_seconds": 0,
            "segments": [],
            "status": "not_started",
            "locked": False,
            "locked_at": None,
            "skipped": False,
            "skipped_at": None,
            "skip_reason": None,
        }

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
        if target.get("skipped"):
            return {"status": "skipped", "number": number, "title": target["title"], "message": f"Agenda item #{number} is skipped"}
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

    def agenda_items_without_time(self) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        return {
            "status": "items",
            "kind": "without_time",
            "items": [self._public_item(item) for item in self.items if not item.get("locked") and item.get("planned_seconds") is None],
            "total": len(self.items),
        }

    def unfinished_questions(self) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        return {
            "status": "items",
            "kind": "unfinished",
            "items": [self._public_item(item) for item in self.items if not item.get("locked")],
            "total": len(self.items),
        }

    def all_questions(self) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        return {
            "status": "items",
            "kind": "all",
            "items": [self._public_item(item) for item in self.items],
            "total": len(self.items),
        }

    def assign_question_time(self, number: int, raw_time: str) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        target_index = self._index_by_number(number)
        if target_index is None:
            return {"status": "not_found", "number": number, "message": f"Agenda item #{number} was not found"}
        item = self.items[target_index]
        if item.get("locked"):
            return {"status": "locked", "number": number, "title": item["title"], "message": f"Agenda item #{number} is already closed"}
        planned_seconds = self._parse_planned_seconds(raw_time)
        if planned_seconds is None:
            return {"status": "invalid_time", "number": number, "raw_time": raw_time}
        item["planned_seconds"] = planned_seconds
        item["planned_time_assigned_at"] = self._now().isoformat()
        item["planned_time_source"] = "chat_command"
        self.planned_time_mode = any(question.get("planned_seconds") is not None for question in self.items)
        if item.get("locked"):
            self._set_completed_status(item)
        self._write()
        return {
            "status": "time_assigned",
            "number": number,
            "index": item["index"],
            "total": len(self.items),
            "title": item["title"],
            "planned_seconds": planned_seconds,
            "planned_time": self._format_duration(planned_seconds),
        }

    def add_question(self, raw_question: str) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        raw_title = " ".join((raw_question or "").strip().split())
        if not raw_title:
            return {"status": "invalid_question", "message": "Question text is empty"}
        title, planned_seconds, planned_ok = self._split_title_and_planned_seconds(raw_title)
        next_number = max((int(item.get("number") or item.get("index") or 0) for item in self.items), default=0) + 1
        item = self._new_item(
            index=len(self.items) + 1,
            number=next_number,
            title=title if planned_ok else raw_title,
            raw_title=raw_title,
            planned_seconds=planned_seconds if planned_ok else None,
        )
        item["added_at"] = self._now().isoformat()
        item["added_by"] = "chat_command"
        self.items.append(item)
        self.completed = False
        self.planned_time_mode = any(question.get("planned_seconds") is not None for question in self.items)
        self._write()
        return {
            "status": "question_added",
            "number": item["number"],
            "index": item["index"],
            "total": len(self.items),
            "title": item["title"],
            "planned_seconds": item.get("planned_seconds"),
            "planned_time": self._format_duration(item.get("planned_seconds")),
        }

    def skip_current_question(self) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        if not self.started:
            self.start()
        if self.current_index is None:
            return self._completed_or_waiting_result()
        skipped_index = self.current_index
        skipped = self._skip_item(skipped_index)
        next_index = self._find_next_unlocked_after(skipped_index)
        if next_index is None:
            self.current_index = None
            self.completed = self._all_items_locked()
            self._write()
            if self.completed:
                return {"status": "skipped_completed", "skipped": self._public_item(skipped), "message": "Agenda completed"}
            return {"status": "skipped_no_next", "skipped": self._public_item(skipped)}
        self._start_segment(next_index)
        self._write()
        result = self._switched_result(self.items[next_index])
        result["status"] = "skipped_and_switched"
        result["skipped"] = self._public_item(skipped)
        return result

    def skip_question(self, number: int) -> dict:
        if not self.enabled:
            return {"status": "disabled", "message": "Agenda is not configured"}
        if not self.started:
            self.start()
        target_index = self._index_by_number(number)
        if target_index is None:
            return {"status": "not_found", "number": number, "message": f"Agenda item #{number} was not found"}
        target = self.items[target_index]
        if target.get("skipped"):
            return {"status": "already_skipped", "number": number, "title": target["title"]}
        if target.get("locked"):
            return {"status": "locked", "number": number, "title": target["title"], "message": f"Agenda item #{number} is already closed"}
        was_active = self.current_index == target_index
        skipped = self._skip_item(target_index)
        if was_active:
            next_index = self._find_next_unlocked_after(target_index)
            if next_index is None:
                self.current_index = None
                self.completed = self._all_items_locked()
                self._write()
                if self.completed:
                    return {"status": "skipped_completed", "skipped": self._public_item(skipped), "message": "Agenda completed"}
                return {"status": "skipped_no_next", "skipped": self._public_item(skipped)}
            self._start_segment(next_index)
            self._write()
            result = self._switched_result(self.items[next_index])
            result["status"] = "skipped_and_switched"
            result["skipped"] = self._public_item(skipped)
            return result
        self._write()
        return {"status": "question_skipped", "skipped": self._public_item(skipped)}


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

    def _public_item(self, item: dict) -> dict:
        return {
            "index": item.get("index"),
            "number": item.get("number"),
            "title": item.get("title"),
            "status": item.get("status"),
            "locked": bool(item.get("locked")),
            "skipped": bool(item.get("skipped")),
            "planned_seconds": item.get("planned_seconds"),
            "planned_time": self._format_duration(item.get("planned_seconds")),
            "actual_seconds": item.get("actual_seconds"),
            "actual_time": self._format_duration(item.get("actual_seconds")),
        }

    @staticmethod
    def _format_duration(seconds: int | None) -> str | None:
        if seconds is None:
            return None
        safe_seconds = max(0, int(seconds))
        hours = safe_seconds // 3600
        minutes = (safe_seconds % 3600) // 60
        remaining_seconds = safe_seconds % 60
        if hours:
            return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
        return f"{minutes}:{remaining_seconds:02d}"

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

    def _skip_item(self, index: int) -> dict:
        item = self.items[index]
        if self.current_index == index:
            self._close_current_segment(lock=False)
        now = self._now().isoformat()
        item["locked"] = True
        item["locked_at"] = now
        item["skipped"] = True
        item["skipped_at"] = now
        item["skip_reason"] = "participant_command"
        item["status"] = "skipped_by_participant"
        if self.current_index == index:
            self.current_index = None
        return item

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
