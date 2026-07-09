import json
import os
import shutil
import http.client
import re
import time
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
    upload_max_attempts: int
    upload_retry_delay_seconds: int

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
        upload_max_attempts = int(os.getenv("YANDEX_DISK_UPLOAD_MAX_ATTEMPTS", "3"))
        upload_retry_delay_seconds = int(os.getenv("YANDEX_DISK_UPLOAD_RETRY_DELAY_SECONDS", "20"))
        return cls(
            enabled=enabled,
            token=token,
            base_path=base_path,
            delete_local_after_upload=delete_local,
            errors_file=errors_file,
            upload_max_attempts=max(1, upload_max_attempts),
            upload_retry_delay_seconds=max(0, upload_retry_delay_seconds),
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

    def delete_resource(self, remote_path: str, permanently: bool = True) -> None:
        url = (
            f"{API_BASE}?"
            f"{urllib.parse.urlencode({'path': self._api_path(remote_path), 'permanently': str(permanently).lower()})}"
        )
        try:
            self._request("DELETE", url)
        except YandexDiskUploadError as error:
            if "HTTP 404" not in str(error):
                raise

    def move_resource(self, source_path: str, target_path: str, overwrite: bool = True) -> None:
        url = (
            f"{API_BASE}/move?"
            f"{urllib.parse.urlencode({
                'from': self._api_path(source_path),
                'path': self._api_path(target_path),
                'overwrite': str(overwrite).lower(),
            })}"
        )
        self._request("POST", url)

    def get_resource(self, remote_path: str, limit: int = 1000) -> dict | None:
        url = (
            f"{API_BASE}?"
            f"{urllib.parse.urlencode({'path': self._api_path(remote_path), 'limit': str(limit)})}"
        )
        try:
            return self._request("GET", url)
        except YandexDiskUploadError as error:
            if "HTTP 404" in str(error):
                return None
            raise

    def list_dir_names(self, remote_path: str) -> list[str]:
        resource = self.get_resource(remote_path)
        if not resource:
            return []
        embedded = resource.get("_embedded") or {}
        items = embedded.get("items") or []
        return [item.get("name", "") for item in items if item.get("name")]

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

    def upload_folder_atomically(self, local_dir: Path, remote_dir: str) -> int:
        normalized_remote_dir = self._normalize_remote_path(remote_dir)
        upload_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        temp_dir = (
            self._normalize_remote_path(str(Path(normalized_remote_dir).parent).replace("\\", "/"))
            + "/.uploading/"
            + Path(normalized_remote_dir).name
            + f"__{upload_id}"
        )

        try:
            self.delete_resource(temp_dir)
            uploaded = self.upload_folder(local_dir, temp_dir)
            if uploaded <= 0:
                raise YandexDiskUploadError(f"No files uploaded from {local_dir}")
            self.move_resource(temp_dir, normalized_remote_dir, overwrite=True)
            return uploaded
        except Exception:
            try:
                self.delete_resource(temp_dir)
            except Exception:
                pass
            raise


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

    def _remote_day_dir(self, meeting_dir: Path) -> str:
        try:
            relative = meeting_dir.relative_to(self.root_dir / DEFAULT_RECORDINGS_DIR)
            parts = relative.parts
            if len(parts) >= 3:
                return (
                    YandexDiskClient._normalize_remote_path(self.config.base_path)
                    + "/"
                    + "/".join(parts[:3])
                )
        except ValueError:
            pass
        return str(Path(self._remote_meeting_dir(meeting_dir)).parent).replace("\\", "/")

    @staticmethod
    def _meeting_number_from_name(name: str) -> int | None:
        import re

        match = re.search(r"(?:^|__)meeting-(\d+)(?:__|$)", name)
        return int(match.group(1)) if match else None

    def latest_remote_meeting_number_for_day_parts(self, year: str, month: str, day: str) -> int:
        if not self.config.enabled or not self.config.token:
            return 0
        remote_day_dir = (
            YandexDiskClient._normalize_remote_path(self.config.base_path)
            + f"/{year}/{month}/{day}"
        )
        try:
            client = YandexDiskClient(self.config.token)
            numbers = [
                number
                for name in client.list_dir_names(remote_day_dir)
                for number in [self._meeting_number_from_name(name)]
                if number is not None
            ]
            return max(numbers) if numbers else 0
        except Exception as error:
            self._append_error(Path(f"{year}/{month}/{day}"), remote_day_dir, error)
            return 0

    def _remote_meeting_exists(self, client: YandexDiskClient, remote_dir: str) -> bool:
        return client.get_resource(remote_dir) is not None

    def _renumber_local_meeting_dir_if_needed(self, meeting_dir: Path, client: YandexDiskClient) -> Path:
        remote_dir = self._remote_meeting_dir(meeting_dir)

        remote_day_dir = self._remote_day_dir(meeting_dir)
        names = client.list_dir_names(remote_day_dir)
        remote_numbers = [
            number
            for name in names
            for number in [self._meeting_number_from_name(name)]
            if number is not None
        ]
        local_numbers = [
            number
            for path in meeting_dir.parent.iterdir()
            if path.is_dir()
            for number in [self._meeting_number_from_name(path.name)]
            if number is not None
        ]
        current_number = self._meeting_number_from_name(meeting_dir.name)
        latest_remote = max(remote_numbers) if remote_numbers else 0
        remote_path_exists = self._remote_meeting_exists(client, remote_dir)

        if current_number and current_number > latest_remote and not remote_path_exists:
            return meeting_dir

        next_number = max(remote_numbers + local_numbers + [0]) + 1
        new_name = re.sub(
            r"(?:^|__)meeting-\d+(?=__|$)",
            lambda match: match.group(0).replace(
                re.search(r"meeting-\d+", match.group(0)).group(0),
                f"meeting-{next_number}",
            ),
            meeting_dir.name,
            count=1,
        )
        if new_name == meeting_dir.name:
            new_name = f"{meeting_dir.name}__meeting-{next_number}"
        new_dir = meeting_dir.with_name(new_name)
        meeting_dir.rename(new_dir)
        print(f"[YandexDisk] Renamed local meeting folder before upload: {meeting_dir} -> {new_dir}")
        return new_dir

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

    @staticmethod
    def is_meeting_folder_complete(meeting_dir: Path) -> bool:
        if not meeting_dir.exists() or not meeting_dir.is_dir():
            return False

        status_path = meeting_dir / "transcription_status.json"
        if not status_path.exists():
            return False
        try:
            status_data = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            return False

        if status_data.get("status") != "completed":
            return False

        required_files = [
            meeting_dir / "meeting_time.json",
            meeting_dir / "recording_meeting.wav",
            status_path,
        ]
        if any(not path.exists() or not path.is_file() or path.stat().st_size <= 0 for path in required_files):
            return False

        pyannote_json = [
            path
            for path in meeting_dir.glob("recording_meeting_pyannote_*.json")
            if not path.name.endswith("_summary.json")
        ]
        pyannote_txt = list(meeting_dir.glob("recording_meeting_pyannote_*.txt"))
        return bool(pyannote_json and pyannote_txt)

    def finalize_meeting_folder(self, meeting_dir: Path) -> bool:
        if not self.config.enabled:
            return False
        if not self.is_meeting_folder_complete(meeting_dir):
            print(f"[YandexDisk] Meeting folder is not complete yet, upload skipped: {meeting_dir}")
            return False
        if not self.config.token:
            remote_dir = self._remote_meeting_dir(meeting_dir)
            self._append_error(meeting_dir, remote_dir, YandexDiskUploadError("YANDEX_DISK_TOKEN is not set"))
            return False

        remote_dir = self._remote_meeting_dir(meeting_dir)
        try:
            client = YandexDiskClient(self.config.token)
            meeting_dir = self._renumber_local_meeting_dir_if_needed(meeting_dir, client)
            remote_dir = self._remote_meeting_dir(meeting_dir)
            uploaded_files = 0
            last_error = None
            for attempt in range(1, self.config.upload_max_attempts + 1):
                try:
                    uploaded_files = client.upload_folder_atomically(meeting_dir, remote_dir)
                    break
                except Exception as error:
                    last_error = error
                    if attempt >= self.config.upload_max_attempts:
                        raise
                    print(
                        "[YandexDisk] Upload attempt failed, retrying: "
                        f"{meeting_dir} attempt {attempt}/{self.config.upload_max_attempts}: {error}"
                    )
                    time.sleep(self.config.upload_retry_delay_seconds)
            if uploaded_files <= 0:
                raise YandexDiskUploadError(f"No files uploaded from {meeting_dir}: {last_error}")
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
