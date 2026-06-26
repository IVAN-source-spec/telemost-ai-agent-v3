import argparse
import itertools
import json
from pathlib import Path


ATTACHMENT_GAP_SECONDS = 35.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank likely duplicate speaker ids using similarity and timeline attachment signals."
    )
    parser.add_argument("job_json_path", help="Path to saved pyannote job JSON")
    parser.add_argument("similarity_report_path", help="Path to local voice similarity report JSON")
    parser.add_argument("--target-speakers", type=int, default=None, help="Expected number of real speakers")
    return parser


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_blocks(segments: list[dict], max_gap_seconds: float = 45.0) -> list[dict]:
    if not segments:
        return []

    ordered = sorted(segments, key=lambda item: item["start"])
    blocks = []
    current = {
        "start": ordered[0]["start"],
        "end": ordered[0]["end"],
        "voiced_duration_sec": round(ordered[0]["end"] - ordered[0]["start"], 2),
    }

    for segment in ordered[1:]:
        if segment["start"] - current["end"] <= max_gap_seconds:
            current["end"] = max(current["end"], segment["end"])
            current["voiced_duration_sec"] = round(
                current["voiced_duration_sec"] + (segment["end"] - segment["start"]),
                2,
            )
        else:
            blocks.append(current)
            current = {
                "start": segment["start"],
                "end": segment["end"],
                "voiced_duration_sec": round(segment["end"] - segment["start"], 2),
            }

    blocks.append(current)
    for block in blocks:
        block["duration_sec"] = round(block["end"] - block["start"], 2)
    return blocks


def build_speaker_profiles(job: dict) -> dict:
    segments = job.get("output", {}).get("exclusiveDiarization") or job.get("output", {}).get("diarization") or []
    turns = job.get("output", {}).get("turnLevelTranscription") or []

    by_speaker_segments = {}
    by_speaker_turns = {}

    for segment in segments:
        speaker = str(segment.get("speaker"))
        by_speaker_segments.setdefault(speaker, []).append(
            {
                "start": float(segment.get("start", 0)),
                "end": float(segment.get("end", 0)),
            }
        )

    for turn in turns:
        speaker = str(turn.get("speaker"))
        by_speaker_turns.setdefault(speaker, []).append(
            {
                "start": float(turn.get("start", 0)),
                "end": float(turn.get("end", 0)),
                "text": str(turn.get("text", "")).strip(),
            }
        )

    profiles = {}
    for speaker in sorted(set(by_speaker_segments.keys()) | set(by_speaker_turns.keys())):
        speaker_segments = by_speaker_segments.get(speaker, [])
        speaker_turns = by_speaker_turns.get(speaker, [])
        blocks = build_blocks(speaker_segments)
        duration = sum(item["end"] - item["start"] for item in speaker_segments)
        first_start = min((item["start"] for item in speaker_segments), default=0.0)
        last_end = max((item["end"] for item in speaker_segments), default=0.0)
        active_span = max(0.0, last_end - first_start)
        concentration = round(duration / active_span, 3) if active_span else 0.0
        largest_block_duration = max((block["voiced_duration_sec"] for block in blocks), default=0.0)
        largest_block_ratio = round(largest_block_duration / duration, 3) if duration else 0.0

        profiles[speaker] = {
            "speaker": speaker,
            "segments": speaker_segments,
            "blocks": blocks,
            "turns": speaker_turns,
            "duration_sec": round(duration, 2),
            "turns_count": len(speaker_turns),
            "first_start_sec": round(first_start, 2),
            "last_end_sec": round(last_end, 2),
            "active_span_sec": round(active_span, 2),
            "concentration": concentration,
            "block_count": len(blocks),
            "largest_block_duration_sec": round(largest_block_duration, 2),
            "largest_block_ratio": largest_block_ratio,
            "sample_text": " ".join(item["text"] for item in speaker_turns)[:220],
        }

    annotate_fragment_flags(profiles)
    return profiles


