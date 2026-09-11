from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, HTTPException, Request, status


session_update_router = APIRouter(prefix="/api/v1/node/session", tags=["node-session"])
ALGORITHM = "AES-256-GCM"
VERSION = 1
MAX_ENVELOPE_BYTES = 3 * 1024 * 1024


def _enabled() -> bool:
    return os.getenv("SESSION_UPDATE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _decode(value: object, field: str) -> bytes:
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{field} must be a base64 string")
    try:
        return base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid {field}") from exc


def _aad(envelope: dict[str, object]) -> bytes:
    metadata = {key: envelope.get(key) for key in ("version", "algorithm", "target_id", "issued_at", "request_id", "sha256")}
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _authorize(request: Request) -> None:
    expected = os.getenv("SESSION_UPDATE_API_TOKEN", "")
    provided = request.headers.get("Authorization", "")
    if not expected or not provided.startswith("Bearer ") or not hmac.compare_digest(provided[7:], expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session update token")


def _validate_storage_state(plaintext: bytes) -> dict[str, object]:
    try:
        state = json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="decrypted payload is not valid JSON") from exc
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
        raise HTTPException(status_code=422, detail="invalid Playwright storage_state structure")
    cookies = state["cookies"]
    if not cookies:
        raise HTTPException(status_code=422, detail="storage_state contains no cookies")
    if not any(isinstance(cookie, dict) and "yandex" in str(cookie.get("domain", "")).lower() for cookie in cookies):
        raise HTTPException(status_code=422, detail="storage_state contains no Yandex cookies")
    return state


@session_update_router.post("/update")
async def update_session(request: Request) -> dict[str, object]:
    if not _enabled():
        raise HTTPException(status_code=404, detail="session update endpoint is disabled")
    _authorize(request)
    body = await request.body()
    if len(body) > MAX_ENVELOPE_BYTES:
        raise HTTPException(status_code=413, detail="encrypted envelope is too large")
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="invalid JSON envelope") from exc
    if not isinstance(envelope, dict):
        raise HTTPException(status_code=422, detail="envelope must be an object")
    if envelope.get("version") != VERSION or envelope.get("algorithm") != ALGORITHM:
        raise HTTPException(status_code=422, detail="unsupported encryption envelope")

    target_id = os.getenv("SESSION_UPDATE_TARGET_ID", os.getenv("BOT_NODE_ID", ""))
    if envelope.get("target_id") != target_id:
        raise HTTPException(status_code=422, detail="target_id mismatch")
    try:
        issued_at = datetime.fromisoformat(str(envelope.get("issued_at", "")).replace("Z", "+00:00"))
        if issued_at.tzinfo is None:
            raise ValueError
        age = abs((datetime.now(timezone.utc) - issued_at.astimezone(timezone.utc)).total_seconds())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid issued_at") from exc
    if age > 300:
        raise HTTPException(status_code=422, detail="encrypted envelope is expired")

    key = _decode(os.getenv("SESSION_UPDATE_ENCRYPTION_KEY", ""), "encryption key")
    nonce = _decode(envelope.get("nonce"), "nonce")
    ciphertext = _decode(envelope.get("ciphertext"), "ciphertext")
    if len(key) != 32 or len(nonce) != 12:
        raise HTTPException(status_code=422, detail="invalid encryption parameters")
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(envelope))
    except Exception as exc:
        raise HTTPException(status_code=422, detail="payload authentication failed") from exc
    max_plaintext = int(os.getenv("SESSION_UPDATE_MAX_PLAINTEXT_BYTES", "2097152"))
    if len(plaintext) > max_plaintext:
        raise HTTPException(status_code=413, detail="decrypted storage_state is too large")
    digest = hashlib.sha256(plaintext).hexdigest()
    if not hmac.compare_digest(digest, str(envelope.get("sha256", ""))):
        raise HTTPException(status_code=422, detail="storage_state checksum mismatch")
    _validate_storage_state(plaintext)

    target = Path(os.getenv("TELEMOST_AUTH_STATE_PATH", "data/auth/yandex-session.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name("yandex-session.previous.json")
    temporary = target.with_suffix(target.suffix + ".tmp")
    if target.exists():
        backup.write_bytes(target.read_bytes())
        os.chmod(backup, 0o600)
    try:
        with temporary.open("wb") as stream:
            stream.write(plaintext)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "status": "updated",
        "target_id": target_id,
        "request_id": envelope.get("request_id"),
        "sha256": digest,
        "size": len(plaintext),
    }
