import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path


API_BASE = "https://api.pyannote.ai/v1"
DEFAULT_MODEL = "precision-2"
DEFAULT_MIN_SEGMENT_SECONDS = 1.2
DEFAULT_MAX_SEGMENT_SECONDS = 8.0
DEFAULT_MAX_CLIP_SECONDS = 24.0
DEFAULT_POLL_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_CROSS_MATCH_THRESHOLD = 60


VENDOR_DIR = Path(__file__).resolve().parent / ".vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

import imageio_ffmpeg  # type: ignore
import numpy as np


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create speaker clips from a pyannote job and compare them with pyannote voiceprints."
    )
    parser.add_argument("media_path", help="Path to the original media file used for diarization")
    parser.add_argument("job_json_path", help="Path to the saved pyannote job JSON")
    parser.add_argument(
        "--mode",
        choices=["api", "local"],
        default="api",
        help="Similarity mode: pyannote voiceprint API or local MFCC-based comparison",
    )
    parser.add_argument(
        "--speaker",
        action="append",
        default=[],
        help="Optional speaker id to limit analysis (repeatable)",
    )
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--min-segment-seconds", type=float, default=DEFAULT_MIN_SEGMENT_SECONDS)
    parser.add_argument("--max-segment-seconds", type=float, default=DEFAULT_MAX_SEGMENT_SECONDS)
    parser.add_argument("--max-clip-seconds", type=float, default=DEFAULT_MAX_CLIP_SECONDS)
    parser.add_argument("--cross-match-threshold", type=int, default=DEFAULT_CROSS_MATCH_THRESHOLD)
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep intermediate clip files instead of removing the temp directory",
    )
    return parser


def request_json(url: str, api_key: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if body is not None:
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} for {url}: {details}") from error