def annotate_fragment_flags(profiles: dict) -> None:
    meeting_end_sec = max((profile["last_end_sec"] for profile in profiles.values()), default=0.0)

    for profile in profiles.values():
        is_scattered_fragment = (
            profile["block_count"] >= 3
            and profile["concentration"] <= 0.12
            and (profile["duration_sec"] <= 80 or profile["turns_count"] <= 15)
        )
        is_tiny_fragment = (
            profile["duration_sec"] <= 25
            and profile["turns_count"] <= 5
            and profile["block_count"] >= 2
        )
        is_tail_fragment = (
            profile["block_count"] == 1
            and 40 <= profile["duration_sec"] <= 160
            and profile["turns_count"] >= 12
            and profile["first_start_sec"] >= meeting_end_sec * 0.65
        )
        is_intro_fragment = (
            profile["block_count"] == 1
            and 40 <= profile["duration_sec"] <= 160
            and profile["turns_count"] >= 12
            and profile["last_end_sec"] <= meeting_end_sec * 0.35
        )
        is_short_oneoff = (
            profile["block_count"] == 1
            and profile["duration_sec"] <= 25
            and profile["turns_count"] <= 6
        )

        profile["fragment_flags"] = {
            "is_scattered_fragment": is_scattered_fragment,
            "is_tiny_fragment": is_tiny_fragment,
            "is_tail_fragment": is_tail_fragment,
            "is_intro_fragment": is_intro_fragment,
            "is_short_oneoff": is_short_oneoff,
            "attachable_fragment": any(
                [
                    is_scattered_fragment,
                    is_tiny_fragment,
                    is_tail_fragment,
                    is_intro_fragment,
                ]
            ),
        }


def build_transition_counts(job: dict) -> dict:
    turns = job.get("output", {}).get("turnLevelTranscription") or []
    counts = {}
    for left, right in zip(turns, turns[1:]):
        speaker_a = str(left.get("speaker"))
        speaker_b = str(right.get("speaker"))
        if speaker_a == speaker_b:
            continue
        key = tuple(sorted((speaker_a, speaker_b)))
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_overlap_seconds(profiles: dict) -> dict:
    overlap = {speaker: {other: 0.0 for other in profiles} for speaker in profiles}
    speakers = list(profiles.keys())
    for first, second in itertools.combinations(speakers, 2):
        total = 0.0
        for first_segment in profiles[first]["segments"]:
            for second_segment in profiles[second]["segments"]:
                shared = min(first_segment["end"], second_segment["end"]) - max(first_segment["start"], second_segment["start"])
                if shared > 0:
                    total += shared
        overlap[first][second] = round(total, 3)
        overlap[second][first] = round(total, 3)
    return overlap


def compute_nearest_gap(segments_a: list[dict], segments_b: list[dict]) -> float:
    nearest_gap = None
    for left in segments_a:
        for right in segments_b:
            if left["end"] < right["start"]:
                gap = right["start"] - left["end"]
            elif right["end"] < left["start"]:
                gap = left["start"] - right["end"]
            else:
                gap = 0.0
            if nearest_gap is None or gap < nearest_gap:
                nearest_gap = gap
    return round(nearest_gap or 0.0, 3)


def compute_window_overlap_ratio(profile_a: dict, profile_b: dict) -> float:
    first = max(profile_a["first_start_sec"], profile_b["first_start_sec"])
    last = min(profile_a["last_end_sec"], profile_b["last_end_sec"])
    shared = max(0.0, last - first)
    base = min(profile_a["active_span_sec"], profile_b["active_span_sec"])
    if base <= 0:
        return 0.0
    return round(shared / base, 3)


def relation_label(profile_a: dict, profile_b: dict) -> str:
    if profile_a["last_end_sec"] <= profile_b["first_start_sec"]:
        return "a_before_b"
    if profile_b["last_end_sec"] <= profile_a["first_start_sec"]:
        return "b_before_a"
    return "overlapping_windows"


