"""Supplemental checks only; never selects a policy or changes evaluator outputs."""
from __future__ import annotations

import argparse
import copy
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

from experiments.phase2b2 import benchmark as b
from experiments.phase2b2.adapter import POLICY_BY_NAME, adapt_candidate_frames
from experiments.phase2b2.reporting import validate_sanitized
from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner
from ipvm.models import CandidateMatch


def primary_matches(p, gt):
    """Independent implementation of frozen single-GT primary eligibility."""
    span = p["accused_end"] - p["accused_start"]
    source_span = p["source_end"] - p["source_start"]
    overlap = max(0, min(p["accused_end"], gt["accused_end"]) - max(p["accused_start"], gt["accused_start"]))
    if overlap < .5 * (gt["accused_end"] - gt["accused_start"]):
        return False
    if p["source_episode"] != gt["source_episode"]:
        return False
    if max(abs(p[k] - gt[k]) for k in ("source_start", "source_end")) > 1:
        return False
    if span <= 0 or source_span <= 0 or abs(source_span - span * gt["speed_ratio"]) > 1:
        return False
    if min(span, source_span) <= 3:
        return p["speed_ratio"] is None
    return p["speed_ratio"] is not None and abs(source_span - span * p["speed_ratio"]) <= 1


def alternative_matches(p, gt):
    if p["source_episode"] != gt["source_episode"]:
        return False
    if max(abs(p[k] - gt[k]) for k in ("source_start", "source_end", "accused_start", "accused_end")) > 1:
        return False
    span = p["accused_end"] - p["accused_start"]
    if abs(span - gt["accused_end"] + gt["accused_start"]) > 1 or span <= 0:
        return False
    slope = (p["source_end"] - p["source_start"]) / span
    return (abs(slope - gt["speed_ratio"]) <= .15 and p["speed_ratio"] is not None
            and abs(p["speed_ratio"] - gt["speed_ratio"]) <= .15)


def classify(row):
    predictions, gt = row["predictions"], row["truth"][0]
    full = len(predictions) == 1 and primary_matches(predictions[0], gt)
    if row["status"] == "AMBIGUOUS":
        if full or any(alternative_matches(p, gt) for p in row["alternatives"]):
            return "SAFE_AMBIGUITY_CONTAINS_GT"
        return "UNSAFE_WRONG_MATCH" if predictions else "NO_MATCH"
    if row["status"] == "MATCH_FOUND":
        return "CORRECT_UNAMBIGUOUS_MATCH" if full else "UNSAFE_WRONG_MATCH"
    return "UNSAFE_WRONG_MATCH" if predictions else "NO_MATCH"


def frame_input(row):
    result = {}
    for item in row["candidate_audit"]:
        time = item["accused_time"]
        result.setdefault(time, {"accused_time": time, "top8": []})["top8"].append({
            "accused_time": time, "source_time": item["source_time"], "source_id": item["source_id"],
            "rank": item["rank"], "score": item["raw_cosine"]})
    return [result[t] for t in sorted(result)]


