import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re

from dotenv import load_dotenv
from core.storage.meeting_storage import get_meeting_storage
from core.transcription.speaker_count import load_snapshot_participant_names, target_speakers_for_audio


load_dotenv()


storage = get_meeting_storage()


def read_status(status_file: Path) -> dict | None:
    if not status_file.exists():
        return None
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as error:
        print(f"[TranscriptionMonitor] Could not read {status_file}: {error}")
        return None


def write_status(status_file: Path, data: dict) -> None:
    status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_status_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def stale_processing_without_job(status_data: dict) -> bool:
    status = str(status_data.get("status") or "").lower()
    if status not in {"pending", "processing"}:
        return False
    if status_data.get("job_id"):
        return False

    stale_seconds = int(os.getenv("TRANSCRIPTION_PROCESSING_STALE_SECONDS", "900"))
    sent_at = parse_status_datetime(status_data.get("sent_at"))
    if sent_at is None:
        return True
    age_seconds = (datetime.now(timezone.utc) - sent_at).total_seconds()
    return age_seconds >= max(60, stale_seconds)


def requeue_stale_processing_without_job(status_file: Path, status_data: dict) -> dict:
    data = dict(status_data)
    previous_attempts = int(data.get("submit_attempts") or 0)
    data["status"] = "queued"
    data["job_id"] = None
    data["sent_at"] = None
    data["completed_at"] = None
    data["transcript_path"] = None
    data["submit_attempts"] = previous_attempts + 1
    data["last_requeued_at"] = datetime.now(timezone.utc).isoformat()
    data["error"] = (
        "Recovered stale transcription state: status was processing/pending "
        "without job_id, so it was queued again."
    )
    write_status(status_file, data)
    return data


def retryable_failed_status(status_data: dict) -> bool:
    status = str(status_data.get("status") or "").lower()
    if status != "failed":
        return False

    attempts = int(status_data.get("submit_attempts") or 0)
    max_attempts = int(os.getenv("TRANSCRIPTION_FAILED_MAX_ATTEMPTS", "5"))
    if attempts >= max(1, max_attempts):
        return False

    error = str(status_data.get("error") or "")
    if not error.strip():
        return True

    retry_markers = (
        "TimeoutError",
        "timed out",
        "HTTP Error 429",
        "HTTP Error 503",
        "Slow Down",
        "ServiceUnavailable",
        "Service Unavailable",
        "Connection reset",
        "Remote end closed connection",
        "Pyannote job failed:",
    )
    return any(marker in error for marker in retry_markers)


def requeue_failed_transcription(status_file: Path, status_data: dict) -> dict:
    data = dict(status_data)
    previous_attempts = int(data.get("submit_attempts") or 0)
    data["status"] = "queued"
    data["job_id"] = None
    data["sent_at"] = None
    data["completed_at"] = None
    data["transcript_path"] = None
    data["submit_attempts"] = previous_attempts + 1
    data["last_requeued_at"] = datetime.now(timezone.utc).isoformat()
    data["error"] = "Recovered retryable failed transcription; queued again."
    write_status(status_file, data)
    return data


def _is_confidential_dir(path: Path) -> bool:
    return path.name in {
        "\u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f \u0447\u0430\u0441\u0442\u044c",
        "confidential_part",
    }


def _meeting_dir_for_audio(audio_path: Path) -> Path:
    audio_dir = audio_path.parent
    if _is_confidential_dir(audio_dir):
        return audio_dir.parent
    return audio_dir


def _confidential_recording_is_completed(audio_path: Path) -> bool:
    audio_dir = audio_path.parent
    if not _is_confidential_dir(audio_dir):
        return True
    status_data = read_status(audio_dir / "confidential_recording_status.json")
    return bool(status_data and status_data.get("status") == "completed")


def find_audio_files(recordings_dir: Path) -> list[Path]:
    audio_files = get_meeting_storage(recordings_dir.parent).find_recording_audio_files()
    confidential_audio_files = sorted(
        path
        for path in recordings_dir.glob("*/*/*/*/*/recording_meeting.wav")
        if path.is_file() and _is_confidential_dir(path.parent)
    )
    return sorted(set(audio_files + confidential_audio_files))


