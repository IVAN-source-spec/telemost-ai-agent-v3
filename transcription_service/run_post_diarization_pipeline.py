import argparse
import json
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local post-diarization pipeline: similarity, speaker stabilization, and optional aliases."
    )
    parser.add_argument("media_path", help="Path to the original media file")
    parser.add_argument("job_json_path", help="Path to saved pyannote job JSON")
    parser.add_argument(
        "--target-speakers",
        type=int,
        default=None,
        help="Expected number of real speakers for stabilization",
    )
    parser.add_argument(
        "--similarity-mode",
        choices=["api", "local"],
        default="local",
        help="Voice similarity mode for run_voiceprint_similarity.py",
    )
    parser.add_argument(
        "--similarity-report-path",
        default=None,
        help="Optional existing similarity report JSON. If omitted, it will be generated.",
    )
    parser.add_argument(
        "--alias",
        action="append",
        default=[],
        help="Optional alias mapping in LABEL=VALUE form, e.g. PERSON_01=Евгений",
    )
    parser.add_argument(
        "--header-note",
        action="append",
        default=[],
        help="Optional note line for the named transcript header",
    )
    return parser


def run_command(command: list[str]) -> list[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        details = stderr or stdout or "Command failed"
        raise RuntimeError(details)
    stdout = result.stdout or ""
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def extract_path_line(lines: list[str]) -> Path:
    for line in reversed(lines):
        candidate = line
        if ":" in line:
            _, tail = line.split(":", 1)
            if "\\" in tail or "/" in tail:
                candidate = tail.strip()
        path = Path(candidate).expanduser()
        if path.exists():
            return path.resolve()
    raise FileNotFoundError(f"Could not find a valid path in command output: {lines}")


def main() -> int:
    args = build_parser().parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    base_dir = Path(__file__).resolve().parent
    media_path = Path(args.media_path).expanduser().resolve()
    job_json_path = Path(args.job_json_path).expanduser().resolve()

    if not media_path.exists():
        raise FileNotFoundError(f"Media file not found: {media_path}")
    if not job_json_path.exists():
        raise FileNotFoundError(f"Job JSON not found: {job_json_path}")

    python_exe = sys.executable
    similarity_script = base_dir / "run_voiceprint_similarity.py"
    stabilizer_script = base_dir / "run_speaker_stabilizer.py"
    aliases_script = base_dir / "apply_aliases_to_transcript.py"

    if args.similarity_report_path:
        similarity_report_path = Path(args.similarity_report_path).expanduser().resolve()
    else:
        similarity_command = [
            python_exe,
            str(similarity_script),
            str(media_path),
            str(job_json_path),
            "--mode",
            args.similarity_mode,
        ]
        similarity_output = run_command(similarity_command)
        similarity_report_path = extract_path_line(similarity_output)

    if not similarity_report_path.exists():
        raise FileNotFoundError(f"Similarity report not found: {similarity_report_path}")

    stabilizer_command = [
        python_exe,
        str(stabilizer_script),
        str(job_json_path),
        str(similarity_report_path),
    ]
    if args.target_speakers is not None:
        stabilizer_command.extend(["--target-speakers", str(args.target_speakers)])

    stabilizer_output = run_command(stabilizer_command)
    stabilized_candidates_path = Path(stabilizer_output[0]).resolve()
    stabilized_transcript_path = Path(stabilizer_output[1]).resolve()

    named_transcript_path = None
    if args.alias:
        named_transcript_path = stabilized_transcript_path.with_name(
            f"{stabilized_transcript_path.stem}_named.txt"
        )
        alias_command = [
            python_exe,
            str(aliases_script),
            str(stabilized_transcript_path),
            str(named_transcript_path),
        ]
        for alias in args.alias:
            alias_command.extend(["--alias", alias])
        for note in args.header_note:
            alias_command.extend(["--header-note", note])
        run_command(alias_command)

    result = {
        "media_path": str(media_path),
        "job_json_path": str(job_json_path),
        "similarity_mode": args.similarity_mode,
        "similarity_report_path": str(similarity_report_path),
        "target_speakers": args.target_speakers,
        "stabilized_candidates_path": str(stabilized_candidates_path),
        "stabilized_transcript_path": str(stabilized_transcript_path),
        "named_transcript_path": str(named_transcript_path) if named_transcript_path else None,
    }

    pipeline_report_path = job_json_path.with_name(f"{job_json_path.stem}_post_pipeline.json")
    pipeline_report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )

    print(similarity_report_path)
    print(stabilized_candidates_path)
    print(stabilized_transcript_path)
    if named_transcript_path:
        print(named_transcript_path)
    print(pipeline_report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
