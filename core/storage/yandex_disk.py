import json
import os
import shutil
import http.client
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.storage.meeting_storage import ASTRAKHAN_TZ, DEFAULT_RECORDINGS_DIR


API_BASE = "https://cloud-api.yandex.net/v1/disk/resources"
DEFAULT_BASE_PATH = "/Материалы встреч/Telemost Bot"


@dataclass(frozen=True)
class YandexDiskConfig:
    enabled: bool
    token: str
    base_path: str
    delete_local_after_upload: bool
    errors_file: Path

    @classmethod
    def from_env(cls, root_dir: Path) -> "YandexDiskConfig":
        enabled = os.getenv("YANDEX_DISK_UPLOAD_ENABLED", "0").lower() in (
            "1",
            "true",
            "yes",
        )
        token = os.getenv("YANDEX_DISK_TOKEN", "").strip().strip('"')
        base_path = os.getenv("YANDEX_DISK_BASE_PATH", DEFAULT_BASE_PATH).strip()
        delete_local = os.getenv("YANDEX_DISK_DELETE_LOCAL_AFTER_UPLOAD", "0").lower() in (
            "1",
            "true",
            "yes",
        )
        errors_file = root_dir / os.getenv(
            "YANDEX_DISK_UPLOAD_ERRORS_FILE",
            "yandex_disk_upload_errors.jsonl",
        )
        return cls(
            enabled=enabled,
            token=token,
            base_path=base_path,
            delete_local_after_upload=delete_local,
            errors_file=errors_file,
        )


class YandexDiskUploadError(RuntimeError):
    pass


class YandexDiskClient:
    def __init__(self, token: str, timeout_seconds: int = 120):
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        headers: dict | None = None,
    ) -> dict:
        request_headers = {"Authorization": f"OAuth {self.token}"}
        if headers:
            request_headers.update(headers)
        req = urllib.request.Request(url, data=data, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = resp.read()
                if not body:
                    return {}
                return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise YandexDiskUploadError(
                f"Yandex Disk HTTP {error.code} {error.reason}: {detail}"
            ) from error
        except Exception as error:
            raise YandexDiskUploadError(str(error)) from error

    @staticmethod
    def _normalize_remote_path(path: str) -> str:
        value = path.strip()
        if value.startswith("disk:"):
            value = value.removeprefix("disk:")
        if not value.startswith("/"):
            value = "/" + value
        return value.rstrip("/") or "/"

    @classmethod
    def _api_path(cls, path: str) -> str:
        return "disk:" + cls._normalize_remote_path(path)

    def _resource_url(self, path: str) -> str:
        return f"{API_BASE}?{urllib.parse.urlencode({'path': self._api_path(path)})}"

    def ensure_dir(self, remote_path: str) -> None:
        normalized = self._normalize_remote_path(remote_path)
        if normalized == "/":
            return

        current = ""
        for part in normalized.strip("/").split("/"):
            current += "/" + part
            url = self._resource_url(current)
            try:
                self._request("PUT", url)
            except YandexDiskUploadError as error:
                message = str(error)
                if "HTTP 409" not in message:
                    raise

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        upload_url = (
            f"{API_BASE}/upload?"
            f"{urllib.parse.urlencode({'path': self._api_path(remote_path), 'overwrite': 'true'})}"
        )
        upload_info = self._request("GET", upload_url)
        href = upload_info.get("href")
        if not href:
            raise YandexDiskUploadError(f"Yandex Disk upload href not found for {remote_path}")

        self._put_file_stream(local_path, href)

    def _put_file_stream(self, local_path: Path, href: str) -> None:
        parsed = urllib.parse.urlparse(href)
        connection = http.client.HTTPSConnection(parsed.netloc, timeout=self.timeout_seconds)
        target = urllib.parse.urlunparse(("", "", parsed.path, parsed.params, parsed.query, ""))
        try:
            connection.putrequest("PUT", target)
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Content-Length", str(local_path.stat().st_size))
            connection.endheaders()
            with local_path.open("rb") as file_obj:
                while True:
                    chunk = file_obj.read(1024 * 1024)
                    if not chunk:
                        break
                    connection.send(chunk)
            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            if response.status >= 400:
                raise YandexDiskUploadError(
                    f"Yandex Disk upload HTTP {response.status} {response.reason}: {body}"
                )
        finally:
            connection.close()

    def upload_folder(self, local_dir: Path, remote_dir: str) -> int:
        if not local_dir.exists() or not local_dir.is_dir():
            raise YandexDiskUploadError(f"Local meeting folder not found: {local_dir}")

        self.ensure_dir(remote_dir)
        uploaded = 0
        for local_path in sorted(local_dir.rglob("*")):
            if not local_path.is_file():
                continue
            relative = local_path.relative_to(local_dir).as_posix()
            remote_path = self._normalize_remote_path(remote_dir) + "/" + relative
            parent = str(Path(remote_path).parent).replace("\\", "/")
            self.ensure_dir(parent)
            self.upload_file(local_path, remote_path)
            uploaded += 1
        return uploaded


class YandexDiskUploader:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.config = YandexDiskConfig.from_env(root_dir)

    def _remote_meeting_dir(self, meeting_dir: Path) -> str:
        try:
            relative = meeting_dir.relative_to(self.root_dir / DEFAULT_RECORDINGS_DIR)
        except ValueError:
            relative = Path(meeting_dir.name)
        relative_path = relative.as_posix()
        base_path = YandexDiskClient._normalize_remote_path(self.config.base_path)
        return base_path + "/" + relative_path

    def _append_error(self, meeting_dir: Path, remote_dir: str, error: Exception) -> None:
        self.config.errors_file.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "failed_at": datetime.now(ASTRAKHAN_TZ).isoformat(),
            "meeting_folder": str(meeting_dir),
            "meeting_title": meeting_dir.name,
            "remote_path": remote_dir,
            "error": str(error),
        }
        with self.config.errors_file.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")

    def finalize_meeting_folder(self, meeting_dir: Path) -> bool:
        if not self.config.enabled:
            return False
        if not self.config.token:
            remote_dir = self._remote_meeting_dir(meeting_dir)
            self._append_error(meeting_dir, remote_dir, YandexDiskUploadError("YANDEX_DISK_TOKEN is not set"))
            return False

        remote_dir = self._remote_meeting_dir(meeting_dir)
        try:
            client = YandexDiskClient(self.config.token)
            uploaded_files = client.upload_folder(meeting_dir, remote_dir)
            if uploaded_files <= 0:
                raise YandexDiskUploadError(f"No files uploaded from {meeting_dir}")
            if self.config.delete_local_after_upload:
                shutil.rmtree(meeting_dir)
            print(
                "[YandexDisk] Uploaded meeting folder: "
                f"{meeting_dir} -> {remote_dir} ({uploaded_files} files)"
            )
            return True
        except Exception as error:
            self._append_error(meeting_dir, remote_dir, error)
            print(f"[YandexDisk] Upload failed for {meeting_dir}: {error}")
            return False
