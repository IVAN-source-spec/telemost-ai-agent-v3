from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx


class RemoteTranscriptionError(RuntimeError):
    pass


class RemoteTranscriptionAdapter:
    TERMINAL_STATUSES = {"succeeded", "failed"}

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        request_timeout_seconds: float = 120.0,
        upload_timeout_seconds: float = 900.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token.strip()
        if not self.base_url:
            raise ValueError("Remote transcription base_url is required")
        if not self.api_token:
            raise ValueError("Remote transcription api_token is required")
        self.request_timeout_seconds = request_timeout_seconds
        self.upload_timeout_seconds = upload_timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        }

    @staticmethod
    def _json_response(response: httpx.Response) -> dict[str, Any]:
        if response.is_error:
            details = response.text.strip().replace("\n", " ")[:800]
            raise RemoteTranscriptionError(
                f"Remote transcription HTTP {response.status_code} for "
                f"{response.request.method} {response.request.url.path}: {details}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise RemoteTranscriptionError("Remote transcription returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise RemoteTranscriptionError("Remote transcription returned unexpected payload")
        return payload

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _idempotency_key(audio_path: Path, data: dict[str, str], content_type: str, content_sha256: str) -> str:
        canonical = json.dumps(
            {
                "filename": audio_path.name,
                "content_type": content_type,
                "fields": data,
                "content_sha256": content_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "telemost-bot-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def submit_job(
        self,
        audio_path: str | Path,
        *,
        target_speakers: int | None = None,
        title: str | None = None,
        meeting_date: str | None = None,
        participants: list[dict[str, Any]] | None = None,
        segment_type: str | None = None,
        result_recipients: list[dict[str, Any] | str] | None = None,
        organizer: dict[str, Any] | None = None,
        mqr_upload: bool | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        path = Path(audio_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)

        data: dict[str, str] = {}
        if target_speakers and target_speakers > 0:
            data["speakers"] = str(target_speakers)
        if title and title.strip():
            data["title"] = title.strip()
        if meeting_date and meeting_date.strip():
            data["meeting_date"] = meeting_date.strip()
        if participants:
            data["participants"] = json.dumps(participants, ensure_ascii=False)
        if segment_type and segment_type.strip():
            data["segment_type"] = segment_type.strip().lower()
        if result_recipients is not None:
            data["result_recipients"] = json.dumps(result_recipients, ensure_ascii=False)
        if organizer:
            data["organizer"] = json.dumps(organizer, ensure_ascii=False)
        if mqr_upload is not None:
            data["mqr_upload"] = "true" if mqr_upload else "false"

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        content_sha256 = self._file_sha256(path)
        idempotency_key = idempotency_key or self._idempotency_key(path, data, content_type, content_sha256)
        timeout = httpx.Timeout(
            connect=min(30.0, self.request_timeout_seconds),
            read=self.request_timeout_seconds,
            write=self.upload_timeout_seconds,
            pool=self.request_timeout_seconds,
        )
        headers = {
            **self._headers(),
            "Idempotency-Key": idempotency_key,
            "X-Content-SHA256": content_sha256,
        }
        with path.open("rb") as file_obj:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{self.base_url}/api/transcriptions",
                    headers=headers,
                    data=data,
                    files={"file": (path.name, file_obj, content_type)},
                )
        payload = self._json_response(response)
        if not str(payload.get("job_id") or "").strip():
            raise RemoteTranscriptionError("Remote transcription accepted upload but returned no job_id")
        return payload

    def get_job(self, job_id: str) -> dict[str, Any]:
        normalized_job_id = job_id.strip()
        if not normalized_job_id:
            raise ValueError("job_id is required")
        with httpx.Client(timeout=self.request_timeout_seconds) as client:
            response = client.get(
                f"{self.base_url}/api/transcriptions/{normalized_job_id}",
                headers=self._headers(),
            )
        return self._json_response(response)

    def retry_job(self, job_id: str) -> dict[str, Any]:
        normalized_job_id = job_id.strip()
        if not normalized_job_id:
            raise ValueError("job_id is required")
        with httpx.Client(timeout=self.request_timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/api/transcriptions/{normalized_job_id}/retry",
                headers=self._headers(),
            )
        return self._json_response(response)

    def download_transcript(self, job_id: str, output_path: str | Path) -> Path:
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.part")
        try:
            with httpx.Client(timeout=self.request_timeout_seconds) as client:
                response = client.get(
                    f"{self.base_url}/api/transcriptions/{job_id.strip()}/transcript",
                    headers={**self._headers(), "Accept": "text/plain"},
                )
            if response.is_error:
                details = response.text.strip().replace("\n", " ")[:800]
                raise RemoteTranscriptionError(
                    f"Remote transcription HTTP {response.status_code} for transcript download: {details}"
                )
            temporary.write_bytes(response.content)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


def build_remote_transcription_adapter_from_env() -> RemoteTranscriptionAdapter:
    base_url = os.getenv("REMOTE_TRANSCRIPTION_BASE_URL") or os.getenv("HERMES_TRANSCRIPTION_BASE_URL", "")
    api_token = os.getenv("REMOTE_TRANSCRIPTION_API_TOKEN") or os.getenv("HERMES_TRANSCRIPTION_API_TOKEN", "")
    return RemoteTranscriptionAdapter(
        base_url=base_url,
        api_token=api_token,
        request_timeout_seconds=float(os.getenv("REMOTE_TRANSCRIPTION_REQUEST_TIMEOUT_SECONDS", "120")),
        upload_timeout_seconds=float(os.getenv("REMOTE_TRANSCRIPTION_UPLOAD_TIMEOUT_SECONDS", "1200")),
    )
