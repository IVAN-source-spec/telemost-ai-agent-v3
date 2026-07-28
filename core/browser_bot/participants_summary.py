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

        actual_participants = sorted(participants_by_key.values(), key=lambda item: item["name"].lower())
        attendance = self._build_attendance(actual_participants)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "meeting_dir": str(self.meeting_dir),
            "participants_count": len(actual_participants),
            "participants": actual_participants,
            "actual_participants_count": len(actual_participants),
            "actual_participants": actual_participants,
            "expected_participants_count": len(attendance["expected_participants"]),
            "expected_participants": attendance["expected_participants"],
            "matched_expected_participants": attendance["matched_expected_participants"],
            "missing_expected_participants": attendance["missing_expected_participants"],
            "unexpected_actual_participants": attendance["unexpected_actual_participants"],
            "attendance": attendance,
            "sources": source_summaries,
        }
        self.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger(f"[Bot] Participants summary saved: {len(actual_participants)} participant(s), {self.output_path}")
        return self.output_path


    def _build_attendance(self, participants: list[dict]) -> dict:
        expected_participants = self._parse_expected_participants(self.expected_participants_raw)
        actual_by_key = {self._name_key(item.get("name")): item for item in participants if self._clean_name(item.get("name"))}
        expected_by_key = {self._name_key(item.get("name")): item for item in expected_participants if self._clean_name(item.get("name"))}

        matched = []
        missing = []
        for key, expected in expected_by_key.items():
            actual = actual_by_key.get(key)
            if actual:
                matched.append({
                    "name": expected["name"],
                    "email": expected.get("email"),
                    "actual_name": actual.get("name"),
                    "actual_participant": actual,
                })
            else:
                missing.append(expected)

        unexpected = [actual for key, actual in actual_by_key.items() if key not in expected_by_key]

        return {
            "actual_participants_count": len(participants),
            "actual_participants": participants,
            "actual_participant_names": [item["name"] for item in participants if self._clean_name(item.get("name"))],
            "expected_participants_count": len(expected_participants),
            "expected_participants": expected_participants,
            "expected_participant_names": [item["name"] for item in expected_participants],
            "matched_expected_participants": matched,
            "matched_participants": [item["name"] for item in matched],
            "missing_expected_participants": missing,
            "missing_participants": [item["name"] for item in missing],
            "unexpected_actual_participants": unexpected,
            "unexpected_participants": [item["name"] for item in unexpected],
        }

    def _parse_expected_participants(self, value: str | list | None) -> list[dict]:
        if value is None:
            return []
        raw_items: list = []
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = re.split(r"[,;\n\r]+", str(value))

        result = []
        seen = set()
        for item in raw_items:
            parsed = self._parse_expected_participant(item)
            if not parsed:
                continue
            key = self._name_key(parsed["name"])
            if key in seen:
                continue
            seen.add(key)
            result.append(parsed)
        return result

    def _parse_expected_participant(self, value) -> dict | None:
        if isinstance(value, dict):
            name = self._clean_name(value.get("name") or value.get("full_name") or value.get("display_name"))
            email = self._clean_email(value.get("email") or value.get("mail"))
            if not name and email:
                name = email
            return {"name": name, "email": email} if name else None

        text = self._clean_name(value)
        if not text:
            return None

        email = None
        angle_match = re.search(r"<\s*([^<>\s@]+@[^<>\s@]+\.[^<>\s@]+)\s*>", text)
        if angle_match:
            email = self._clean_email(angle_match.group(1))
            text = self._clean_name(text[:angle_match.start()] + " " + text[angle_match.end():])
        else:
            trailing_match = re.search(r"(?:\s+-\s+|\s+)([^\s<>@]+@[^\s<>@]+\.[^\s<>@]+)\s*$", text)
            if trailing_match:
                email = self._clean_email(trailing_match.group(1))
                text = self._clean_name(text[:trailing_match.start()])

        text = re.sub(r"\s+-\s*$", "", text).strip()
        name = self._clean_name(text)
        if not name and email:
            name = email
        return {"name": name, "email": email} if name else None

    @staticmethod
    def _clean_email(value) -> str | None:
        email = " ".join(str(value or "").split()).strip().lower()
        if not email:
            return None
        if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", email):
            return None
        return email

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
