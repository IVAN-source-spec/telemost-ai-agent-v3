import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PORTABLE_SESSION_FORMAT = "portable-browser-session-v1"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _validate_secret_key(secret_key: str) -> None:
    if not isinstance(secret_key, str) or not secret_key:
        raise ValueError("secret_key must be non-empty")
    if len(secret_key) < 16:
        raise ValueError("secret_key must be at least 16 characters")


def _parse_iso8601_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("issued_at must be ISO8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("issued_at must include timezone")
    return parsed.astimezone(timezone.utc)


def _to_iso8601_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_payload(payload_fields: dict[str, str]) -> str:
    return json.dumps(
        payload_fields,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _sign_payload(payload: str, secret_key: str) -> str:
    return hmac.new(
        key=secret_key.encode("utf-8"),
        msg=payload.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def build_portable_session_metadata(session_id: str, owner_repo: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "owner_repo": owner_repo,
        "encrypted": True,
        "format": PORTABLE_SESSION_FORMAT,
    }


def create_session_artifact(
    session_id: str,
    owner_repo: str,
    issued_at: str,
    secret_key: str,
    ttl_seconds: int = 300,
) -> dict[str, str]:
    _validate_secret_key(secret_key)
    if not session_id or not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("session_id must be non-empty and use safe charset")
    if not OWNER_REPO_PATTERN.fullmatch(owner_repo):
        raise ValueError("owner_repo must match org/repo")
    issued_at_dt = _parse_iso8601_utc(issued_at)
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    expires_at = _to_iso8601_utc(issued_at_dt + timedelta(seconds=ttl_seconds))
    payload_fields = {
        "session_id": session_id,
        "owner_repo": owner_repo,
        "issued_at": _to_iso8601_utc(issued_at_dt),
        "expires_at": expires_at,
        "format": PORTABLE_SESSION_FORMAT,
    }
    payload = _canonical_payload(payload_fields)
    return {**payload_fields, "signature": _sign_payload(payload, secret_key)}


def verify_session_artifact(
    artifact: dict[str, Any],
    secret_key: str,
    current_time: str | datetime | None = None,
    clock_skew_seconds: int = 5,
) -> bool:
    _validate_secret_key(secret_key)
    if clock_skew_seconds < 0:
        raise ValueError("clock_skew_seconds must be non-negative")

    required_keys = (
        "session_id",
        "owner_repo",
        "issued_at",
        "expires_at",
        "format",
        "signature",
    )
    if not isinstance(artifact, dict):
        return False
    if not all(key in artifact for key in required_keys):
        return False
    if not all(isinstance(artifact[key], str) for key in required_keys):
        return False
    if artifact["format"] != PORTABLE_SESSION_FORMAT:
        return False
    if not artifact["session_id"] or not SESSION_ID_PATTERN.fullmatch(artifact["session_id"]):
        return False
    if not OWNER_REPO_PATTERN.fullmatch(artifact["owner_repo"]):
        return False

    try:
        issued_at_dt = _parse_iso8601_utc(artifact["issued_at"])
        expires_at_dt = _parse_iso8601_utc(artifact["expires_at"])
    except ValueError:
        return False
    if expires_at_dt <= issued_at_dt:
        return False

    if current_time is None:
        now_dt = datetime.now(timezone.utc)
    elif isinstance(current_time, datetime):
        now_dt = (
            current_time.astimezone(timezone.utc)
            if current_time.tzinfo is not None
            else current_time.replace(tzinfo=timezone.utc)
        )
    else:
        try:
            now_dt = _parse_iso8601_utc(current_time)
        except ValueError:
            return False

    if now_dt + timedelta(seconds=clock_skew_seconds) < issued_at_dt:
        return False
    if now_dt > expires_at_dt:
        return False

    payload = _canonical_payload(
        {
            "session_id": artifact["session_id"],
            "owner_repo": artifact["owner_repo"],
            "issued_at": artifact["issued_at"],
            "expires_at": artifact["expires_at"],
            "format": artifact["format"],
        }
    )
    expected_signature = _sign_payload(payload, secret_key)
    return hmac.compare_digest(expected_signature, artifact["signature"])


def load_session_artifact(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    with artifact_path.open("r", encoding="utf-8") as artifact_file:
        payload = json.load(artifact_file)
    if not isinstance(payload, dict):
        raise ValueError("session artifact must be a JSON object")
    return payload


def write_session_artifact(path: str | Path, artifact: dict[str, str]) -> None:
    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def resolve_verified_storage_state_path(
    *,
    storage_state_path: str | Path,
    artifact_path: str | Path,
    secret_key: str | None,
    current_time: str | datetime | None = None,
) -> Path | None:
    state_path = Path(storage_state_path)
    session_artifact_path = Path(artifact_path)
    if not secret_key or not state_path.exists() or not session_artifact_path.exists():
        return None

    try:
        artifact = load_session_artifact(session_artifact_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return None

    if not verify_session_artifact(
        artifact,
        secret_key=secret_key,
        current_time=current_time,
    ):
        return None
    return state_path