def upload_file(local_path: Path, signed_url: str) -> None:
    req = urllib.request.Request(
        signed_url,
        data=local_path.read_bytes(),
        method="PUT",
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Upload failed with status {resp.status}")


def wait_for_job(job_id: str, api_key: str, poll_seconds: int, timeout_seconds: int) -> dict:
    started_at = time.time()
    while time.time() - started_at < timeout_seconds:
        job = request_json(f"{API_BASE}/jobs/{job_id}", api_key)
        status = str(job.get("status", "")).lower()
        if status in {"failed", "error"}:
            raise RuntimeError(json.dumps(job, ensure_ascii=False, indent=2))
        if status in {"succeeded", "completed"} or job.get("output"):
            return job
        time.sleep(poll_seconds)
    raise TimeoutError(f"Timeout waiting for job {job_id}")


def choose_segments_for_speaker(
    segments: list[dict],
    speaker: str,
    min_segment_seconds: float,
    max_segment_seconds: float,
    max_clip_seconds: float,
) -> list[dict]:
    speaker_segments = [
        segment
        for segment in segments
        if segment.get("speaker") == speaker and float(segment.get("end", 0)) > float(segment.get("start", 0))
    ]

    selected = []
    total_seconds = 0.0

    def append_segment(start: float, end: float) -> None:
        nonlocal total_seconds
        if end <= start:
            return
        duration = end - start
        if total_seconds + duration > max_clip_seconds:
            end = start + max(0.0, max_clip_seconds - total_seconds)
            duration = end - start
        if duration <= 0:
            return
        selected.append({"start": round(start, 3), "end": round(end, 3)})
        total_seconds += duration

    for segment in speaker_segments:
        start = float(segment["start"])
        end = float(segment["end"])
        duration = end - start
        if duration < min_segment_seconds:
            continue
        trimmed_end = start + min(duration, max_segment_seconds)
        append_segment(start, trimmed_end)
        if total_seconds >= max_clip_seconds:
            break

    if selected:
        return selected

    for segment in speaker_segments:
        start = float(segment["start"])
        end = float(segment["end"])
        duration = end - start
        if duration <= 0.5:
            continue
        trimmed_end = start + min(duration, max_segment_seconds)
        append_segment(start, trimmed_end)
        if total_seconds >= max_clip_seconds:
            break

    return selected


def run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg command failed")


def build_speaker_clip(
    ffmpeg_exe: str,
    media_path: Path,
    speaker: str,
    selected_segments: list[dict],
    output_dir: Path,
) -> Path:
    speaker_dir = output_dir / speaker
    speaker_dir.mkdir(parents=True, exist_ok=True)

    part_files = []
    for index, segment in enumerate(selected_segments):
        start = float(segment["start"])
        end = float(segment["end"])
        duration = round(end - start, 3)
        if duration <= 0:
            continue

        part_path = speaker_dir / f"part_{index:02d}.wav"
        command = [
            ffmpeg_exe,
            "-y",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(media_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(part_path),
        ]
        run_ffmpeg(command)
        part_files.append(part_path)

    if not part_files:
        raise RuntimeError(f"No clip parts produced for {speaker}")

    final_clip_path = output_dir / f"{speaker}_voiceprint.wav"
    if len(part_files) == 1:
        shutil.copyfile(part_files[0], final_clip_path)
        return final_clip_path

    concat_list_path = speaker_dir / "concat.txt"
    concat_lines = [f"file '{part.as_posix()}'" for part in part_files]
    concat_list_path.write_text("\n".join(concat_lines), encoding="utf-8")

    concat_command = [
        ffmpeg_exe,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        str(final_clip_path),
    ]
    run_ffmpeg(concat_command)
    return final_clip_path


def upload_media_alias(local_path: Path, alias: str, api_key: str) -> str:
    signed = request_json(f"{API_BASE}/media/input", api_key, {"url": alias})
    upload_file(local_path, signed["url"])
    return alias


def create_voiceprint(alias: str, api_key: str, poll_seconds: int, timeout_seconds: int) -> str:
    created = request_json(
        f"{API_BASE}/voiceprint",
        api_key,
        {
            "url": alias,
            "model": DEFAULT_MODEL,
        },
    )
    job = wait_for_job(created["jobId"], api_key, poll_seconds, timeout_seconds)
    return str(job.get("output", {}).get("voiceprint", ""))


def identify_clip(
    alias: str,
    voiceprints: list[dict],
    api_key: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> dict:
    created = request_json(
        f"{API_BASE}/identify",
        api_key,
        {
            "voiceprints": voiceprints,
            "url": alias,
            "model": DEFAULT_MODEL,
            "numSpeakers": 1,
            "exclusive": True,
            "confidence": True,
            "matching": {
                "exclusive": False,
                "threshold": 0,
            },
        },
    )
    return wait_for_job(created["jobId"], api_key, poll_seconds, timeout_seconds)


def build_overlap_index(segments: list[dict], speakers: list[str]) -> dict:
    overlap_seconds = {speaker: {other: 0.0 for other in speakers} for speaker in speakers}

    for index, first in enumerate(segments):
        first_speaker = first.get("speaker")
        first_start = float(first.get("start", 0))
        first_end = float(first.get("end", 0))
        for second in segments[index + 1 :]:
            second_speaker = second.get("speaker")
            if first_speaker == second_speaker:
                continue
            second_start = float(second.get("start", 0))
            second_end = float(second.get("end", 0))
            overlap = min(first_end, second_end) - max(first_start, second_start)
            if overlap > 0:
                overlap_seconds[first_speaker][second_speaker] += overlap
                overlap_seconds[second_speaker][first_speaker] += overlap

    return overlap_seconds


def build_similarity_report(
    speakers: list[str],
    confidence_matrix: dict,
    overlap_index: dict,
    cross_match_threshold: int,
) -> dict:
    pair_candidates = []
    for index, first in enumerate(speakers):
        first_scores = confidence_matrix.get(first, {})
        for second in speakers[index + 1 :]:
            second_scores = confidence_matrix.get(second, {})
            first_to_second = first_scores.get(second)
            second_to_first = second_scores.get(first)
            if first_to_second is None or second_to_first is None:
                continue
            mutual_score = round((float(first_to_second) + float(second_to_first)) / 2, 2)
            candidate = {
                "speaker_a": first,
                "speaker_b": second,
                "a_to_b": first_to_second,
                "b_to_a": second_to_first,
                "mutual_score": mutual_score,
                "overlap_seconds": round(overlap_index.get(first, {}).get(second, 0.0), 3),
                "suggest_merge": (
                    mutual_score >= cross_match_threshold
                    and round(overlap_index.get(first, {}).get(second, 0.0), 3) == 0.0
                ),
            }
            pair_candidates.append(candidate)

    pair_candidates.sort(key=lambda item: item["mutual_score"], reverse=True)
    return {
        "cross_match_threshold": cross_match_threshold,
        "pairs": pair_candidates,
        "suggested_merges": [item for item in pair_candidates if item["suggest_merge"]],
    }


def load_wav_samples(clip_path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(clip_path), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise RuntimeError(f"Unsupported sample width for {clip_path}: {sample_width}")

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return sample_rate, samples


def hz_to_mel(value: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + value / 700.0)


def mel_to_hz(value: np.ndarray) -> np.ndarray:
    return 700.0 * (10 ** (value / 2595.0) - 1.0)


def build_mel_filterbank(sample_rate: int, n_fft: int, n_mels: int = 26) -> np.ndarray:
    low_mel = hz_to_mel(np.array([80.0], dtype=np.float32))[0]
    high_mel = hz_to_mel(np.array([min(7600.0, sample_rate / 2 - 50)], dtype=np.float32))[0]
    mel_points = np.linspace(low_mel, high_mel, n_mels + 2, dtype=np.float32)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)

    filterbank = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for index in range(1, n_mels + 1):
        left = bins[index - 1]
        center = bins[index]
        right = bins[index + 1]
        if center <= left:
            center = left + 1
        if right <= center:
            right = center + 1

        for bin_index in range(left, min(center, filterbank.shape[1])):
            filterbank[index - 1, bin_index] = (bin_index - left) / max(center - left, 1)
        for bin_index in range(center, min(right, filterbank.shape[1])):
            filterbank[index - 1, bin_index] = (right - bin_index) / max(right - center, 1)

    return filterbank


def build_dct_basis(n_mels: int, n_mfcc: int = 13) -> np.ndarray:
    basis = np.zeros((n_mfcc, n_mels), dtype=np.float32)
    scale = math.pi / n_mels
    for coeff in range(n_mfcc):
        for mel_index in range(n_mels):
            basis[coeff, mel_index] = math.cos((mel_index + 0.5) * coeff * scale)
    basis[0, :] *= math.sqrt(1.0 / n_mels)
    if n_mfcc > 1:
        basis[1:, :] *= math.sqrt(2.0 / n_mels)
    return basis


def extract_voice_fingerprint(clip_path: Path) -> dict:
    sample_rate, samples = load_wav_samples(clip_path)
    if samples.size < sample_rate * 0.3:
        raise RuntimeError(f"Clip too short for fingerprinting: {clip_path}")

    emphasized = np.empty_like(samples)
    emphasized[0] = samples[0]
    emphasized[1:] = samples[1:] - 0.97 * samples[:-1]

    frame_size = int(sample_rate * 0.025)
    hop_size = int(sample_rate * 0.01)
    n_fft = 512
    usable = emphasized.size - frame_size
    if usable <= 0:
        raise RuntimeError(f"Clip too short after framing: {clip_path}")

    frame_count = 1 + usable // hop_size
    shape = (frame_count, frame_size)
    strides = (emphasized.strides[0] * hop_size, emphasized.strides[0])
    frames = np.lib.stride_tricks.as_strided(emphasized, shape=shape, strides=strides).copy()
    frames *= np.hamming(frame_size).astype(np.float32)

    power = np.abs(np.fft.rfft(frames, n=n_fft, axis=1)) ** 2
    mel_bank = build_mel_filterbank(sample_rate, n_fft)
    mel_energy = np.maximum(power @ mel_bank.T, 1e-10)
    log_mel = np.log(mel_energy)

    dct_basis = build_dct_basis(log_mel.shape[1])
    mfcc = log_mel @ dct_basis.T
    spectral_bins = np.linspace(0, sample_rate / 2, power.shape[1], dtype=np.float32)
    centroid = (power * spectral_bins).sum(axis=1) / np.maximum(power.sum(axis=1), 1e-10)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    zcr = ((frames[:, :-1] * frames[:, 1:]) < 0).mean(axis=1)
    pitch_profile = estimate_pitch_profile(frames, sample_rate)

    core = mfcc[:, 1:13]
    feature_vector = np.concatenate(
        [
            core.mean(axis=0),
            core.std(axis=0),
            np.array(
                [
                    centroid.mean() / 1000.0,
                    centroid.std() / 1000.0,
                    rms.mean() * 10.0,
                    rms.std() * 10.0,
                    zcr.mean(),
                    zcr.std(),
                ],
                dtype=np.float32,
            ),
            pitch_profile,
        ]
    ).astype(np.float32)

    norm = np.linalg.norm(feature_vector)
    if norm > 0:
        feature_vector = feature_vector / norm

    return {
        "sample_rate": sample_rate,
        "duration_seconds": round(samples.size / sample_rate, 3),
        "feature_vector": feature_vector,
    }


def cosine_similarity(first: np.ndarray, second: np.ndarray) -> float:
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator == 0:
        return 0.0
    return float(np.dot(first, second) / denominator)


def build_local_confidence_matrix(speaker_assets: dict) -> dict:
    matrix = {}
    for speaker, first_data in speaker_assets.items():
        first_vector = first_data["fingerprint_vector"]
        row = {}
        for other_speaker, second_data in speaker_assets.items():
            similarity = cosine_similarity(first_vector, second_data["fingerprint_vector"])
            row[other_speaker] = round(max(0.0, similarity) * 100.0, 2)
        matrix[speaker] = row
    return matrix


def estimate_pitch_profile(frames: np.ndarray, sample_rate: int) -> np.ndarray:
    min_hz = 80
    max_hz = 320
    min_lag = max(1, int(sample_rate / max_hz))
    max_lag = max(min_lag + 1, int(sample_rate / min_hz))

    pitches = []
    rms = np.sqrt(np.mean(frames**2, axis=1))
    rms_threshold = max(float(np.median(rms)) * 0.7, 0.01)

    for frame, frame_rms in zip(frames, rms):
        if frame_rms < rms_threshold:
            continue
        autocorr = np.correlate(frame, frame, mode="full")[frame.size - 1 :]
        reference = float(autocorr[0]) if autocorr.size else 0.0
        if reference <= 0:
            continue
        search = autocorr[min_lag:max_lag]
        if search.size == 0:
            continue
        peak_index = int(np.argmax(search))
        peak_value = float(search[peak_index])
        if peak_value < reference * 0.25:
            continue
        lag = peak_index + min_lag
        pitches.append(sample_rate / lag)

    if not pitches:
        return np.zeros(4, dtype=np.float32)

    values = np.array(pitches, dtype=np.float32)
    return np.array(
        [
            float(values.mean() / 100.0),
            float(values.std() / 100.0),
            float(np.percentile(values, 25) / 100.0),
            float(np.percentile(values, 75) / 100.0),
        ],
        dtype=np.float32,
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    api_key = os.getenv("PYANNOTE_API_KEY")
    if args.mode == "api" and not api_key:
        raise SystemExit("Set PYANNOTE_API_KEY before running the script in api mode.")

    media_path = Path(args.media_path).expanduser().resolve()
    job_json_path = Path(args.job_json_path).expanduser().resolve()
    if not media_path.exists():
        raise SystemExit(f"Media file not found: {media_path}")
    if not job_json_path.exists():
        raise SystemExit(f"Job JSON file not found: {job_json_path}")

    job = json.loads(job_json_path.read_text(encoding="utf-8-sig"))
    job_id = str(job.get("jobId") or "unknown-job")
    output = job.get("output", {})
    segments = output.get("exclusiveDiarization") or output.get("diarization") or []
    turns = output.get("turnLevelTranscription") or []

    available_speakers = sorted({str(segment.get("speaker")) for segment in segments if segment.get("speaker")})
    requested_speakers = sorted(set(args.speaker)) if args.speaker else available_speakers
    speakers = [speaker for speaker in requested_speakers if speaker in available_speakers]
    if not speakers:
        raise SystemExit("No speakers selected for analysis.")

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    temp_dir = Path(tempfile.mkdtemp(prefix=f"voicecheck_{job_id}_"))

    try:
        speaker_assets = {}
        for speaker in speakers:
            selected_segments = choose_segments_for_speaker(
                segments,
                speaker,
                min_segment_seconds=args.min_segment_seconds,
                max_segment_seconds=args.max_segment_seconds,
                max_clip_seconds=args.max_clip_seconds,
            )
            if not selected_segments:
                continue

            clip_path = build_speaker_clip(ffmpeg_exe, media_path, speaker, selected_segments, temp_dir)
            speaker_assets[speaker] = {
                "clip_path": str(clip_path),
                "selected_segments": selected_segments,
                "turns_count": sum(1 for turn in turns if turn.get("speaker") == speaker),
            }

            if args.mode == "api":
                alias = f"media://voicecheck/{job_id}/{speaker}.wav"
                upload_media_alias(clip_path, alias, api_key)
                voiceprint = create_voiceprint(alias, api_key, args.poll_seconds, args.timeout_seconds)
                speaker_assets[speaker]["media_alias"] = alias
                speaker_assets[speaker]["voiceprint"] = voiceprint
                print(f"Prepared voiceprint for {speaker}")
            else:
                fingerprint = extract_voice_fingerprint(clip_path)
                speaker_assets[speaker]["fingerprint_vector"] = fingerprint["feature_vector"]
                speaker_assets[speaker]["fingerprint_duration_seconds"] = fingerprint["duration_seconds"]
                speaker_assets[speaker]["fingerprint_sample_rate"] = fingerprint["sample_rate"]
                print(f"Prepared local fingerprint for {speaker}")

        if len(speaker_assets) < 2:
            raise SystemExit("Need at least two valid speaker clips for similarity analysis.")

        if args.mode == "api":
            voiceprints = [
                {"label": speaker, "voiceprint": data["voiceprint"]}
                for speaker, data in speaker_assets.items()
                if data.get("voiceprint")
            ]
            if len(voiceprints) < 2:
                raise SystemExit("Need at least two valid voiceprints for similarity analysis.")

            confidence_matrix = {}
            for speaker, data in speaker_assets.items():
                identified = identify_clip(
                    data["media_alias"],
                    voiceprints,
                    api_key,
                    args.poll_seconds,
                    args.timeout_seconds,
                )
                voiceprint_rows = identified.get("output", {}).get("voiceprints") or []
                if not voiceprint_rows:
                    confidence_matrix[speaker] = {}
                    continue
                confidence = voiceprint_rows[0].get("confidence") or {}
                confidence_matrix[speaker] = confidence
                print(f"Collected similarity row for {speaker}")
        else:
            confidence_matrix = build_local_confidence_matrix(speaker_assets)
            for data in speaker_assets.values():
                data.pop("fingerprint_vector", None)
            print("Built local similarity matrix")

        overlap_index = build_overlap_index(output.get("diarization") or [], list(speaker_assets.keys()))
        similarity_report = build_similarity_report(
            list(speaker_assets.keys()),
            confidence_matrix,
            overlap_index,
            args.cross_match_threshold,
        )

        report = {
            "job_id": job_id,
            "mode": args.mode,
            "source_media": str(media_path),
            "source_job_json": str(job_json_path),
            "speakers_analyzed": list(speaker_assets.keys()),
            "speaker_assets": speaker_assets,
            "confidence_matrix": confidence_matrix,
            "similarity_report": similarity_report,
        }

        report_path = media_path.with_name(f"{media_path.stem}_voice_similarity_{job_id}.json")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8-sig")
        print(f"Saved report: {report_path}")
        return 0
    finally:
        if args.keep_temp:
            print(f"Kept temp dir: {temp_dir}")
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