def verify(cal, selected, held):
    b.verify_protocol()
    assert selected["implementation_identity"] == b.implementation_identity()
    assert b.digest(selected) == held["selected_policy_digest"]
    positives = [r for stage in (cal, held) for r in stage["cases"] if r["positive"]]
    mismatch = [r["case_id"] for r in positives if classify(r) != r["outcome"]]
    name = selected["selected_policy"]
    policy = POLICY_BY_NAME[name]
    row = next(r for r in held["cases"] if r["backend"] == name and r["transformation"] == "DIRECT")
    frames = frame_input(row)
    aligner = TemporalAligner(MatchingConfig())
    stream = adapt_candidate_frames(frames, backend="DINO_RAW", policy=policy)
    a = aligner.align_with_diagnostics(list(stream.candidates), index_complete=True)
    repeated = aligner.align_with_diagnostics(list(stream.candidates), index_complete=True)
    shuffled = copy.deepcopy(list(reversed(frames)))
    for frame in shuffled:
        frame["top8"].reverse()
        frame["expected_source_time"] = -999999
        frame["ground_truth_episode"] = "POISONED_GT"
    alternate = adapt_candidate_frames(shuffled, backend="DINO_RAW", policy=policy)
    permuted = aligner.align_with_diagnostics(list(alternate.candidates), index_complete=True)
    # Directly permute CandidateMatch objects as well, bypassing adapter canonicalization.
    direct_permuted = aligner.align_with_diagnostics(list(reversed(stream.candidates)), index_complete=True)
    removed = aligner.align_with_diagnostics([c for c in stream.candidates if c.accused_time >= 3], index_complete=True)
    coherent = [CandidateMatch(i / 2, source, offset + i / 2, .99)
                for i in range(20) for source, offset in (("SCRATCH_A", 100), ("SCRATCH_B", 200))]
    contest = aligner.align_with_diagnostics(coherent, index_complete=True)
    lone_wrong = aligner.align_with_diagnostics([c for c in coherent if c.source_episode == "SCRATCH_B"], index_complete=True)
    actual_lengths = Counter(n for stage in (cal, held) for r in stage["inference"] for n in r["actual_batch_lengths"])
    all_audits = [a for stage in (cal, held) for r in stage["cases"] for a in r["candidate_audit"]]
    result = {"schema_version": "phase2b2-verification-v0.1", "protocol_commit": b.PROTOCOL,
        "independent_taxonomy": {"positive_backend_cases": len(positives), "mismatch_count": len(mismatch)},
        "source_grid": {"audited_candidates_including_policy_replays": len(all_audits),
                        "off_grid_count": sum(abs(a["source_time"] * 2 - round(a["source_time"] * 2)) > 1e-6 for a in all_audits)},
        "actual_inference_batch_lengths": dict(actual_lengths), "configured_batch_size": 16,
        "repeat_identical": asdict(a) == asdict(repeated),
        "gt_mutation_and_adapter_permutation_stream_identical": stream == alternate,
        "adapter_permutation_alignment_identical": asdict(a) == asdict(permuted),
        "direct_candidate_permutation_alignment_identical": asdict(a) == asdict(direct_permuted),
        "band_removal": {"before_segment_count": len(a.segments), "after_segment_count": len(removed.segments),
                         "before_total_accused_span": sum(p.accused_end-p.accused_start for p in a.segments),
                         "after_total_accused_span": sum(p.accused_end-p.accused_start for p in removed.segments)},
        "coherent_competing_wrong_line": {"ambiguous": contest.ambiguous,
            "alternatives_count": len(contest.alternatives),
            "green_count": sum(p.candidate_tier == "GREEN" for p in contest.segments)},
        "lone_coherent_wrong_line": {"segments": len(lone_wrong.segments),
            "green_count": sum(p.candidate_tier == "GREEN" for p in lone_wrong.segments),
            "review_tiers": sorted({p.review_tier for p in lone_wrong.segments})},
        "release_tier_violation_count": sum(p["review_tier"] != "REVIEW_REQUIRED"
            for stage in (cal, held) for r in stage["cases"] for p in r["predictions"])}
    assert not mismatch and result["source_grid"]["off_grid_count"] == 0
    assert result["repeat_identical"] and result["gt_mutation_and_adapter_permutation_stream_identical"]
    assert result["release_tier_violation_count"] == 0
    # 'segments' is a count here; validator deliberately rejects that raw field name.
    result["lone_coherent_wrong_line"]["segment_count"] = result["lone_coherent_wrong_line"].pop("segments")
    validate_sanitized(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=b.ROOT / "workspace/phase2b2")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(b.read(args.workspace / "calibration.json"), b.read(args.workspace / "selected-adapter.json"),
                    b.read(args.workspace / "held_out.json"))
    b.write(args.output, result)
    print(__import__("json").dumps(result, indent=2))


if __name__ == "__main__":
    main()
