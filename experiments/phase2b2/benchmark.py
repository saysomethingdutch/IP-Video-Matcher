"""Isolated Phase 2B-2 experiment. Run with python -m experiments.phase2b2.benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from experiments.phase2b1 import benchmark as retrieval
from experiments.phase2b2.adapter import (
    DINO_BATCH_SIZE, POLICY_BY_NAME, POLICY_GRID, adapt_candidate_frames,
)
from ipvm.benchmarking import evaluate_segments
from ipvm.config import MatchingConfig, load_config
from ipvm.database import Database
from ipvm.features import DCTVisualEncoder
from ipvm.indexer import validate_index_completeness
from ipvm.matching import TemporalAligner, VideoMatcher
from ipvm.media import probe_media, sha256_file
from ipvm.models import MatchStatus
from ipvm.temporal_benchmark import (
    CASE_OUTCOMES, _metric_summary, _retrieval_sufficiency,
    _supported_run_statistics, classify_boundary_assertion, classify_case_outcome,
    _mapping_matches_truth,
)

PROTOCOL = "20884b1e9ca5d81f35660bc84a7e1294897084a0"
ROOT = Path(__file__).resolve().parents[2]
TRANSFORMS = retrieval.TRANSFORMATIONS


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def now():
    return datetime.now(UTC).isoformat()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def verify_protocol():
    paths = git("diff-tree", "--no-commit-id", "--name-only", "-r", PROTOCOL).splitlines()
    if paths != ["experiments/phase2b2/PROTOCOL.md"]:
        raise ValueError("protocol commit must contain only the protocol")
    if git("diff", "93bf13b", "--", "src/", "config/"):
        raise ValueError("frozen production baseline changed")
    if git("diff", PROTOCOL, "--", "experiments/phase2b2/PROTOCOL.md"):
        raise ValueError("pre-registered protocol changed")


def split(identifier):
    ordinal = int(identifier.rsplit("_", 1)[-1])
    upper = 20 if identifier.startswith("NEGATIVE_") else 10
    if not 1 <= ordinal <= upper:
        raise ValueError("ordinal outside registered corpus")
    return "CALIBRATION" if ordinal % 2 else "HELD_OUT"


def validate_corpus(corpus, negatives):
    positions = corpus["positions"]
    assert len(positions) == 20 and len(corpus["queries"]) == 220
    assert len(negatives["queries"]) == 20
    assert {p["source_alias"] for p in positions} == {"SOURCE_A", "SOURCE_B"}
    for source in ("SOURCE_A", "SOURCE_B"):
        for phase in ("CALIBRATION", "HELD_OUT"):
            assert sum(p["source_alias"] == source and split(p["position_id"]) == phase
                       for p in positions) == 5
    expected = {(p["position_id"], t) for p in positions for t in TRANSFORMS}
    actual = [(q["position_id"], q["transformation"]) for q in corpus["queries"]]
    assert set(actual) == expected and len(actual) == len(set(actual))


def truth_for(case):
    speed = float(case["speed_ratio"])
    start = float(case["source_start_seconds"])
    return [{"accused_start": 0.0, "accused_end": 8.0 / speed,
             "source_episode": case["source_alias"], "source_start": start,
             "source_end": start + 8.0, "speed_ratio": speed}]


def support_detail(candidates, truth, count, config):
    hits = {round(c.accused_time * 2) for c in candidates
            if c.source_episode == truth["source_episode"]
            and abs(c.source_time - truth["source_start"]
                    - truth["speed_ratio"] * c.accused_time) <= .5 + 1e-6}
    stats = _supported_run_statistics(hits, count, config.maximum_frame_gap_seconds, 2.0)
    any_match, full = _retrieval_sufficiency(stats, count, 6, 2.0)
    return {**stats, "actual_supported_frame_count": len(hits), "query_frame_count": count,
            "retrieval_sufficient_for_any_match": any_match,
            "retrieval_sufficient_for_full_boundary": full}


def encode_query(path, encoder):
    """Fixed 16-frame chunks, natural final short chunk; same policy as source index."""
    times, embeddings, batches = [], [], []
    for ts, images in retrieval.iter_rgb_batches(path, batch_size=DINO_BATCH_SIZE):
        times.extend(ts.tolist())
        embeddings.append(encoder.encode_images(images))
        batches.append(len(images))
    return np.asarray(times), np.concatenate(embeddings), batches


def retrieve_frames(query_times, embeddings, index):
    """No case/GT input: exact, identity-preserving top-8 candidate construction."""
    frames = []
    for ts, embedding in zip(query_times, embeddings, strict=True):
        ids, scores = retrieval.exact_cosine_topk(index["embedding"] @ embedding, 8)
        frames.append({"accused_time": float(ts), "top8": [
            {"accused_time": float(ts), "source_time": float(index["source_time"][i]),
             "source_id": str(index["source_alias"][i]), "rank": rank, "score": float(score)}
            for rank, (i, score) in enumerate(zip(ids, scores, strict=True), 1)]})
    return frames


def prepare_dct(corpus, index_dir, output):
    """Register identical cached DCT arrays in an isolated real DB; no user DB access."""
    config = load_config(ROOT)
    config = replace(config, paths=replace(config.paths, workspace=output,
                                          database=output / "dct-control.db"))
    db = Database(config.paths.database)
    project = db.initialize(config.paths.root)
    arrays = retrieval._load_index(index_dir / "dct-source-index.npz")
    for source in corpus["sources"]:
        alias, path = source["alias"], Path(source["path"])
        mask = arrays["source_alias"] == alias
        index = output / f"{alias}-dct.npz"
        np.savez(index, timestamps=arrays["source_time"][mask], features=arrays["embedding"][mask])
        metadata = probe_media(path)
        completeness = validate_index_completeness(metadata.duration, 2.0,
                                                   arrays["source_time"][mask].tolist())
        if completeness["status"] != "COMPLETE":
            raise ValueError("cached source index incomplete")
        db.upsert_original(project, alias, path, sha256_file(path), metadata, index,
                           config.matching.algorithm_version, DCTVisualEncoder.name, completeness)
    db.update_project_index_status(project, "COMPLETE", 2, 2)
    return VideoMatcher(config, db)


def record(case, result, candidates, audit, *, backend, phase, runtime, frame_count):
    positive = case["transformation"] != "NEGATIVE"
    truth = truth_for(case) if positive else []
    predictions, alternatives = result.segments, result.alternatives
    if hasattr(result, "status"):
        status = result.status
    else:
        status = (MatchStatus.AMBIGUOUS.value if result.ambiguous or result.contradiction
                  else MatchStatus.MATCH_FOUND.value if predictions else MatchStatus.MATCH_NOT_FOUND.value)
    metrics = evaluate_segments(truth, predictions, status, alternatives)
    row = {"case_id": case["case_id"], "position_id": case["position_id"],
           "split": phase, "backend": backend, "transformation": case["transformation"],
           "positive": positive, "status": status, "frame_count": frame_count,
           "metrics": metrics, "predictions": [p.to_dict() for p in predictions],
           "alternatives": [p.to_dict() for p in alternatives],
           "warnings": list(result.warnings), "truncated_episodes": list(result.truncated_episodes),
           "conflict_graph": result.conflict_graph, "candidate_audit": audit,
           "runtime_seconds": runtime,
           "review_tier": "REVIEW_REQUIRED" if predictions or status == "AMBIGUOUS" else "RED"}
    if positive:
        row.update(source_alias=case["source_alias"],
                   normalized_source_start=case["normalized_source_start"], truth=truth)
        row["outcome"] = classify_case_outcome(status, metrics, truth, alternatives)
        full = metrics["source_aware_recalled_segments"] == 1 and metrics["false_positive_segments"] == 0
        row["boundary_assertion_outcome"] = classify_boundary_assertion(predictions, truth, full)
        row["retrieval_attribution_detail"] = support_detail(candidates, truth[0], frame_count, MatchingConfig())
        effective = [c for c in candidates if c.visual_score >= .82]
        row["post_threshold_support"] = support_detail(effective, truth[0], frame_count, MatchingConfig())
        detail = row["post_threshold_support"]
        row["failure_attribution"] = (None if full else
            "RETRIEVAL_INSUFFICIENT_FOR_ANY_MATCH" if not detail["retrieval_sufficient_for_any_match"] else
            "RETRIEVAL_INSUFFICIENT_FOR_FULL_BOUNDARY" if not detail["retrieval_sufficient_for_full_boundary"]
            else "ALIGNMENT_LIMIT")
        row["shared_same_time_wrong_source_frames"] = len({round(c.accused_time * 2) for c in candidates
            if c.source_episode != truth[0]["source_episode"] and
            abs(c.source_time - truth[0]["source_start"] - truth[0]["speed_ratio"] * c.accused_time) <= 1.0})
        row["full_mapping_explanation_present"] = any(
            _mapping_matches_truth(p, truth[0]) for p in predictions + alternatives)
    else:
        counts = Counter(c.source_episode for c in candidates)
        row["source_concentration"] = max(counts.values(), default=0) / max(1, len(candidates))
        row["coherent_false_alignment"] = bool(predictions)
        row["confident_false_alignment"] = bool(predictions) and (
            status == "MATCH_FOUND" or any(p.candidate_tier == "GREEN" for p in predictions))
        row["best_visual_score"] = max((c.visual_score for c in candidates), default=None)
        row["maximum_support"] = max((p.inlier_count for p in predictions), default=0)
        row["maximum_temporal_score"] = max((p.temporal_score for p in predictions), default=0)
    return row


def ratio(n, d):
    return {"numerator": int(n), "denominator": int(d), "rate": n / d if d else None}


def summary(rows):
    positive = [r for r in rows if r["positive"]]
    negative = [r for r in rows if not r["positive"]]
    metrics = _metric_summary(positive)
    taxonomy = {name: sum(r["outcome"] == name for r in positive) for name in CASE_OUTCOMES}
    assert sum(taxonomy.values()) == len(positive)
    predictions = [p for r in positive for p in r["predictions"]]
    audits = [a for r in rows for a in r["candidate_audit"]]
    metrics.update(taxonomy=taxonomy,
        safe_success=ratio(taxonomy[CASE_OUTCOMES[0]] + taxonomy[CASE_OUTCOMES[1]], len(positive)),
        unsafe_rate=ratio(taxonomy[CASE_OUTCOMES[2]], len(positive)),
        episode_correctness_fraction=ratio(sum(r["metrics"].get("correct_episode_predictions", 0) for r in positive),
                                           sum(r["metrics"].get("overlapping_predictions", 0) for r in positive)),
        candidate_tiers=dict(Counter(p["candidate_tier"] for p in predictions)),
        review_tiers=dict(Counter(p["review_tier"] for p in predictions)),
        case_review_tiers=dict(Counter(r["review_tier"] for r in positive)),
        segment_counts=dict(Counter(str(len(r["predictions"])) for r in positive)),
        warning_codes=dict(Counter(w["code"] for r in rows for w in r["warnings"])),
        retained_fraction=ratio(sum(a["retained"] for a in audits), len(audits)),
        effective_fraction=ratio(sum(a["retained"] and a["adapted_visual_score"] >= .82 for a in audits), len(audits)),
        retained_cosine=retrieval._distribution(a["raw_cosine"] for a in audits if a["retained"]),
        dropped_cosine=retrieval._distribution(a["raw_cosine"] for a in audits if not a["retained"]),
        negatives={"query_count": len(negative),
            "emitted": sum(r["coherent_false_alignment"] for r in negative),
            "confident": sum(r["confident_false_alignment"] for r in negative),
            "status": dict(Counter(r["status"] for r in negative)),
            "candidate_tiers": dict(Counter(p["candidate_tier"] for r in negative for p in r["predictions"])),
            "best_visual_score": retrieval._distribution(r["best_visual_score"] for r in negative)})
    return metrics


def select_policy(calibration):
    """Reject any held-out input; strict conservative operationalization of material safety."""
    if calibration["stage"] != "CALIBRATION":
        raise ValueError("selection accepts CALIBRATION only")
    if any(r["split"] != "CALIBRATION" for r in calibration.get("cases", [])):
        raise ValueError("held-out case in selection input")
    baseline = calibration["summaries"]["DCT"]["taxonomy"]["UNSAFE_WRONG_MATCH"]
    choices, rejected = [], {}
    for ordinal, policy in enumerate(POLICY_GRID):
        s = calibration["summaries"][policy.name]
        unsafe = s["taxonomy"]["UNSAFE_WRONG_MATCH"]
        if s["negatives"]["emitted"] or unsafe > baseline:
            rejected[policy.name] = "negative emitted or unsafe positive count exceeds DCT"
            continue
        key = (unsafe, s["negatives"]["emitted"], -s["safe_success"]["numerator"],
               s["source_boundary_mae_seconds"] if s["source_boundary_mae_seconds"] is not None else math.inf,
               s["accused_boundary_mae_seconds"] if s["accused_boundary_mae_seconds"] is not None else math.inf,
               ordinal)
        choices.append((key, policy))
    policy = min(choices, key=lambda x: x[0])[1] if choices else None
    return {"selected_policy": policy.name if policy else None,
            "selected_floor": policy.floor if policy else None,
            "rejected": rejected, "selection_source": "CALIBRATION_ONLY",
            "selection_reason": "lexicographic unsafe count, negative alignment, safe success, source MAE, accused MAE, simplicity"}


def implementation_identity():
    files = [Path(__file__), Path(__file__).with_name("adapter.py"), Path(__file__).with_name("reporting.py"),
             ROOT / "src/ipvm/matching.py", ROOT / "src/ipvm/benchmarking.py",
             ROOT / "src/ipvm/temporal_benchmark.py", ROOT / "config/default.yaml"]
    return {str(p.relative_to(ROOT)): sha256_file(p) for p in files}


def run_stage(args, phase):
    verify_protocol()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "workspace"):
        raise ValueError("raw output must stay in ignored workspace")
    output.mkdir(parents=True, exist_ok=True)
    selected_path = output / "selected-adapter.json"
    if phase == "CALIBRATION" and selected_path.exists():
        raise ValueError("selected policy already frozen; calibration rerun forbidden")
    selected = read(selected_path) if phase == "HELD_OUT" else None
    if selected is not None:
        if selected["implementation_identity"] != implementation_identity():
            raise ValueError("implementation changed after selection freeze")
        if selected["protocol_commit"] != PROTOCOL or selected["selection_source"] != "CALIBRATION_ONLY":
            raise ValueError("invalid frozen selection provenance")
        if not selected["selected_policy"]:
            raise ValueError("no eligible policy; held-out evaluation not authorized")
    target = output / f"{phase.lower()}.json"
    if target.exists():
        raise ValueError("run result exists; refusing to overwrite audit trail")
    corpus, negatives = read(args.corpus), read(args.negatives)
    validate_corpus(corpus, negatives)
    cases = [q for q in corpus["queries"] + negatives["queries"] if split(q["position_id"]) == phase]
    indexes = {b: retrieval._load_index(args.index_dir / f"{b}-source-index.npz") for b in ("dct", "dino")}
    for key in ("source_time", "source_alias"):
        np.testing.assert_array_equal(indexes["dct"][key], indexes["dino"][key])
    assert np.all(indexes["dino"]["source_time"] * 2 == np.round(indexes["dino"]["source_time"] * 2))
    encoder = retrieval.DinoOnnxEncoder(args.model)
    matcher = prepare_dct(corpus, args.index_dir, output)
    config = matcher.config.matching
    assert config == MatchingConfig()  # no hidden config override
    policies = list(POLICY_GRID) if phase == "CALIBRATION" else [POLICY_BY_NAME[selected["selected_policy"]]]
    rows, provenance = [], []
    started = time.perf_counter()
    for n, case in enumerate(cases, 1):
        print(f"{phase} {n}/{len(cases)} {case['case_id']}", flush=True)
        captured = []
        original_retrieve = matcher.retriever.retrieve

        def capture(query_times, query_features):
            result = original_retrieve(query_times, query_features)
            captured.extend(result)
            return result

        matcher.retriever.retrieve = capture
        dct_result = matcher.analyze_file(case["case_id"], Path(case["path"]), persist=False)
        matcher.retriever.retrieve = original_retrieve
        if dct_result.status in {"ERROR", "INCOMPLETE_INDEX"}:
            raise ValueError(f"DCT control blocked: {dct_result.reason}")
        dct_audit = []
        ranks = Counter()
        for c in captured:
            ranks[c.accused_time] += 1
            dct_audit.append({"accused_time": c.accused_time, "source_time": c.source_time,
                "source_id": c.source_episode, "rank": ranks[c.accused_time],
                "raw_cosine": c.visual_score * 2 - 1, "retained": True,
                "adapted_visual_score": c.visual_score})
        rows.append(record(case, dct_result, captured, dct_audit, backend="DCT", phase=phase,
                           runtime=dct_result.elapsed_seconds, frame_count=dct_result.frame_count))
        encode_start = time.perf_counter()
        times, embeddings, batches = encode_query(Path(case["path"]), encoder)
        encode_seconds = time.perf_counter() - encode_start
        assert times.tolist() == sorted({c.accused_time for c in captured})
        retrieve_start = time.perf_counter()
        frames = retrieve_frames(times, embeddings, indexes["dino"])
        retrieve_seconds = time.perf_counter() - retrieve_start
        provenance.append({"case_id": case["case_id"], "dino_batch_size": DINO_BATCH_SIZE,
            "actual_batch_lengths": batches, "encode_seconds": encode_seconds,
            "retrieval_seconds": retrieve_seconds})
        for policy in policies:
            stream = adapt_candidate_frames(frames, backend="DINO_RAW", policy=policy)
            t = time.perf_counter()
            alignment = TemporalAligner(config).align_with_diagnostics(list(stream.candidates), index_complete=True)
            rows.append(record(case, alignment, list(stream.candidates), list(stream.audit),
                backend=policy.name, phase=phase, runtime=time.perf_counter() - t, frame_count=len(times)))
    result = {"stage": phase, "protocol_commit": PROTOCOL, "dino_batch_size": DINO_BATCH_SIZE,
        "implementation_identity": implementation_identity(), "completed_at": now(),
        "cases": rows, "inference": provenance, "runtime_seconds": time.perf_counter() - started,
        "summaries": {name: summary([r for r in rows if r["backend"] == name])
                      for name in ["DCT"] + [p.name for p in policies]},
        "source_frame_count": len(indexes["dino"]["source_time"])}
    if selected is not None:
        result["selected_policy_digest"] = digest(selected)
    write(target, result, exclusive=True)


def freeze(output):
    calibration = read(output / "calibration.json")
    if calibration["implementation_identity"] != implementation_identity():
        raise ValueError("implementation changed since calibration")
    selected = {**select_policy(calibration), "protocol_commit": PROTOCOL,
        "dino_batch_size": DINO_BATCH_SIZE, "frozen_at": now(),
        "calibration_metrics_digest": digest(calibration["summaries"]),
        "implementation_identity": implementation_identity(), "held_out_metrics_used": False}
    write(output / "selected-adapter.json", selected, exclusive=True)
    print(json.dumps({k: v for k, v in selected.items() if k != "implementation_identity"}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["calibrate", "freeze", "held-out"])
    parser.add_argument("--output", type=Path, default=ROOT / "workspace/phase2b2")
    parser.add_argument("--corpus", type=Path, default=ROOT / "workspace/phase2b1/raw/corpus.json")
    parser.add_argument("--negatives", type=Path, default=ROOT / "workspace/phase2b1/raw/negatives.json")
    parser.add_argument("--index-dir", type=Path, default=ROOT / "workspace/phase2b1/index")
    parser.add_argument("--model", type=Path, required=False)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze(args.output)
    else:
        if not args.model:
            parser.error("--model required for inference")
        run_stage(args, "CALIBRATION" if args.command == "calibrate" else "HELD_OUT")


if __name__ == "__main__":
    main()
