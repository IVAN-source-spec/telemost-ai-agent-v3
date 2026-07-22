import json
import re
from datetime import datetime, timezone
from pathlib import Path


class ParticipantsSummaryBuilder:
    CONFIDENTIAL_DIR_NAME = "\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f \u0447\u0430\u0441\u0442\u044c"

    def __init__(self, meeting_dir: Path, expected_participants: str | list[str] | None = None, logger=print):
        self.meeting_dir = Path(meeting_dir)
        self.expected_participants_raw = expected_participants
        self.logger = logger
        self.output_path = self.meeting_dir / "participants_all.json"

    def build(self) -> Path | None:
        if not self.meeting_dir.exists():
            return None

        participants_by_key: dict[str, dict] = {}
        source_summaries = []
        for source_name, path in self._snapshot_sources():
            snapshots = self._load_snapshots(path)
            source_names = []
            source_seen = set()
            for snapshot in snapshots:
                meeting_time = snapshot.get("meeting_time")
                meeting_time_seconds = snapshot.get("meeting_time_seconds")
                for name in snapshot.get("participants") or []:
                    clean_name = self._clean_name(name)
                    if not clean_name:
                        continue
                    key = clean_name.lower()
                    entry = participants_by_key.setdefault(
                        key,
                        {
                            "name": clean_name,
                            "sources": [],
                            "first_seen": None,
                            "last_seen": None,
                        },
                    )
                    if source_name not in entry["sources"]:
                        entry["sources"].append(source_name)
                    seen_record = {
                        "source": source_name,
                        "meeting_time": meeting_time,
                        "meeting_time_seconds": meeting_time_seconds,
                    }
                    if entry["first_seen"] is None:
                        entry["first_seen"] = seen_record
                    entry["last_seen"] = seen_record
                    if key not in source_seen:
                        source_seen.add(key)
                        source_names.append(clean_name)

            source_summaries.append(
                {
                    "source": source_name,
                    "path": str(path),
                    "exists": path.exists(),
                    "snapshots": len(snapshots),
                    "participants_count": len(source_names),
                    "participants": source_names,
                }
            )

        participants = sorted(participants_by_key.values(), key=lambda item: item["name"].lower())
        attendance = self._build_attendance(participants)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "meeting_dir": str(self.meeting_dir),
            "participants_count": len(participants),
            "participants": participants,
            "attendance": attendance,
            "sources": source_summaries,
        }
        self.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger(f"[Bot] Participants summary saved: {len(participants)} participant(s), {self.output_path}")
        return self.output_path


    def _build_attendance(self, participants: list[dict]) -> dict:
        actual_names = [item["name"] for item in participants if self._clean_name(item.get("name"))]
        expected_names = self._parse_expected_participants(self.expected_participants_raw)
        actual_by_key = {self._name_key(name): name for name in actual_names}
        expected_by_key = {self._name_key(name): name for name in expected_names}

        matched = [expected_by_key[key] for key in expected_by_key if key in actual_by_key]
        missing = [expected_by_key[key] for key in expected_by_key if key not in actual_by_key]
        unexpected = [actual_by_key[key] for key in actual_by_key if key not in expected_by_key]

        return {
            "actual_participants": actual_names,
            "expected_participants": expected_names,
            "matched_participants": matched,
            "missing_participants": missing,
            "unexpected_participants": unexpected,
        }

    def _parse_expected_participants(self, value: str | list[str] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = re.split(r"[,\n\r]+", str(value))
        result = []
        seen = set()
        for item in raw_items:
            name = self._clean_name(item)
            if not name:
                continue
            key = self._name_key(name)
            if key in seen:
                continue
            seen.add(key)
            result.append(name)
        return result

    def _name_key(self, value) -> str:
        return self._clean_name(value).casefold()

    def _snapshot_sources(self) -> list[tuple[str, Path]]:
        main_path = self.meeting_dir / "participants_snapshot.json"
        sources: list[tuple[str, Path]] = [("main", main_path)]
        seen_paths = {main_path.resolve() if main_path.exists() else main_path.absolute()}

        expected_confidential_path = self.meeting_dir / self.CONFIDENTIAL_DIR_NAME / "participants_snapshot.json"
        self._append_source_once(sources, seen_paths, "confidential", expected_confidential_path)

        for nested_path in sorted(self.meeting_dir.glob("*/participants_snapshot.json")):
            self._append_source_once(sources, seen_paths, "confidential", nested_path)
        return sources

    def _append_source_once(
        self,
        sources: list[tuple[str, Path]],
        seen_paths: set[Path],
        source_name: str,
        path: Path,
    ) -> None:
        path_key = path.resolve() if path.exists() else path.absolute()
        if path_key in seen_paths:
            return
        seen_paths.add(path_key)
        sources.append((source_name, path))

    def _load_snapshots(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            self.logger(f"[Bot] Could not read participants snapshot {path}: {error}")
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            if isinstance(data.get("snapshots"), list):
                return [item for item in data["snapshots"] if isinstance(item, dict)]
            return [data]
        return []

    def _clean_name(self, value) -> str:
        return " ".join(str(value or "").split()).strip()