def find_completed_meeting_dirs(recordings_dir: Path) -> list[Path]:
    meeting_dirs = []
    for audio_path in find_audio_files(recordings_dir):
        if not _confidential_recording_is_completed(audio_path):
            continue
        status_data = read_status(audio_path.parent / "transcription_status.json")
        if status_data and status_data.get("status") == "completed":
            meeting_dirs.append(_meeting_dir_for_audio(audio_path))
    return sorted(set(meeting_dirs))


async def finalize_meeting_folder(meeting_dir: Path) -> None:
    uploaded = await asyncio.to_thread(storage.finalize_meeting_folder, meeting_dir)
    if uploaded:
        print(f"[TranscriptionMonitor] Meeting folder finalized: {meeting_dir}")


def ensure_status_for_audio(audio_path: Path) -> dict:
    status_file = audio_path.parent / "transcription_status.json"
    data = read_status(status_file)
    if data is not None:
        return data

    target_speakers = target_speakers_for_audio(audio_path)
    data = {
        "audio_path": str(audio_path),
        "job_id": None,
        "target_speakers": target_speakers,
        "status": "queued",
        "sent_at": None,
        "completed_at": None,
        "transcript_path": None,
        "error": None,
    }
    write_status(status_file, data)
    print(f"[TranscriptionMonitor] Queued new audio: {audio_path}")
    return data


def transcription_backend() -> str:
    return "remote"


def status_backend(status_data: dict) -> str:
    return "remote"


def _safe_job_id(job_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in job_id)


def remote_transcript_path(audio_path: Path, job_id: str) -> Path:
    return audio_path.parent / f"{audio_path.stem}_pyannote_{_safe_job_id(job_id)}.txt"


def remote_job_json_path(audio_path: Path, job_id: str) -> Path:
    return audio_path.parent / f"{audio_path.stem}_pyannote_{_safe_job_id(job_id)}.json"


def write_remote_job_json(audio_path: Path, job_id: str, payload: dict) -> Path:
    output_path = remote_job_json_path(audio_path, job_id)
    data = {
        "jobId": job_id,
        "remoteJobId": job_id,
        "provider": "remote-transcription-service",
        "payload": payload,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def _participant_name(entry: dict) -> str:
    participant = entry.get("participant") if isinstance(entry.get("participant"), dict) else {}
    return str(
        participant.get("display_name")
        or participant.get("name")
        or entry.get("display_name")
        or entry.get("name")
        or "Unknown participant"
    ).strip()


def _participant_email(entry: dict) -> str | None:
    participant = entry.get("participant") if isinstance(entry.get("participant"), dict) else {}
    profile = entry.get("profile") if isinstance(entry.get("profile"), dict) else {}
    email = participant.get("email") or profile.get("email") or entry.get("email")
    if not email:
        return None
    normalized = str(email).strip()
    return normalized or None


def _readiness_from_payload(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    options = payload.get("options")
    if not isinstance(options, dict):
        return None
    readiness = options.get("participant_readiness")
    return readiness if isinstance(readiness, dict) else None


def _readiness_from_status(status_data: dict) -> dict | None:
    for key in ("remote_status_response", "remote_submit_response"):
        readiness = _readiness_from_payload(status_data.get(key))
        if readiness:
            return readiness
    return None


def write_voice_profile_readiness(audio_path: Path, status_data: dict) -> Path | None:
    readiness = _readiness_from_status(status_data)
    if not readiness:
        return None

    summary = readiness.get("summary") if isinstance(readiness.get("summary"), dict) else {}
    participants = readiness.get("participants") if isinstance(readiness.get("participants"), list) else []
    groups = {"ready": [], "incomplete": [], "missing": [], "other": []}

    for entry in participants:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "other").strip().lower()
        if status not in groups:
            status = "other"
        groups[status].append(entry)

    def format_entry(entry: dict) -> str:
        name = _participant_name(entry)
        email = _participant_email(entry)
        label = f"{name} <{email}>" if email else name
        action = entry.get("action")
        issues = entry.get("issues") if isinstance(entry.get("issues"), list) else []
        details = []
        if action:
            details.append(f"action: {action}")
        if issues:
            details.append("issues: " + ", ".join(str(item) for item in issues))
        return f"- {label}" + (f" ({'; '.join(details)})" if details else "")

    lines = [
        "Voice profile readiness",
        "",
        f"Total participants: {summary.get('participants_count', len(participants))}",
        f"Ready: {summary.get('ready_count', len(groups['ready']))}",
        f"Need more voice samples: {summary.get('incomplete_count', len(groups['incomplete']))}",
        f"Need profile creation: {summary.get('missing_count', len(groups['missing']))}",
        "",
        "Ready:",
    ]
    lines.extend(format_entry(entry) for entry in groups["ready"])
    if not groups["ready"]:
        lines.append("- none")

    lines.extend(["", "Need more voice samples:"])
    lines.extend(format_entry(entry) for entry in groups["incomplete"])
    if not groups["incomplete"]:
        lines.append("- none")

    lines.extend(["", "Need profile creation:"])
    lines.extend(format_entry(entry) for entry in groups["missing"])
    if not groups["missing"]:
        lines.append("- none")

    if groups["other"]:
        lines.extend(["", "Other statuses:"])
        lines.extend(format_entry(entry) for entry in groups["other"])

    lines.extend(["", f"Updated at: {datetime.now(timezone.utc).isoformat()}"])
    output_path = audio_path.parent / "voice_profile_readiness.txt"
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _remote_error_message(error: object) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or error)
    return str(error or "")


