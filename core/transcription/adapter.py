import json
import os
import sys
import subprocess
import glob
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
 
class TranscriptionAdapter:
    def __init__(self, api_key: str, similarity_mode: str = "local"):
        self.api_key = api_key
        self.similarity_mode = similarity_mode
        self.base_dir = Path(__file__).resolve().parents[2]
        self.job_script = self.base_dir / "transcription_service" / "run_pyannote_job.py"
 
    def submit_job(self, audio_path: str, target_speakers: Optional[int] = None, timeout_seconds: int = 14400) -> str:
        """
        Запускает транскрипцию и возвращает job_id.
        """
        load_dotenv()  # загружаем .env
 
        audio_path = Path(audio_path).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
 
        # Проверяем, есть ли уже результат
        existing_json = glob.glob(str(audio_path.parent / f"{audio_path.stem}_pyannote_*.json"))
        if existing_json:
            with open(existing_json[0], 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
                job_id = data.get("jobId")
                if job_id:
                    print(f"[Adapter] Found existing job: {job_id}")
                    return job_id
 
        # Формируем команду
        env = os.environ.copy()
        env["PYANNOTE_API_KEY"] = self.api_key
 
        cmd = [sys.executable, str(self.job_script), str(audio_path)]
        if target_speakers is not None and target_speakers > 0:
            cmd.extend(["--speakers", str(target_speakers)])
        cmd.extend(["--timeout-seconds", str(timeout_seconds)])
 
        print(f"[Adapter] Submitting job: {' '.join(cmd)}")
 
        # Запускаем процесс и ЖДЁМ его завершения (только для получения job_id)
        # Используем subprocess.run, а не Popen, чтобы дождаться завершения
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds
        )
 
        print(f"[Adapter] Process finished with code {result.returncode}")
 
        if result.returncode != 0:
            print(f"[Adapter] STDERR: {result.stderr}")
            raise RuntimeError(f"Pyannote job failed: {result.stderr}")
 
        # Ищем созданный JSON-файл
        json_files = glob.glob(str(audio_path.parent / f"{audio_path.stem}_pyannote_*.json"))
        if not json_files:
            # Если файла нет, смотрим, не был ли он создан с другим именем
            all_json = glob.glob(str(audio_path.parent / "*.json"))
            print(f"[Adapter] Found JSON files: {all_json}")
            raise RuntimeError("No JSON file found after job")
 
        with open(json_files[0], 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
            job_id = data.get("jobId")
            if not job_id:
                raise RuntimeError("No jobId found in JSON")
 
        print(f"[Adapter] Job submitted successfully: {job_id}")
        return job_id