def compute_block_attachment(source_profile: dict, anchor_profile: dict, max_gap_seconds: float = ATTACHMENT_GAP_SECONDS) -> dict:
    source_blocks = source_profile["blocks"]
    anchor_blocks = anchor_profile["blocks"]
    if not source_blocks or not anchor_blocks:
        return {
            "connected_blocks": 0,
            "connected_block_ratio": 0.0,
            "sandwich_blocks": 0,
            "before_hits": 0,
            "after_hits": 0,
            "nearest_block_gap_seconds": None,
        }

    connected_blocks = 0
    sandwich_blocks = 0
    before_hits = 0
    after_hits = 0
    nearest_gap = None

    for source_block in source_blocks:
        before_gap = None
        after_gap = None

        for anchor_block in anchor_blocks:
            if anchor_block["end"] <= source_block["start"]:
                gap = source_block["start"] - anchor_block["end"]
                if before_gap is None or gap < before_gap:
                    before_gap = gap
            elif anchor_block["start"] >= source_block["end"]:
                gap = anchor_block["start"] - source_block["end"]
                if after_gap is None or gap < after_gap:
                    after_gap = gap

        candidate_gaps = [gap for gap in [before_gap, after_gap] if gap is not None]
        if candidate_gaps:
            block_gap = min(candidate_gaps)
            if nearest_gap is None or block_gap < nearest_gap:
                nearest_gap = block_gap

        before_close = before_gap is not None and before_gap <= max_gap_seconds
        after_close = after_gap is not None and after_gap <= max_gap_seconds

        if before_close:
            before_hits += 1
        if after_close:
            after_hits += 1
        if before_close or after_close:
            connected_blocks += 1
        if before_close and after_close:
            sandwich_blocks += 1

    return {
        "connected_blocks": connected_blocks,
        "connected_block_ratio": round(connected_blocks / len(source_blocks), 3),
        "sandwich_blocks": sandwich_blocks,
        "before_hits": before_hits,
        "after_hits": after_hits,
        "nearest_block_gap_seconds": round(nearest_gap, 3) if nearest_gap is not None else None,
    }


def build_directional_recommendation(source_profile: dict, anchor_profile: dict, attachment: dict) -> dict | None:
    flags = source_profile["fragment_flags"]
    if not flags["attachable_fragment"]:
        return None

    score = 0
    reasons = []

    if flags["is_scattered_fragment"]:
        score += 3
        reasons.append("source looks scattered across multiple small blocks")
    if flags["is_tiny_fragment"]:
        score += 2
        reasons.append("source is a tiny multi-fragment tail")
    if flags["is_tail_fragment"]:
        score += 2
        reasons.append("source looks like a late single-block tail")
    if flags["is_intro_fragment"]:
        score += 2
        reasons.append("source looks like an early single-block intro")

    ratio = attachment["connected_block_ratio"]
    if ratio >= 1.0:
        score += 3
        reasons.append("all source blocks touch the same anchor on the timeline")
    elif ratio >= 0.75:
        score += 2
        reasons.append("most source blocks touch the same anchor on the timeline")
    elif ratio >= 0.5:
        score += 1
        reasons.append("many source blocks touch the same anchor on the timeline")

    if attachment["sandwich_blocks"] >= 1:
        score += 1
        reasons.append("source is sandwiched by the anchor at least once")

    nearest_block_gap = attachment["nearest_block_gap_seconds"]
    if nearest_block_gap is not None and nearest_block_gap <= 5:
        score += 1
        reasons.append("source and anchor reconnect almost immediately")

    if anchor_profile["duration_sec"] >= source_profile["duration_sec"] * 2:
        score += 1
    if anchor_profile["turns_count"] >= max(1, source_profile["turns_count"] * 2):
        score += 1

    return {
        "source_speaker": source_profile["speaker"],
        "anchor_speaker": anchor_profile["speaker"],
        "directional_score": score,
        "source_flags": flags,
        "attachment": attachment,
        "reasons": reasons,
    }