def _is_no_turn_level_transcript(result: dict) -> bool:
    error = result.get('error')
    code = ''
    if isinstance(error, dict):
        code = str(error.get('code') or '').lower()
    message = _remote_error_message(error).lower()
    return (
        code == 'no_speech_detected'
        or 'no_speech_detected' in message
        or 'no speech detected' in message
        or 'did not detect speech' in message
        or 'no turn-level transcript' in message
        or 'returned no turn-level transcript' in message
    )

def write_empty_remote_transcript(audio_path: Path, job_id: str, result: dict) -> Path:
    transcript_path = remote_transcript_path(audio_path, f"{job_id}_no_speech")
    lines = [
        "Transcript was not created: the service did not detect speech segments in the recording.",
        "",
        "This is not a voice profile readiness error. The transcription provider returned empty diarization, wordLevelTranscription and turnLevelTranscription lists.",
        "",
        f"Remote job: {job_id}",
        f"Audio: {audio_path}",
        f"Processed at: {datetime.now(timezone.utc).isoformat()}",
    ]
    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_remote_job_json(audio_path, job_id, result)
    return transcript_path


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_email(value: object) -> str | None:
    email = _clean_text(value).lower()
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return None
    return email


def _name_key(value: object) -> str:
    return _clean_text(value).casefold()


