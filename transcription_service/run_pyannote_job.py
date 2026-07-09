import argparse
import collections
import json
import os
import statistics
import time
import urllib.request
from pathlib import Path


API_BASE = "https://api.pyannote.ai/v1"
DEFAULT_MODEL = "precision-2"
DEFAULT_ACTIVE_DURATION_THRESHOLD_SEC = 15.0
DEFAULT_ACTIVE_TURNS_THRESHOLD = 5
DEFAULT_PRIMARY_DURATION_THRESHOLD_SEC = 45.0
DEFAULT_PRIMARY_TURNS_THRESHOLD = 12
DEFAULT_ARTIFACT_DURATION_THRESHOLD_SEC = 30.0
DEFAULT_ARTIFACT_TURNS_THRESHOLD = 10
DEFAULT_SHORT_TURN_THRESHOLD_SEC = 1.2
DEFAULT_ARTIFACT_SHORT_TURN_RATIO = 0.6


def extract_text_letters(text: str) -> tuple[int, int]:
    cyrillic = 0
    latin = 0
    for char in text.lower():
        code = ord(char)
        if 0x0430 <= code <= 0x044f or code == 0x0451:
            cyrillic += 1
        elif 0x0061 <= code <= 0x007a:
            latin += 1
    return cyrillic, latin


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run pyannote diarization + transcription for a local audio file."
    )
    parser.add_argument("audio_path", nargs="?", help="Path to local audio file")
    parser.add_argument(
        "--speakers",
        type=int,
        default=None,
        help="Fixed number of speakers. Omit for automatic detection.",
    )
    parser.add_argument(
        "--min-speakers",
        type=int,
        default=None,
        help="Optional minimum number of speakers for auto detection.",
    )
    parser.add_argument(
        "--max-speakers",
        type=int,
        default=None,
        help="Optional maximum number of speakers for auto detection.",
    )
    parser.add_argument("--poll-seconds", type=int, default=10, help="Polling interval in seconds")
    parser.add_argument("--timeout-seconds", type=int, default=1800, help="Overall timeout in seconds")
    parser.add_argument(
        "--http-timeout-seconds",
        type=int,
        default=int(os.getenv("PYANNOTE_HTTP_TIMEOUT_SECONDS", "180")),
        help="HTTP read timeout for pyannote API requests.",
    )
    parser.add_argument("--media-url", default="media://telemost/test-audio.mp3", help="Remote media URL alias in pyannote")
    parser.add_argument(
        "--from-job-json",
        default=None,
        help="Reuse an existing pyannote job JSON and only rebuild local summary/transcript files.",
    )
    return parser