def choose_preferred_direction(profile_a: dict, profile_b: dict, attachment_a_b: dict, attachment_b_a: dict) -> dict | None:
    options = []
    option_a_b = build_directional_recommendation(profile_a, profile_b, attachment_a_b)
    option_b_a = build_directional_recommendation(profile_b, profile_a, attachment_b_a)
    if option_a_b:
        options.append(option_a_b)
    if option_b_a:
        options.append(option_b_a)
    if not options:
        return None

    options.sort(
        key=lambda item: (
            item["directional_score"],
            item["attachment"]["connected_block_ratio"],
            item["attachment"]["sandwich_blocks"],
        ),
        reverse=True,
    )
    return options[0]


def score_candidate(
    profile_a: dict,
    profile_b: dict,
    similarity: float,
    overlap_seconds: float,
    transition_count: int,
    nearest_gap: float,
    preferred_direction: dict | None,
) -> tuple[int, list[str]]:
    reasons = []
    score = 0

    duration_a = profile_a["duration_sec"]
    duration_b = profile_b["duration_sec"]
    turns_a = profile_a["turns_count"]
    turns_b = profile_b["turns_count"]

    shorter_duration = min(duration_a, duration_b)
    overlap_ratio = round(overlap_seconds / shorter_duration, 3) if shorter_duration > 0 else 0.0
    one_fragment = duration_a <= 60 or duration_b <= 60 or turns_a <= 20 or turns_b <= 20
    one_tiny = duration_a <= 25 or duration_b <= 25 or turns_a <= 8 or turns_b <= 8

    if similarity >= 97:
        score += 5
        reasons.append("very high voice similarity")
    elif similarity >= 95:
        score += 4
        reasons.append("high voice similarity")
    elif similarity >= 93:
        score += 3
        reasons.append("noticeable voice similarity")
    elif similarity >= 90:
        score += 2
    elif similarity >= 85:
        score += 1

    if overlap_ratio == 0:
        score += 2
        reasons.append("no direct overlap in diarization segments")
    elif overlap_ratio <= 0.03:
        score += 1
    elif overlap_ratio <= 0.12:
        score -= 1
    else:
        score -= 3
        reasons.append("too much overlap for a duplicate speaker hypothesis")

    if nearest_gap <= 8:
        score += 2
        reasons.append("timeline reconnects almost immediately")
    elif nearest_gap <= 45:
        score += 1
    elif nearest_gap >= 300 and transition_count == 0:
        score -= 2
        reasons.append("segments are far apart with no local alternation")

    if transition_count <= 2:
        score += 1
    elif transition_count >= 8:
        score -= 2
        reasons.append("speakers alternate too often like different people")

    if one_fragment:
        score += 1
    if one_tiny and similarity >= 92:
        score += 1

    if preferred_direction:
        score += preferred_direction["directional_score"]
        reasons.extend(preferred_direction["reasons"])

    return score, reasons


