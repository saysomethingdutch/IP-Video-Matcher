"""Post-evaluation descriptive diagnostics; no policy selection or alternative held-out runs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from experiments.phase2b2 import benchmark as b
from experiments.phase2b2.reporting import validate_sanitized
from experiments.phase2b2.verify import primary_matches, alternative_matches
from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner
from ipvm.models import CandidateMatch


def raw_candidates(row):
    return [CandidateMatch(a["accused_time"], a["source_id"], a["source_time"], a["raw_cosine"])
            for a in row["candidate_audit"]]


def coherent_candidate_evidence(row):
    """Pre-filter support diagnostic only. No segment extraction or decision call."""
    candidates = raw_candidates(row)
    aligner = TemporalAligner(MatchingConfig())
    truth = row["truth"][0]
    correct = b.support_detail(candidates, truth, row["frame_count"], MatchingConfig())
    by_episode = defaultdict(list)
    for c in candidates:
        by_episode[c.source_episode].append(c)
    wrong_peak = False
    for episode, items in by_episode.items():
        for peak in aligner._sparse_peaks(episode, items):
            for run in aligner._continuous_runs(aligner._support_for_peak(peak, items)):
                if len(run) < 6 or run[-1].accused_time - run[0].accused_time + .5 < 3:
                    continue
                deviation = max(abs((peak.slope - truth["speed_ratio"]) * c.accused_time
                                    + peak.offset - truth["source_start"]) for c in (run[0], run[-1]))
                if episode != truth["source_episode"] or deviation > 1:
                    wrong_peak = True
    return {"correct_gt_support_for_any_match": correct["retrieval_sufficient_for_any_match"],
            "correct_gt_support_for_full_boundary": correct["retrieval_sufficient_for_full_boundary"],
            "competing_wrong_peak_with_minimum_support": wrong_peak}


def diagnostics(cal, selected, held, old):
    name = selected["selected_policy"]
    stages = {"calibration": cal, "held_out": held}
    result = {"schema_version": "phase2b2-descriptive-diagnostics-v0.1",
              "purpose": "post-evaluation inspection; does not change calibration, held-out outcomes or Gate",
              "selected_policy": name}
    for stage_name, stage in stages.items():
        baselines = {r["case_id"]: r for r in stage["cases"] if r["backend"] == "DCT"}
        rows = [r for r in stage["cases"] if r["backend"] == name and r["positive"]]
        pip = [r for r in rows if r["transformation"] == "PIP_REDUCED_VIEWPORT"]
        pip_evidence = [coherent_candidate_evidence(r) for r in pip]
        inspection = []
        for r in rows:
            if r["outcome"] != "UNSAFE_WRONG_MATCH":
                continue
            gt = r["truth"][0]
            before = b.support_detail(raw_candidates(r), gt, r["frame_count"], MatchingConfig())
            after = r["post_threshold_support"]
            inspection.append({"position_alias": r["position_id"], "transformation": r["transformation"],
                "dct_outcome": baselines[r["case_id"]]["outcome"], "selected_status": r["status"],
                "wrong_episode_emitted": any(p["source_episode"] != gt["source_episode"] for p in r["predictions"]),
                "candidate_tiers": dict(Counter(p["candidate_tier"] for p in r["predictions"])),
                "boundary_assertion": r["boundary_assertion_outcome"],
                "source_boundary_errors_seconds": r["metrics"]["raw_source_boundary_errors"],
                "accused_boundary_errors_seconds": r["metrics"]["raw_accused_boundary_errors"],
                "source_mae_denominator_note": "wrong-episode predictions excluded by frozen evaluator",
                "raw_top8_actual_gt_hits": before["actual_supported_frame_count"],
                "effective_actual_gt_hits": after["actual_supported_frame_count"],
                "query_frame_count": r["frame_count"],
                "effective_full_boundary_support": after["retrieval_sufficient_for_full_boundary"],
                "attribution": r["failure_attribution"]})
        changes = Counter((baselines[r["case_id"]]["outcome"], r["outcome"]) for r in rows)
        result[stage_name] = {
            "paired_outcome_transitions": [{"dct": d, "selected": s, "count": n}
                                           for (d, s), n in sorted(changes.items())],
            "unsafe_inspection": inspection,
            "pip": {"query_count": len(pip), "pre_filter_coherence": {
                key: sum(e[key] for e in pip_evidence) for key in pip_evidence[0]},
                "retained_candidate_count": sum(a["retained"] for r in pip for a in r["candidate_audit"]),
                "effective_candidate_count": sum(a["retained"] and a["adapted_visual_score"] >= .82
                                                 for r in pip for a in r["candidate_audit"]),
                "emitted_query_count": sum(bool(r["predictions"]) for r in pip),
                "coherence_definition": "before floor/filter, frozen sparse peaks and continuous support >=6 actual frames over >=3s; not an emitted match"},
            "safe_but_strict_full_mapping_absent": sum(
                r["outcome"] in {"CORRECT_UNAMBIGUOUS_MATCH", "SAFE_AMBIGUITY_CONTAINS_GT"}
                and not r["full_mapping_explanation_present"] for r in rows),
            "source_boundary_error_denominator": sum(len(r["metrics"]["raw_source_boundary_errors"]) for r in rows),
            "accused_boundary_error_denominator": sum(len(r["metrics"]["raw_accused_boundary_errors"]) for r in rows),
            "speed_error_denominator": sum(len(r["metrics"]["raw_speed_ratio_errors"]) for r in rows)}
    old_by_case = {r["case_id"]: r for r in old["positive_cases"] + old["negative_cases"]
                   if r["backend"] == "DINO_RAW"}
    differences = defaultdict(lambda: Counter())
    for stage in stages.values():
        for row in stage["cases"]:
            if row["backend"] != name:
                continue
            previous = [a for f in old_by_case[row["case_id"]]["frames"] for a in f["top8"]]
            assert len(previous) == len(row["candidate_audit"])
            stats = differences[row["transformation"]]
            stats["candidate_count"] += len(previous)
            for before, after in zip(previous, row["candidate_audit"], strict=True):
                different = any(before[key] != after[key] for key in ("accused_time", "source_time", "source_id", "rank"))
                stats["candidate_identity_differences"] += different
                stats["cosine_difference_over_2e_5"] += abs(before["score"] - after["raw_cosine"]) > 2e-5
    result["batch_comparison_with_previous_query_records"] = {k: dict(v) for k, v in differences.items()}
    validate_sanitized(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=b.ROOT / "workspace/phase2b2")
    parser.add_argument("--previous", type=Path, default=b.ROOT / "workspace/phase2b1/raw/retrieval.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    b.write(args.output, diagnostics(b.read(args.workspace / "calibration.json"),
        b.read(args.workspace / "selected-adapter.json"), b.read(args.workspace / "held_out.json"), b.read(args.previous)))


if __name__ == "__main__":
    main()
