import json
import os
import sys
import subprocess
import glob
import time
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
 
class TranscriptionAdapter:
    def __init__(self, api_key: str, similarity_mode: str = "local"):
        self.api_key = api_key
        self.similarity_mode = similarity_mode
        self.base_dir = Path(__file__).resolve().parents[2]
        self.job_script = self.base_dir / "transcription_service" / "run_pyannote_job.py"

    @staticmethod
    def _find_job_json(audio_path: Path) -> Path | None:
        candidates = sorted(audio_path.parent.glob(f"{audio_path.stem}_pyannote_*.json"))
        for path in candidates:
            if path.name.endswith("_summary.json"):
                continue
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("jobId"):
                return path
        return None

    @staticmethod
    def _is_retryable_failure(stderr: str) -> bool:
        retryable_markers = (
            "TimeoutError",
            "timed out",
            "The read operation timed out",
            "Temporary failure",
            "Connection reset",
            "Remote end closed connection",
        )
        return any(marker.lower() in stderr.lower() for marker in retryable_markers)
 
    def submit_job(self, audio_path: str, target_speakers: Optional[int] = None, timeout_seconds: int = 14400) -> str:
        """
        Запускает транскрипцию и возвращает job_id.
        """
        load_dotenv()  # загружаем .env
 
        audio_path = Path(audio_path).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
 
        # Проверяем, есть ли уже результат
        existing_json = self._find_job_json(audio_path)
        if existing_json:
            with open(existing_json, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            job_id = data.get("jobId")
            print(f"[Adapter] Found existing job: {job_id}")
            return job_id
 
        # Формируем команду
        env = os.environ.copy()
        env["PYANNOTE_API_KEY"] = self.api_key
 
        cmd = [sys.executable, str(self.job_script), str(audio_path)]
        if target_speakers is not None and target_speakers > 0:
            cmd.extend(["--speakers", str(target_speakers)])
        cmd.extend(["--timeout-seconds", str(timeout_seconds)])
        http_timeout_seconds = os.getenv("PYANNOTE_HTTP_TIMEOUT_SECONDS")
        if http_timeout_seconds:
            cmd.extend(["--http-timeout-seconds", http_timeout_seconds])
 
        print(f"[Adapter] Submitting job: {' '.join(cmd)}")
 
        # Запускаем процесс и ЖДЁМ его завершения (только для получения job_id)
        # Используем subprocess.run, а не Popen, чтобы дождаться завершения
        max_attempts = int(os.getenv("PYANNOTE_SUBMIT_MAX_ATTEMPTS", "3"))
        result = None
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                print(f"[Adapter] Retrying pyannote job, attempt {attempt}/{max_attempts}")
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=timeout_seconds
                )
            except subprocess.TimeoutExpired as e:
                stderr = str(e)
                print(f"[Adapter] Process timed out: {stderr}")
                if attempt >= max_attempts:
                    raise RuntimeError(f"Pyannote job failed: {stderr}") from e
                time.sleep(min(30, 2 ** attempt))
                continue

            print(f"[Adapter] Process finished with code {result.returncode}")

            if result.returncode == 0:
                break

            print(f"[Adapter] STDERR: {result.stderr}")
            if not self._is_retryable_failure(result.stderr) or attempt >= max_attempts:
                raise RuntimeError(f"Pyannote job failed: {result.stderr}")
            time.sleep(min(30, 2 ** attempt))
 
        if result is None or result.returncode != 0:
            raise RuntimeError("Pyannote job failed without process result")
 
        # Ищем созданный JSON-файл
        job_json = self._find_job_json(audio_path)
        if not job_json:
            # Если файла нет, смотрим, не был ли он создан с другим именем
            all_json = glob.glob(str(audio_path.parent / "*.json"))
            print(f"[Adapter] Found JSON files: {all_json}")
            raise RuntimeError("No JSON file found after job")
 
        with open(job_json, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
            job_id = data.get("jobId")
            if not job_id:
                raise RuntimeError("No jobId found in JSON")
 
        print(f"[Adapter] Job submitted successfully: {job_id}")
        return job_id