def build_pair_candidates(job: dict, profiles: dict, similarity_report: dict) -> list[dict]:
    transition_counts = build_transition_counts(job)
    overlap_seconds = build_overlap_seconds(profiles)
    confidence_matrix = similarity_report.get("confidence_matrix") or {}

    candidates = []
    for speaker_a, speaker_b in itertools.combinations(sorted(profiles.keys()), 2):
        similarity = float(confidence_matrix.get(speaker_a, {}).get(speaker_b, 0.0))
        transition_count = transition_counts.get(tuple(sorted((speaker_a, speaker_b))), 0)
        pair_overlap_seconds = overlap_seconds[speaker_a][speaker_b]
        shorter_duration = min(profiles[speaker_a]["duration_sec"], profiles[speaker_b]["duration_sec"])
        overlap_ratio = round(pair_overlap_seconds / shorter_duration, 3) if shorter_duration > 0 else 0.0
        nearest_gap = compute_nearest_gap(profiles[speaker_a]["segments"], profiles[speaker_b]["segments"])
        attachment_a_b = compute_block_attachment(profiles[speaker_a], profiles[speaker_b])
        attachment_b_a = compute_block_attachment(profiles[speaker_b], profiles[speaker_a])
        preferred_direction = choose_preferred_direction(
            profiles[speaker_a],
            profiles[speaker_b],
            attachment_a_b,
            attachment_b_a,
        )

        score, reasons = score_candidate(
            profiles[speaker_a],
            profiles[speaker_b],
            similarity,
            pair_overlap_seconds,
            transition_count,
            nearest_gap,
            preferred_direction,
        )

        candidate = {
            "speaker_a": speaker_a,
            "speaker_b": speaker_b,
            "similarity": round(similarity, 2),
            "score": score,
            "transition_count": transition_count,
            "overlap_seconds": pair_overlap_seconds,
            "overlap_ratio": overlap_ratio,
            "nearest_gap_seconds": nearest_gap,
            "window_relation": relation_label(profiles[speaker_a], profiles[speaker_b]),
            "window_overlap_ratio": compute_window_overlap_ratio(profiles[speaker_a], profiles[speaker_b]),
            "speaker_a_profile": {
                "duration_sec": profiles[speaker_a]["duration_sec"],
                "turns_count": profiles[speaker_a]["turns_count"],
                "first_start_sec": profiles[speaker_a]["first_start_sec"],
                "last_end_sec": profiles[speaker_a]["last_end_sec"],
                "concentration": profiles[speaker_a]["concentration"],
                "block_count": profiles[speaker_a]["block_count"],
                "largest_block_ratio": profiles[speaker_a]["largest_block_ratio"],
                "fragment_flags": profiles[speaker_a]["fragment_flags"],
                "sample_text": profiles[speaker_a]["sample_text"],
            },
            "speaker_b_profile": {
                "duration_sec": profiles[speaker_b]["duration_sec"],
                "turns_count": profiles[speaker_b]["turns_count"],
                "first_start_sec": profiles[speaker_b]["first_start_sec"],
                "last_end_sec": profiles[speaker_b]["last_end_sec"],
                "concentration": profiles[speaker_b]["concentration"],
                "block_count": profiles[speaker_b]["block_count"],
                "largest_block_ratio": profiles[speaker_b]["largest_block_ratio"],
                "fragment_flags": profiles[speaker_b]["fragment_flags"],
                "sample_text": profiles[speaker_b]["sample_text"],
            },
            "a_to_b_attachment": attachment_a_b,
            "b_to_a_attachment": attachment_b_a,
            "directional_recommendation": preferred_direction,
            "reasons": reasons,
        }
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["directional_recommendation"]["directional_score"] if item["directional_recommendation"] else -1,
            item["similarity"],
        ),
        reverse=True,
    )
    return candidates


def pick_merge_plan(candidates: list[dict], target_speakers: int | None, detected_count: int) -> list[dict]:
    if target_speakers is None or detected_count <= target_speakers:
        return []

    merge_budget = detected_count - target_speakers

    selected = []
    used_sources = set()

    for candidate in candidates:
        direction = candidate.get("directional_recommendation")
        if not direction:
            continue
        if candidate["score"] < 8:
            continue
        if candidate["similarity"] < 93:
            continue
        if candidate["overlap_ratio"] > 0.03:
            continue
        if direction["directional_score"] < 4:
            continue

        source_speaker = direction["source_speaker"]
        if source_speaker in used_sources:
            continue

        selected.append(
            {
                "source_speaker": source_speaker,
                "anchor_speaker": direction["anchor_speaker"],
                "pair_score": candidate["score"],
                "directional_score": direction["directional_score"],
                "similarity": candidate["similarity"],
                "transition_count": candidate["transition_count"],
                "nearest_gap_seconds": candidate["nearest_gap_seconds"],
                "reasons": candidate["reasons"],
                "attachment": direction["attachment"],
            }
        )
        used_sources.add(source_speaker)

        if len(selected) >= merge_budget:
            break

    return selected


