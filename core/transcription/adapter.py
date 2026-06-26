import json
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional
 
class TranscriptionAdapter:
    def __init__(self, api_key: str, similarity_mode: str = "local"):
        self.api_key = api_key
        self.similarity_mode = similarity_mode
        self.base_dir = Path(__file__).resolve().parents[2]
        self.pipeline_script = self.base_dir / "transcription_service" / "run_post_diarization_pipeline.py"
 
    def transcribe(self, audio_path: str, target_speakers: Optional[int] = None, aliases: Optional[dict] = None) -> dict:
        """
        Запускает пайплайн транскрибации и возвращает результат.
        """
        audio_path = Path(audio_path).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
 
        # Проверяем, есть ли уже pyannote JSON рядом
        pyannote_json = audio_path.parent / f"{audio_path.stem}_pyannote_*.json"
        import glob
        existing_json = glob.glob(str(pyannote_json))
        if existing_json:
            job_json = existing_json[0]
        else:
            # Запускаем pyannote job
            job_script = self.base_dir / "transcription_service" / "run_pyannote_job.py"
            env = os.environ.copy()
            env["PYANNOTE_API_KEY"] = self.api_key
 
            result = subprocess.run(
                [sys.executable, str(job_script), str(audio_path)],
                capture_output=True,
                text=True,
                env=env
            )
            if result.returncode != 0:
                raise RuntimeError(f"Pyannote job failed: {result.stderr}")
 
            # Находим созданный JSON
            pyannote_json = audio_path.parent / f"{audio_path.stem}_pyannote_*.json"
            import glob
            json_files = glob.glob(str(pyannote_json))
            if not json_files:
                raise FileNotFoundError("Pyannote JSON not found after job")
            job_json = json_files[0]
 
        # Запускаем пост-обработку
        pipeline_args = [
            sys.executable,
            str(self.pipeline_script),
            str(audio_path),
            job_json,
            "--similarity-mode", self.similarity_mode
        ]
        if target_speakers:
            pipeline_args.extend(["--target-speakers", str(target_speakers)])
        if aliases:
            for label, name in aliases.items():
                pipeline_args.extend(["--alias", f"{label}={name}"])
 
        env = os.environ.copy()
        env["PYANNOTE_API_KEY"] = self.api_key
 
        result = subprocess.run(pipeline_args, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"Post-pipeline failed: {result.stderr}")
 
        # Ищем результат
        post_report = audio_path.parent / f"{audio_path.stem}_post_pipeline.json"
        if not post_report.exists():
            # Пробуем найти по маске
            import glob
            reports = glob.glob(str(audio_path.parent / f"{audio_path.stem}_post_pipeline.json"))
            if reports:
                post_report = Path(reports[0])
            else:
                raise FileNotFoundError("Post-pipeline report not found")
 
        with open(post_report, 'r', encoding='utf-8') as f:
            return json.load(f)