def _load_json_file(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        print(f"[TranscriptionMonitor] Could not read {path}: {error}")
        return None


def _split_participant_names(value: object) -> list[str]:
    normalized = _clean_text(value)
    if not normalized:
        return []
    for separator in [",", ";", "\n"]:
        if separator in normalized:
            return [_clean_text(part) for part in normalized.split(separator) if _clean_text(part)]
    words = [word for word in normalized.split() if word]
    if len(words) > 2 and len(words) % 2 == 0:
        return [" ".join(words[index:index + 2]) for index in range(0, len(words), 2)]
    return [normalized]


def _summary_path_for_audio(audio_path: Path) -> Path:
    return _meeting_dir_for_audio(audio_path) / "participants_all.json"


def _participants_summary_for_audio(audio_path: Path) -> dict:
    summary = _load_json_file(_summary_path_for_audio(audio_path))
    return summary if isinstance(summary, dict) else {}


def _participant_email_map(audio_path: Path) -> dict[str, str]:
    summary = _participants_summary_for_audio(audio_path)
    if not summary:
        return {}

    emails: dict[str, str] = {}
    for item in summary.get("matched_expected_participants") or []:
        if not isinstance(item, dict):
            continue
        email = _clean_email(item.get("email"))
        if not email:
            continue
        for name in (item.get("actual_name"), item.get("name")):
            key = _name_key(name)
            if key:
                emails[key] = email

    for item in summary.get("expected_participants") or []:
        if not isinstance(item, dict):
            continue
        email = _clean_email(item.get("email"))
        key = _name_key(item.get("name"))
        if key and email:
            emails.setdefault(key, email)

    for item in summary.get("actual_participants") or []:
        if not isinstance(item, dict):
            continue
        email = _clean_email(item.get("email"))
        key = _name_key(item.get("name"))
        if key and email:
            emails.setdefault(key, email)
    return emails


def _participant_names_for_audio(audio_path: Path) -> list[str]:
    snapshot_path = audio_path.parent / "participants_snapshot.json"
    names = load_snapshot_participant_names(snapshot_path)
    if names:
        return names

    meeting_time = _load_json_file(audio_path.parent / "meeting_time.json")
    if isinstance(meeting_time, dict):
        names = _split_participant_names(meeting_time.get("participants"))
        if names:
            return names

    summary = _load_json_file(_summary_path_for_audio(audio_path))
    if isinstance(summary, dict):
        source_name = "confidential" if _is_confidential_dir(audio_path.parent) else "main"
        for source in summary.get("sources") or []:
            if isinstance(source, dict) and source.get("source") == source_name:
                names = [_clean_text(name) for name in source.get("participants") or []]
                names = [name for name in names if name]
                if names:
                    return names
        names = [_clean_text(item.get("name")) for item in summary.get("actual_participants") or [] if isinstance(item, dict)]
        return [name for name in names if name]
    return []


def _expected_participants_for_audio(audio_path: Path) -> list[dict]:
    summary = _participants_summary_for_audio(audio_path)
    participants = []
    seen = set()
    for item in summary.get("expected_participants") or []:
        if not isinstance(item, dict):
            continue
        clean_name = _clean_text(item.get("name"))
        key = _name_key(clean_name)
        if not key or key in seen:
            continue
        seen.add(key)
        participant = {"display_name": clean_name}
        email = _clean_email(item.get("email"))
        if email:
            participant["email"] = email
        participants.append(participant)
    return participants


def participants_for_audio(audio_path: Path) -> list[dict]:
    if segment_type_for_audio(audio_path) == "public":
        expected_participants = _expected_participants_for_audio(audio_path)
        if expected_participants:
            return expected_participants

    email_by_name = _participant_email_map(audio_path)
    participants = []
    seen = set()
    for name in _participant_names_for_audio(audio_path):
        clean_name = _clean_text(name)
        key = _name_key(clean_name)
        if not key or key in seen:
            continue
        seen.add(key)
        participant = {"display_name": clean_name}
        email = email_by_name.get(key)
        if email:
            participant["email"] = email
        participants.append(participant)
    return participants


def segment_type_for_audio(audio_path: Path) -> str:
    return "confidential" if _is_confidential_dir(audio_path.parent) else "public"


def _recipient_from_name_email(name: object = None, email: object = None) -> dict | None:
    clean_email = _clean_email(email)
    if not clean_email:
        return None
    recipient = {"email": clean_email}
    display_name = _clean_text(name)
    if display_name and display_name != clean_email:
        recipient["display_name"] = display_name
    return recipient


def _parse_person_with_email(value: object) -> dict | None:
    if isinstance(value, dict):
        return _recipient_from_name_email(
            value.get("display_name") or value.get("full_name") or value.get("name"),
            value.get("email") or value.get("mail"),
        )
    text = _clean_text(value)
    if not text:
        return None
    email = None
    angle_match = re.search(r"<\s*([^<>\s@]+@[^<>\s@]+\.[^<>\s@]+)\s*>", text)
    if angle_match:
        email = angle_match.group(1)
        text = _clean_text(text[:angle_match.start()] + " " + text[angle_match.end():])
    else:
        trailing_match = re.search(r"(?:\s+-\s+|\s+)([^\s<>@]+@[^\s<>@]+\.[^\s<>@]+)\s*$", text)
        if trailing_match:
            email = trailing_match.group(1)
            text = _clean_text(text[:trailing_match.start()])
    return _recipient_from_name_email(text, email)


def _unique_recipients(items: list[dict | None]) -> list[dict]:
    recipients = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        email = _clean_email(item.get("email"))
        if not email or email in seen:
            continue
        seen.add(email)
        recipient = {"email": email}
        display_name = _clean_text(item.get("display_name"))
        if display_name:
            recipient["display_name"] = display_name
        recipients.append(recipient)
    return recipients


def expected_result_recipients_for_audio(audio_path: Path) -> list[dict]:
    summary = _participants_summary_for_audio(audio_path)
    recipients = []
    for item in summary.get("expected_participants") or []:
        if not isinstance(item, dict):
            continue
        recipients.append(_recipient_from_name_email(item.get("name"), item.get("email")))
    organizer = organizer_for_transcription(audio_path)
    if organizer:
        recipients.append(organizer)
    return _unique_recipients(recipients)


def result_recipients_for_audio(audio_path: Path, participants: list[dict]) -> list[dict] | None:
    if segment_type_for_audio(audio_path) == "confidential":
        return _unique_recipients([
            _recipient_from_name_email(participant.get("display_name"), participant.get("email"))
            for participant in participants
        ])
    recipients = expected_result_recipients_for_audio(audio_path)
    return recipients or None


def organizer_for_transcription(audio_path: Path | None = None) -> dict | None:
    if audio_path is not None:
        meeting_time = _load_json_file(_meeting_dir_for_audio(audio_path) / "meeting_time.json")
        if isinstance(meeting_time, dict):
            organizer = _parse_person_with_email(meeting_time.get("organizer"))
            if organizer:
                return organizer
    email = _clean_email(
        os.getenv("REMOTE_TRANSCRIPTION_ORGANIZER_EMAIL")
        or os.getenv("TRANSCRIPTION_ORGANIZER_EMAIL")
        or os.getenv("TELEMOST_ORGANIZER_EMAIL")
    )
    if not email:
        return None
    name = _clean_text(
        os.getenv("REMOTE_TRANSCRIPTION_ORGANIZER_NAME")
        or os.getenv("TRANSCRIPTION_ORGANIZER_NAME")
        or os.getenv("TELEMOST_ORGANIZER_NAME")
    )
    return _recipient_from_name_email(name, email)


def transcription_title_for_audio(audio_path: Path) -> str:
    if _is_confidential_dir(audio_path.parent):
        return f"{_meeting_dir_for_audio(audio_path).name} - \u041a\u043e\u043d\u0444\u0438\u0434\u0435\u043d\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f \u0447\u0430\u0441\u0442\u044c"
    return audio_path.parent.name


async def submit_transcription(meeting_dir: Path, status_data: dict, api_key: str | None) -> None:
    status_file = meeting_dir / "transcription_status.json"
    audio_path = Path(status_data["audio_path"])
    previous_target_speakers = int(status_data.get("target_speakers") or 0)
    target_speakers = target_speakers_for_audio(audio_path, fallback=previous_target_speakers or None)

    data = read_status(status_file) or status_data
    data["target_speakers"] = target_speakers
    data["status"] = "processing"
    data["sent_at"] = datetime.now(timezone.utc).isoformat()
    data["error"] = None
    write_status(status_file, data)

    backend = transcription_backend()
    data["transcription_backend"] = backend
    write_status(status_file, data)

    print(f"[TranscriptionMonitor] Submitting transcription via {backend}: {audio_path}")
    try:
        if backend == "remote":
            from core.transcription.remote_adapter import build_remote_transcription_adapter_from_env

            adapter = build_remote_transcription_adapter_from_env()
            remote_participants = participants_for_audio(audio_path)
            remote_segment_type = segment_type_for_audio(audio_path)
            if remote_segment_type == "public" and remote_participants:
                target_speakers = max(1, len(remote_participants))
            remote_result_recipients = result_recipients_for_audio(audio_path, remote_participants)
            remote_organizer = organizer_for_transcription(audio_path)
            data["target_speakers"] = target_speakers
            data["remote_participants"] = remote_participants
            data["remote_segment_type"] = remote_segment_type
            data["remote_result_recipients"] = remote_result_recipients
            data["remote_organizer"] = remote_organizer
            write_status(status_file, data)
            print(f"[TranscriptionMonitor] Target speakers: {target_speakers}")

            payload = await asyncio.to_thread(
                adapter.submit_job,
                str(audio_path),
                target_speakers=target_speakers,
                title=transcription_title_for_audio(audio_path),
                participants=remote_participants,
                segment_type=remote_segment_type,
                result_recipients=remote_result_recipients,
                organizer=remote_organizer,
                mqr_upload=False if remote_segment_type == "confidential" else None,
            )
            job_id = str(payload["job_id"])
            data = read_status(status_file) or status_data
            data["job_id"] = job_id
            data["status"] = "processing"
            data["remote_status"] = payload.get("status") or "queued"
            data["remote_submit_response"] = payload
            data["transcription_backend"] = "remote"
            data["checked_at"] = datetime.now(timezone.utc).isoformat()
            write_voice_profile_readiness(audio_path, data)
            data["completed_at"] = None
            data["transcript_path"] = None
            data["error"] = None
            write_status(status_file, data)
            print(f"[TranscriptionMonitor] Remote transcription queued for {meeting_dir.name}, job_id={job_id}")
            return
    except Exception as error:
        data = read_status(status_file) or status_data
        data["status"] = "failed"
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
        data["error"] = str(error)
        write_status(status_file, data)
        print(f"[TranscriptionMonitor] Failed {meeting_dir.name}: {error}")


def _remote_failure_code(status_data: dict) -> str:
    result = status_data.get("remote_status_response")
    error = result.get("error") if isinstance(result, dict) else status_data.get("error")
    if isinstance(error, dict):
        return _clean_text(error.get("code")).lower()
    return ""


def _remote_failure_message(status_data: dict) -> str:
    result = status_data.get("remote_status_response")
    if isinstance(result, dict):
        return _remote_error_message(result.get("error"))
    return _remote_error_message(status_data.get("error"))


def remote_job_retryable(status_data: dict) -> bool:
    if status_backend(status_data) != "remote":
        return False
    if str(status_data.get("status") or "").lower() != "failed":
        return False
    if not str(status_data.get("job_id") or "").strip():
        return False

    attempts = int(status_data.get("remote_retry_attempts") or 0)
    max_attempts = int(os.getenv("REMOTE_TRANSCRIPTION_JOB_MAX_RETRIES", "3"))
    if attempts >= max(1, max_attempts):
        return False

    code = _remote_failure_code(status_data)
    message = _remote_failure_message(status_data)
    retry_markers = (
        "service_restarted",
        "Service restarted after the upload completed",
        "TimeoutError",
        "timed out",
        "HTTP Error 429",
        "HTTP Error 503",
        "ServiceUnavailable",
        "Service Unavailable",
        "Connection reset",
        "Remote end closed connection",
    )
    return code == "service_restarted" or any(marker in message for marker in retry_markers)


async def refresh_failed_remote_status(meeting_dir: Path, status_data: dict) -> dict:
    if status_backend(status_data) != "remote":
        return status_data
    if str(status_data.get("status") or "").lower() != "failed":
        return status_data
    job_id = str(status_data.get("job_id") or "").strip()
    if not job_id:
        return status_data

    from core.transcription.remote_adapter import build_remote_transcription_adapter_from_env

    status_file = meeting_dir / "transcription_status.json"
    adapter = build_remote_transcription_adapter_from_env()
    result = await asyncio.to_thread(adapter.get_job, job_id)
    data = read_status(status_file) or status_data
    data["remote_status"] = str(result.get("status") or "unknown").lower()
    data["remote_status_response"] = result
    data["checked_at"] = datetime.now(timezone.utc).isoformat()
    data["transcription_backend"] = "remote"
    write_status(status_file, data)
    return data


async def retry_existing_remote_job(meeting_dir: Path, status_data: dict) -> bool:
    if not remote_job_retryable(status_data):
        return False

    from core.transcription.remote_adapter import build_remote_transcription_adapter_from_env

    status_file = meeting_dir / "transcription_status.json"
    job_id = str(status_data.get("job_id") or "").strip()
    adapter = build_remote_transcription_adapter_from_env()
    attempts = int(status_data.get("remote_retry_attempts") or 0) + 1
    try:
        payload = await asyncio.to_thread(adapter.retry_job, job_id)
    except Exception as error:
        data = read_status(status_file) or status_data
        data["remote_retry_attempts"] = attempts
        data["last_remote_retry_at"] = datetime.now(timezone.utc).isoformat()
        data["error"] = f"Remote job retry failed: {error}"
        write_status(status_file, data)
        print(f"[TranscriptionMonitor] Remote job retry failed for {meeting_dir.name}, job_id={job_id}: {error}")
        return False

    data = read_status(status_file) or status_data
    data["status"] = "processing"
    data["remote_status"] = str(payload.get("status") or "queued").lower()
    data["remote_retry_attempts"] = attempts
    data["remote_retry_response"] = payload
    data["last_remote_retry_at"] = datetime.now(timezone.utc).isoformat()
    data["completed_at"] = None
    data["error"] = None
    data["checked_at"] = datetime.now(timezone.utc).isoformat()
    write_status(status_file, data)
    print(f"[TranscriptionMonitor] Retried existing remote job for {meeting_dir.name}, job_id={job_id}, attempt={attempts}")
    return True


async def complete_no_speech_remote_failure(meeting_dir: Path, status_data: dict) -> bool:
    if status_backend(status_data) != "remote":
        return False
    if str(status_data.get("status") or "").lower() != "failed":
        return False
    result = status_data.get("remote_status_response")
    if not isinstance(result, dict) or not _is_no_turn_level_transcript(result):
        return False
    job_id = str(status_data.get("job_id") or "").strip()
    if not job_id:
        return False

    status_file = meeting_dir / "transcription_status.json"
    data = read_status(status_file) or status_data
    audio_path = Path(data["audio_path"])
    write_voice_profile_readiness(audio_path, data)
    transcript_path = write_empty_remote_transcript(audio_path, job_id, result)
    data["status"] = "completed"
    data["completed_at"] = datetime.now(timezone.utc).isoformat()
    data["transcript_path"] = str(transcript_path)
    data["empty_transcript"] = True
    data["error"] = None
    data["warning"] = _remote_error_message(result.get("error"))
    write_status(status_file, data)
    print(
        f"[TranscriptionMonitor] Recovered no-speech remote failure for {meeting_dir.name}; "
        f"saved diagnostic transcript, job_id={job_id}"
    )
    await finalize_meeting_folder(_meeting_dir_for_audio(audio_path))
    return True


async def refresh_pending_status(meeting_dir: Path, status_data: dict, api_key: str | None) -> None:
    job_id = status_data.get("job_id")
    if not job_id:
        return

    status_file = meeting_dir / "transcription_status.json"
    backend = status_backend(status_data)

    if backend == "remote":
        from core.transcription.remote_adapter import build_remote_transcription_adapter_from_env

        adapter = build_remote_transcription_adapter_from_env()
        result = await asyncio.to_thread(adapter.get_job, str(job_id))
        job_status = str(result.get("status", "unknown")).lower()
        data = read_status(status_file) or status_data
        data["remote_status"] = job_status
        data["remote_status_response"] = result
        data["checked_at"] = datetime.now(timezone.utc).isoformat()
        data["transcription_backend"] = "remote"
        audio_path = Path(data["audio_path"])
        write_voice_profile_readiness(audio_path, data)

        if job_status == "succeeded":
            transcript_path = remote_transcript_path(audio_path, str(job_id))
            await asyncio.to_thread(adapter.download_transcript, str(job_id), transcript_path)
            write_remote_job_json(audio_path, str(job_id), result)
            data["status"] = "completed"
            data["completed_at"] = datetime.now(timezone.utc).isoformat()
            data["transcript_path"] = str(transcript_path)
            data["error"] = None
            write_status(status_file, data)
            print(f"[TranscriptionMonitor] Remote transcription completed for {meeting_dir.name}, job_id={job_id}")
            await finalize_meeting_folder(_meeting_dir_for_audio(audio_path))
        elif job_status == "failed":
            if _is_no_turn_level_transcript(result):
                transcript_path = write_empty_remote_transcript(audio_path, str(job_id), result)
                data["status"] = "completed"
                data["completed_at"] = datetime.now(timezone.utc).isoformat()
                data["transcript_path"] = str(transcript_path)
                data["empty_transcript"] = True
                data["error"] = None
                data["warning"] = _remote_error_message(result.get("error"))
                write_status(status_file, data)
                print(
                    f"[TranscriptionMonitor] Remote job produced no transcript for {meeting_dir.name}; "
                    f"saved diagnostic transcript, job_id={job_id}"
                )
                await finalize_meeting_folder(_meeting_dir_for_audio(audio_path))
            else:
                data["status"] = "failed"
                data["completed_at"] = datetime.now(timezone.utc).isoformat()
                data["error"] = result.get("error") or "Remote transcription failed"
                write_status(status_file, data)
                print(f"[TranscriptionMonitor] Remote job failed for {meeting_dir.name}: {data['error']}")
        else:
            data["status"] = "processing"
            write_status(status_file, data)
        return


async def monitor_loop() -> None:
    backend = transcription_backend()
    api_key = None

    recordings_dir = Path.cwd() / "recordings"
    recordings_dir.mkdir(exist_ok=True)

    interval_seconds = int(os.getenv("TRANSCRIPTION_MONITOR_INTERVAL_SECONDS", "10"))
    max_parallel = int(os.getenv("TRANSCRIPTION_MONITOR_MAX_PARALLEL", "3"))
    retry_failed_uploads = os.getenv("YANDEX_DISK_RETRY_FAILED_UPLOADS_ENABLED", "1").lower() in (
        "1",
        "true",
        "yes",
    )
    retry_upload_interval_seconds = int(os.getenv("YANDEX_DISK_RETRY_FAILED_UPLOADS_INTERVAL_SECONDS", "300"))
    semaphore = asyncio.Semaphore(max_parallel)
    running: dict[str, asyncio.Task] = {}
    running_upload_retries: dict[str, asyncio.Task] = {}
    next_upload_retry_at = 0.0

    print(f"[TranscriptionMonitor] Monitoring {recordings_dir}")
    print(f"[TranscriptionMonitor] Backend: {backend}")
    print(f"[TranscriptionMonitor] Max parallel submissions: {max_parallel}")
    if retry_failed_uploads:
        print(
            "[TranscriptionMonitor] Yandex Disk retry for completed local meetings enabled: "
            f"every {retry_upload_interval_seconds}s"
        )

    async def run_limited(meeting_dir: Path, status_data: dict) -> None:
        async with semaphore:
            await submit_transcription(meeting_dir, status_data, api_key)

    async def run_upload_retry_limited(meeting_dir: Path) -> None:
        async with semaphore:
            print(f"[TranscriptionMonitor] Retrying Yandex Disk upload: {meeting_dir}")
            await finalize_meeting_folder(meeting_dir)

    while True:
        for key, task in list(running.items()):
            if task.done():
                running.pop(key, None)
                try:
                    task.result()
                except Exception as error:
                    print(f"[TranscriptionMonitor] Background task failed: {error}")

        for key, task in list(running_upload_retries.items()):
            if task.done():
                running_upload_retries.pop(key, None)
                try:
                    task.result()
                except Exception as error:
                    print(f"[TranscriptionMonitor] Background upload retry failed: {error}")

        for audio_path in find_audio_files(recordings_dir):
            if not _confidential_recording_is_completed(audio_path):
                continue
            meeting_dir = audio_path.parent
            status_data = ensure_status_for_audio(audio_path)
            status_file = meeting_dir / "transcription_status.json"
            status = status_data.get("status")
            key = str(meeting_dir)

            if key not in running and stale_processing_without_job(status_data):
                status_data = requeue_stale_processing_without_job(status_file, status_data)
                status = status_data.get("status")
                print(f"[TranscriptionMonitor] Requeued stale processing transcription: {meeting_dir}")

            if key not in running and status == "failed":
                status_data = await refresh_failed_remote_status(meeting_dir, status_data)
                recovered = await complete_no_speech_remote_failure(meeting_dir, status_data)
                if recovered:
                    continue
                retried_remote = await retry_existing_remote_job(meeting_dir, status_data)
                if retried_remote:
                    continue

            if key not in running and retryable_failed_status(status_data):
                status_data = requeue_failed_transcription(status_file, status_data)
                status = status_data.get("status")
                print(f"[TranscriptionMonitor] Requeued failed transcription: {meeting_dir}")

            if status == "queued" and key not in running:
                running[key] = asyncio.create_task(run_limited(meeting_dir, status_data))
            elif status in {"pending", "processing"} and key not in running:
                await refresh_pending_status(meeting_dir, status_data, api_key)

        loop_time = asyncio.get_running_loop().time()
        if retry_failed_uploads and loop_time >= next_upload_retry_at:
            next_upload_retry_at = loop_time + max(30, retry_upload_interval_seconds)
            for meeting_dir in find_completed_meeting_dirs(recordings_dir):
                key = str(meeting_dir)
                if key in running or key in running_upload_retries:
                    continue
                running_upload_retries[key] = asyncio.create_task(run_upload_retry_limited(meeting_dir))

        await asyncio.sleep(interval_seconds)


def main() -> None:
    asyncio.run(monitor_loop())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[TranscriptionMonitor] Stopped")