def resolve_merge_target(merge_map: dict[str, str], speaker: str) -> str:
    current = speaker
    visited = set()
    while current in merge_map and current not in visited:
        visited.add(current)
        current = merge_map[current]
    return current


def build_merge_map(merge_plan: list[dict]) -> dict[str, str]:
    merge_map = {}
    for item in merge_plan:
        source = item["source_speaker"]
        anchor = item["anchor_speaker"]
        merge_map[source] = resolve_merge_target(merge_map, anchor)
    return merge_map


def render_stabilized_transcript(job: dict, merge_map: dict[str, str], output_path: Path) -> None:
    turns = job.get("output", {}).get("turnLevelTranscription") or []
    if not turns:
        output_path.write_text("No turnLevelTranscription found.\n", encoding="utf-8-sig")
        return

    canonical_first_start = {}
    raw_members = {}
    merged_turns = []

    for turn in turns:
        raw_speaker = str(turn.get("speaker"))
        canonical_speaker = resolve_merge_target(merge_map, raw_speaker)
        start = float(turn.get("start", 0))
        end = float(turn.get("end", 0))
        text = str(turn.get("text", "")).strip()

        canonical_first_start.setdefault(canonical_speaker, start)
        raw_members.setdefault(canonical_speaker, set()).add(raw_speaker)
        merged_turns.append(
            {
                "speaker": canonical_speaker,
                "start": start,
                "end": end,
                "text": text,
            }
        )

    canonical_order = sorted(canonical_first_start.items(), key=lambda item: item[1])
    person_labels = {
        speaker: f"PERSON_{index:02d}"
        for index, (speaker, _) in enumerate(canonical_order, start=1)
    }

    lines = [
        "Stabilized transcript for manual review.",
        "",
        "Applied merge map:",
    ]
    if merge_map:
        for source, anchor in sorted(merge_map.items()):
            lines.append(f"- {source} -> {anchor}")
    else:
        lines.append("- no merges applied")

    lines.extend(["", "Canonical speakers:"])
    for canonical_speaker, _ in canonical_order:
        members = " + ".join(sorted(raw_members.get(canonical_speaker, {canonical_speaker})))
        lines.append(f"- {person_labels[canonical_speaker]} = {members}")

    lines.extend(["", "Transcript:", ""])
    for turn in merged_turns:
        label = person_labels[turn["speaker"]]
        lines.append(f"[{turn['start']:.1f}-{turn['end']:.1f}] {label}: {turn['text']}")

    output_path.write_text("\n".join(lines), encoding="utf-8-sig")


def main() -> int:
    args = build_parser().parse_args()
    job_json_path = Path(args.job_json_path).expanduser().resolve()
    similarity_report_path = Path(args.similarity_report_path).expanduser().resolve()

    job = load_json(job_json_path)
    similarity_report = load_json(similarity_report_path)
    profiles = build_speaker_profiles(job)
    candidates = build_pair_candidates(job, profiles, similarity_report)

    merge_plan = pick_merge_plan(candidates, args.target_speakers, len(profiles))
    merge_map = build_merge_map(merge_plan)

    result = {
        "job_json_path": str(job_json_path),
        "similarity_report_path": str(similarity_report_path),
        "detected_speakers_count": len(profiles),
        "detected_speakers": sorted(profiles.keys()),
        "target_speakers": args.target_speakers,
        "top_candidates": candidates[:12],
        "recommended_merge_plan": merge_plan,
        "applied_merge_map": merge_map,
    }

    output_path = job_json_path.with_name(f"{job_json_path.stem}_stabilized_candidates.json")
    transcript_path = job_json_path.with_name(f"{job_json_path.stem}_stabilized_transcript.txt")

    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    render_stabilized_transcript(job, merge_map, transcript_path)

    print(output_path)
    print(transcript_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