def request_json(
    url: str,
    api_key: str,
    payload: dict | None = None,
    *,
    timeout_seconds: int = 180,
) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if body is not None:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upload_file(audio_path: Path, signed_url: str) -> None:
    req = urllib.request.Request(
        signed_url,
        data=audio_path.read_bytes(),
        method="PUT",
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Upload failed with status {resp.status}")


def render_turns(job: dict) -> str:
    turns = job.get("output", {}).get("turnLevelTranscription") or []
    lines = []
    for turn in turns:
        start = f"{float(turn.get('start', 0)):.1f}"
        end = f"{float(turn.get('end', 0)):.1f}"
        speaker = turn.get("speaker", "UNKNOWN")
        text = str(turn.get("text", "")).strip()
        lines.append(f"[{start}-{end}] {speaker}: {text}")
    return "\n".join(lines)


def build_speaker_stats(job: dict) -> list[dict]:
    diarization = job.get("output", {}).get("exclusiveDiarization") or job.get("output", {}).get("diarization") or []
    turns = job.get("output", {}).get("turnLevelTranscription") or []

    durations = collections.defaultdict(float)
    for segment in diarization:
        speaker = segment.get("speaker", "UNKNOWN")
        start = float(segment.get("start", 0))
        end = float(segment.get("end", 0))
        durations[speaker] += max(0.0, end - start)

    turn_groups = collections.defaultdict(list)
    for turn in turns:
        speaker = turn.get("speaker", "UNKNOWN")
        turn_groups[speaker].append(turn)

    speaker_stats = []
    for speaker in sorted(set(durations.keys()) | set(turn_groups.keys())):
        speaker_turns = turn_groups.get(speaker, [])
        turn_durations = [max(0.0, float(turn.get("end", 0)) - float(turn.get("start", 0))) for turn in speaker_turns]
        short_turns = [d for d in turn_durations if d <= DEFAULT_SHORT_TURN_THRESHOLD_SEC]
        all_text = " ".join(str(turn.get("text", "")).strip() for turn in speaker_turns)
        cyrillic_letters, latin_letters = extract_text_letters(all_text)
        total_letters = cyrillic_letters + latin_letters
        cyrillic_ratio = round(cyrillic_letters / total_letters, 3) if total_letters else 0.0
        latin_ratio = round(latin_letters / total_letters, 3) if total_letters else 0.0

        stats = {
            "speaker": speaker,
            "duration_sec": round(durations.get(speaker, 0.0), 2),
            "turns_count": len(speaker_turns),
            "avg_turn_duration_sec": round(sum(turn_durations) / len(turn_durations), 2) if turn_durations else 0.0,
            "median_turn_duration_sec": round(statistics.median(turn_durations), 2) if turn_durations else 0.0,
            "short_turn_ratio": round(len(short_turns) / len(turn_durations), 3) if turn_durations else 0.0,
            "cyrillic_ratio": cyrillic_ratio,
            "latin_ratio": latin_ratio,
            "sample_text": all_text[:200],
        }
        speaker_stats.append(stats)

    return speaker_stats


def classify_speakers(speaker_stats: list[dict]) -> dict:
    primary = []
    secondary = []
    possible_artifact = []

    for stats in speaker_stats:
        is_possible_artifact = (
            stats["duration_sec"] <= DEFAULT_ARTIFACT_DURATION_THRESHOLD_SEC
            and stats["turns_count"] <= DEFAULT_ARTIFACT_TURNS_THRESHOLD
            and (
                stats["short_turn_ratio"] >= DEFAULT_ARTIFACT_SHORT_TURN_RATIO
                or (
                    stats["latin_ratio"] > stats["cyrillic_ratio"]
                    and stats["latin_ratio"] >= 0.55
                )
            )
        )

        if is_possible_artifact:
            possible_artifact.append(stats)
            continue

        is_primary = (
            stats["duration_sec"] >= DEFAULT_PRIMARY_DURATION_THRESHOLD_SEC
            or stats["turns_count"] >= DEFAULT_PRIMARY_TURNS_THRESHOLD
        )
        if is_primary:
            primary.append(stats)
        else:
            secondary.append(stats)

    return {
        "primary": primary,
        "secondary": secondary,
        "possible_artifact": possible_artifact,
        "normalized_speakers_count": len(primary) + len(secondary),
    }


def build_summary(job: dict, request_payload: dict) -> dict:
    diarization = job.get("output", {}).get("exclusiveDiarization") or job.get("output", {}).get("diarization") or []
    turns = job.get("output", {}).get("turnLevelTranscription") or []

    speaker_stats = build_speaker_stats(job)
    classified = classify_speakers(speaker_stats)
    detected_speakers = [item["speaker"] for item in speaker_stats]
    turn_counts = {item["speaker"]: item["turns_count"] for item in speaker_stats}
    durations = {item["speaker"]: item["duration_sec"] for item in speaker_stats}

    likely_active = [
        {"speaker": item["speaker"], "duration_sec": item["duration_sec"], "turns_count": item["turns_count"]}
        for item in speaker_stats
        if item["duration_sec"] >= DEFAULT_ACTIVE_DURATION_THRESHOLD_SEC or item["turns_count"] >= DEFAULT_ACTIVE_TURNS_THRESHOLD
    ]
    likely_minor = [
        {"speaker": item["speaker"], "duration_sec": item["duration_sec"], "turns_count": item["turns_count"]}
        for item in speaker_stats
        if not (item["duration_sec"] >= DEFAULT_ACTIVE_DURATION_THRESHOLD_SEC or item["turns_count"] >= DEFAULT_ACTIVE_TURNS_THRESHOLD)
    ]

    return {
        "status": job.get("status"),
        "job_id": job.get("jobId"),
        "request": request_payload,
        "detected_speakers_count": len(detected_speakers),
        "detected_speakers": detected_speakers,
        "recommended_speakers_count": classified["normalized_speakers_count"],
        "recommended_speakers": [
            item["speaker"] for item in classified["primary"] + classified["secondary"]
        ],
        "speaker_turn_counts": turn_counts,
        "speaker_durations_sec": durations,
        "transcript_turns_count": len(turns),
        "likely_active_speakers": likely_active,
        "likely_minor_speakers": likely_minor,
        "active_speaker_rule": {
            "min_duration_sec": DEFAULT_ACTIVE_DURATION_THRESHOLD_SEC,
            "min_turns_count": DEFAULT_ACTIVE_TURNS_THRESHOLD,
        },
        "speaker_stats": speaker_stats,
        "classification": classified,
        "classification_rules": {
            "primary_min_duration_sec": DEFAULT_PRIMARY_DURATION_THRESHOLD_SEC,
            "primary_min_turns_count": DEFAULT_PRIMARY_TURNS_THRESHOLD,
            "artifact_max_duration_sec": DEFAULT_ARTIFACT_DURATION_THRESHOLD_SEC,
            "artifact_max_turns_count": DEFAULT_ARTIFACT_TURNS_THRESHOLD,
            "artifact_short_turn_threshold_sec": DEFAULT_SHORT_TURN_THRESHOLD_SEC,
            "artifact_min_short_turn_ratio": DEFAULT_ARTIFACT_SHORT_TURN_RATIO,
            "artifact_latin_ratio_note": "Possible artifact if short/rare speaker is dominated by Latin text.",
        },
    }


def save_outputs(audio_path: Path, job_id: str, job: dict, request_payload: dict) -> tuple[Path, Path | None, Path]:
    out_base = audio_path.with_name(f"{audio_path.stem}_pyannote_{job_id}")
    json_path = out_base.with_suffix(".json")
    txt_path = out_base.with_suffix(".txt")
    summary_path = out_base.with_name(f"{out_base.name}_summary.json")

    json_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    transcript = render_turns(job)
    summary = build_summary(job, request_payload)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    if transcript:
        txt_path.write_text(transcript, encoding="utf-8-sig")
        return json_path, txt_path, summary_path

    return json_path, None, summary_path


def load_job_json(job_json_path: Path) -> dict:
    return json.loads(job_json_path.read_text(encoding="utf-8-sig"))


def rebuild_outputs_from_job_json(job_json_path: Path) -> tuple[Path, Path | None, Path]:
    job = load_job_json(job_json_path)
    job_id = job.get("jobId")
    if not job_id:
        raise SystemExit(f"jobId not found in {job_json_path}")

    source_stem = job_json_path.stem
    marker = f"_pyannote_{job_id}"
    if marker not in source_stem:
        raise SystemExit(
            f"Cannot infer source audio name from {job_json_path.name}. "
            f"Expected filename containing {marker}"
        )

    audio_stem = source_stem[: source_stem.index(marker)]
    sibling_mp3 = job_json_path.with_name(f"{audio_stem}.mp3")
    sibling_webm = job_json_path.with_name(f"{audio_stem}.webm")
    audio_path = sibling_mp3 if sibling_mp3.exists() else sibling_webm
    if not audio_path.exists():
        audio_path = job_json_path.with_name(audio_stem)

    request_payload = job.get("parameters") or {}
    return save_outputs(audio_path, job_id, job, request_payload)


def build_diarize_payload(args: argparse.Namespace) -> dict:
    payload = {
        "url": args.media_url,
        "model": DEFAULT_MODEL,
        "exclusive": True,
        "transcription": True,
    }

    if args.speakers is not None:
        payload["numSpeakers"] = args.speakers
    else:
        if args.min_speakers is not None:
            payload["minSpeakers"] = args.min_speakers
        if args.max_speakers is not None:
            payload["maxSpeakers"] = args.max_speakers

    return payload


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.from_job_json:
        job_json_path = Path(args.from_job_json).expanduser().resolve()
        if not job_json_path.exists():
            raise SystemExit(f"Job JSON file not found: {job_json_path}")

        json_path, txt_path, summary_path = rebuild_outputs_from_job_json(job_json_path)
        print("Rebuilt outputs from existing job JSON.")
        print("Saved summary result.")
        if txt_path:
            print("Saved text transcript.")
        return 0

    api_key = os.getenv("PYANNOTE_API_KEY")
    if not api_key:
        raise SystemExit("Set PYANNOTE_API_KEY before running the script.")

    if not args.audio_path:
        raise SystemExit("audio_path is required unless --from-job-json is used.")

    audio_path = Path(args.audio_path).expanduser().resolve()
    if not audio_path.exists():
        raise SystemExit(f"Audio file not found: {audio_path}")

    signed = request_json(
        f"{API_BASE}/media/input",
        api_key,
        {"url": args.media_url},
        timeout_seconds=args.http_timeout_seconds,
    )
    upload_file(audio_path, signed["url"])
    print("Upload completed.")

    request_payload = build_diarize_payload(args)
    created = request_json(
        f"{API_BASE}/diarize",
        api_key,
        request_payload,
        timeout_seconds=args.http_timeout_seconds,
    )
    job_id = created["jobId"]
    print(f"Job created: {job_id}")

    started_at = time.time()
    while time.time() - started_at < args.timeout_seconds:
        job = request_json(
            f"{API_BASE}/jobs/{job_id}",
            api_key,
            timeout_seconds=args.http_timeout_seconds,
        )
        status = str(job.get("status", "")).lower()
        print(f"Job status: {job.get('status')}")

        if status in {"failed", "error"}:
            raise SystemExit(json.dumps(job, ensure_ascii=False, indent=2))

        if status in {"succeeded", "completed"} or job.get("output"):
            json_path, txt_path, summary_path = save_outputs(audio_path, job_id, job, request_payload)
            print("Saved JSON result.")
            print("Saved summary result.")
            if txt_path:
                print("Saved text transcript.")
            else:
                print("No turn-level transcript returned, only JSON was saved.")
            return 0

        time.sleep(args.poll_seconds)

    raise SystemExit("Timeout waiting for job completion.")


if __name__ == "__main__":
    raise SystemExit(main())
