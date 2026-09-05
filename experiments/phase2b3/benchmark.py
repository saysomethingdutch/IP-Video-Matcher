#!/usr/bin/env python3
"""Phase 2B-3 isolated DINO score-contract remediation experiment."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import time
from typing import Any, Iterable

import numpy as np

from experiments.phase2b1 import benchmark as retrieval
from experiments.phase2b2 import benchmark as phase2b2
from experiments.phase2b3.adapter import (
    ALIGNER_ADMISSION_SCORE,
    DINO_BATCH_SIZE,
    POLICY_BY_NAME,
    POLICY_GRID,
    SCORE_MAPPING_VERSION,
    ScorePolicy,
    adapt_candidate_frames,
)
from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner
from ipvm.media import probe_media, sha256_file
from ipvm.models import CandidateMatch
from ipvm.temporal_benchmark import CASE_OUTCOMES, _mapping_matches_truth


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_COMMIT = "277dea444545b5555f6bc4813334c6528df8a4cb"
PRODUCTION_COMMIT = "93bf13b"
PHASE2B1_COMMIT = "dccec2820750126b46baabcacb14ce0caf828c7b"
PHASE2B2_COMMIT = "4a5b7dedc2fa3189d62e6a0478cad0cddc8e90cc"
FRESH_SEED = 20260905
FRESH_POSITIONS_PER_SOURCE = 5
FRESH_SPAN_SECONDS = 8.0
EXCLUSION_SECONDS = 30.0
START_SEPARATION_SECONDS = FRESH_SPAN_SECONDS + EXCLUSION_SECONDS
A010_TRANSFORMS = ("DIRECT", "H264_REENCODE", "SPEED_0_8X")


def now() -> str:
    return datetime.now(UTC).isoformat()


def read(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path: Path | str, value: Any, *, exclusive: bool = False) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x" if exclusive else "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def verify_protocol() -> None:
    paths = git("diff-tree", "--no-commit-id", "--name-only", "-r", PROTOCOL_COMMIT).splitlines()
    if paths != ["experiments/phase2b3/PROTOCOL.md"]:
        raise ValueError("Phase 2B-3 protocol commit must contain only PROTOCOL.md")
    if git("diff", PROTOCOL_COMMIT, "--", "experiments/phase2b3/PROTOCOL.md"):
        raise ValueError("pre-registered Phase 2B-3 protocol changed")
    if git("diff", PRODUCTION_COMMIT, "--", "src/", "config/"):
        raise ValueError("frozen Phase 2A production/config changed")


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "mtime_ns": resolved.stat().st_mtime_ns,
        "sha256": sha256_file(resolved),
    }


def implementation_identity() -> dict[str, str]:
    files = (
        Path(__file__),
        Path(__file__).with_name("adapter.py"),
        Path(__file__).with_name("reporting.py"),
        ROOT / "experiments/phase2b1/benchmark.py",
        ROOT / "experiments/phase2b2/benchmark.py",
        ROOT / "src/ipvm/matching.py",
        ROOT / "src/ipvm/benchmarking.py",
        ROOT / "src/ipvm/temporal_benchmark.py",
        ROOT / "config/default.yaml",
    )
    return {str(path.relative_to(ROOT)): sha256_file(path) for path in files}


def execution_identity(args: argparse.Namespace) -> dict[str, Any]:
    metadata = read(args.index_dir / "index-metadata.json")
    return {
        "protocol_commit": PROTOCOL_COMMIT,
        "implementation": implementation_identity(),
        "model_public_identity": {
            "name": "dinov2-small-int8",
            "implementation_version": metadata["dino"]["implementation_version"],
            "provenance": metadata["dino"]["model_provenance"],
        },
        "model_local_identity": _file_identity(args.model),
        "source_indexes": {
            name: _file_identity(args.index_dir / name)
            for name in ("dino-source-index.npz", "dct-source-index.npz", "index-metadata.json")
        },
        "input_manifests": {
            "dev_corpus": _file_identity(args.corpus),
            "dev_negatives": _file_identity(args.negatives),
        },
        "configuration": {
            "source_fps": 2.0,
            "query_fps": 2.0,
            "top_k": 8,
            "dino_batch_size": DINO_BATCH_SIZE,
            "score_mapping_version": SCORE_MAPPING_VERSION,
            "tau_grid": [asdict(policy) for policy in POLICY_GRID],
        },
    }


def _assert_workspace(output: Path) -> Path:
    resolved = output.resolve()
    if not resolved.is_relative_to((ROOT / "workspace").resolve()):
        raise ValueError("raw Phase 2B-3 output must remain under ignored workspace")
    return resolved


def ensure_dev_lock(args: argparse.Namespace) -> dict[str, Any]:
    verify_protocol()
    output = _assert_workspace(args.output)
    if (output / "selected-score-contract.json").exists():
        raise ValueError("selected score contract already frozen; DEV rerun forbidden")
    identity = execution_identity(args)
    lock_path = output / "dev-stage-identity.json"
    if lock_path.exists():
        lock = read(lock_path)
        if lock["execution_identity"] != identity:
            raise ValueError("DEV execution identity differs from immutable stage lock")
        return lock
    lock = {"stage": "DEV_LOCKED", "created_at": now(), "execution_identity": identity}
    write(lock_path, lock, exclusive=True)
    return lock


def verify_selected(args: argparse.Namespace) -> dict[str, Any]:
    verify_protocol()
    output = _assert_workspace(args.output)
    selected_path = output / "selected-score-contract.json"
    if not selected_path.exists():
        raise ValueError("fresh data is forbidden before score policy freeze")
    selected = read(selected_path)
    current = execution_identity(args)
    if selected["execution_identity"] != current:
        raise ValueError("execution/evaluator/model/index identity changed after policy freeze")
    if selected["protocol_commit"] != PROTOCOL_COMMIT:
        raise ValueError("selected score contract has wrong protocol provenance")
    return selected


def validate_dev_inputs(corpus: dict[str, Any], negatives: dict[str, Any]) -> None:
    if len(corpus["sources"]) != 2 or len(corpus["positions"]) != 20:
        raise ValueError("DEV corpus must contain the 20 observed positions")
    if len(corpus["queries"]) != 220 or len(negatives["queries"]) != 20:
        raise ValueError("DEV must contain 220 positives and 20 negatives")
    expected = {(p["position_id"], t) for p in corpus["positions"] for t in retrieval.TRANSFORMATIONS}
    actual = [(q["position_id"], q["transformation"]) for q in corpus["queries"]]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("DEV transformation cells are incomplete or duplicated")
    if {p["source_alias"] for p in corpus["positions"]} != {"SOURCE_A", "SOURCE_B"}:
        raise ValueError("unexpected DEV source aliases")


def _frames_from_audit(audit: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time: dict[float, dict[str, Any]] = {}
    for item in audit:
        accused_time = float(item["accused_time"])
        frame = by_time.setdefault(accused_time, {"accused_time": accused_time, "top8": []})
        frame["top8"].append(
            {
                "accused_time": accused_time,
                "source_time": item["source_time"],
                "source_id": item["source_id"],
                "rank": item["rank"],
                "score": item["raw_cosine"],
            }
        )
    return [by_time[key] for key in sorted(by_time)]


def _maximum_wrong_source_line(candidates: list[CandidateMatch], correct_source: str) -> dict[str, Any]:
    aligner = TemporalAligner(MatchingConfig())
    by_episode: dict[str, list[CandidateMatch]] = defaultdict(list)
    for candidate in candidates:
        if candidate.source_episode != correct_source:
            # This diagnostic measures coherent candidate membership, not a second
            # score-policy decision. Equal unit weights keep raw-vs-effective changes
            # attributable solely to the frozen 0.82 admission gate.
            by_episode[candidate.source_episode].append(
                CandidateMatch(
                    candidate.accused_time,
                    candidate.source_episode,
                    candidate.source_time,
                    1.0,
                )
            )
    best_count = 0
    best_span = 0.0
    for episode, matches in by_episode.items():
        for peak in aligner._sparse_peaks(episode, matches):
            for run in aligner._continuous_runs(aligner._support_for_peak(peak, matches)):
                frames = {round(item.accused_time * 2) for item in run}
                span = run[-1].accused_time - run[0].accused_time + 0.5 if run else 0.0
                if (len(frames), span) > (best_count, best_span):
                    best_count, best_span = len(frames), span
    return {"actual_frame_count": best_count, "temporal_span_seconds": best_span}


def _enrich_positive(row: dict[str, Any], candidates: list[CandidateMatch]) -> None:
    truth = row["truth"][0]
    effective = [c for c in candidates if c.visual_score >= ALIGNER_ADMISSION_SCORE]
    row["effective_candidate_count"] = len(effective)
    row["input_candidate_count"] = len(candidates)
    row["raw_wrong_source_line"] = _maximum_wrong_source_line(candidates, truth["source_episode"])
    row["effective_wrong_source_line"] = _maximum_wrong_source_line(effective, truth["source_episode"])
    first = row["predictions"][0] if row["predictions"] else None
    row["first_primary_wrong_source"] = bool(first and first["source_episode"] != truth["source_episode"])
    row["correct_complete_alternative"] = any(
        _mapping_matches_truth(_dict_segment(item), truth) for item in row["alternatives"]
    )
    row["wrong_primary_but_safe_alternative"] = bool(
        row["first_primary_wrong_source"]
        and row["outcome"] == "SAFE_AMBIGUITY_CONTAINS_GT"
        and row["correct_complete_alternative"]
    )
    row["wrong_primary_without_correct_alternative"] = bool(
        row["first_primary_wrong_source"] and not row["correct_complete_alternative"]
    )
    raw_support = row["retrieval_attribution_detail"]
    effective_support = row["post_threshold_support"]
    if row["boundary_assertion_outcome"] == "FULL_MAPPING_CORRECT":
        category = None
    elif row["transformation"] == "PIP_REDUCED_VIEWPORT" and not raw_support["retrieval_sufficient_for_full_boundary"]:
        category = "PIP_GLOBAL_EMBEDDING_LIMIT"
    elif not raw_support["retrieval_sufficient_for_any_match"]:
        category = "RETRIEVAL"
    elif not effective_support["retrieval_sufficient_for_any_match"]:
        category = "SCORE_CONTRACT"
    elif (
        row["first_primary_wrong_source"]
        and row["effective_wrong_source_line"]["actual_frame_count"] >= 6
        and effective_support["retrieval_sufficient_for_full_boundary"]
    ):
        category = "TRUE_VISUAL_AMBIGUITY"
    elif effective_support["retrieval_sufficient_for_full_boundary"]:
        category = "ALIGNMENT_LIMIT"
    else:
        category = "SCORE_CONTRACT_OR_ALIGNMENT_LIMIT"
    row["phase2b3_failure_category"] = category


def _dict_segment(value: dict[str, Any]) -> Any:
    """Attribute-view adapter for frozen private evaluator helper."""

    class View:
        pass

    result = View()
    for key, item in value.items():
        setattr(result, key, item)
    return result


def _record_dct_audit(captured: list[CandidateMatch]) -> list[dict[str, Any]]:
    ranks: Counter[float] = Counter()
    audit: list[dict[str, Any]] = []
    for candidate in captured:
        ranks[candidate.accused_time] += 1
        audit.append(
            {
                "accused_time": candidate.accused_time,
                "source_time": candidate.source_time,
                "source_id": candidate.source_episode,
                "rank": ranks[candidate.accused_time],
                "raw_cosine": candidate.visual_score * 2 - 1,
                "adapted_visual_score": candidate.visual_score,
                "retained": True,
            }
        )
    return audit


def run_cases(
    cases: list[dict[str, Any]],
    corpus: dict[str, Any],
    policies: list[ScorePolicy],
    args: argparse.Namespace,
    stage: str,
) -> dict[str, Any]:
    indexes = {
        name: retrieval._load_index(args.index_dir / f"{name}-source-index.npz")
        for name in ("dct", "dino")
    }
    for key in ("source_time", "source_alias"):
        np.testing.assert_array_equal(indexes["dct"][key], indexes["dino"][key])
    if not np.all(indexes["dino"]["source_time"] * 2 == np.round(indexes["dino"]["source_time"] * 2)):
        raise ValueError("source index is not on the frozen 2 FPS grid")
    encoder = retrieval.DinoOnnxEncoder(args.model)
    matcher = phase2b2.prepare_dct(corpus, args.index_dir, args.output)
    config = matcher.config.matching
    if config != MatchingConfig():
        raise ValueError("hidden Temporal Alignment config override")
    rows: list[dict[str, Any]] = []
    inference: list[dict[str, Any]] = []
    started = time.perf_counter()
    for ordinal, case in enumerate(cases, 1):
        print(f"{stage} {ordinal}/{len(cases)} {case['case_id']}", flush=True)
        captured: list[CandidateMatch] = []
        original_retrieve = matcher.retriever.retrieve

        def capture(query_times: np.ndarray, query_features: np.ndarray) -> list[CandidateMatch]:
            found = original_retrieve(query_times, query_features)
            captured.extend(found)
            return found

        matcher.retriever.retrieve = capture
        dct_result = matcher.analyze_file(case["case_id"], Path(case["path"]), persist=False)
        matcher.retriever.retrieve = original_retrieve
        if dct_result.status in {"ERROR", "INCOMPLETE_INDEX"}:
            raise ValueError(f"DCT control blocked: {dct_result.reason}")
        dct_row = phase2b2.record(
            case,
            dct_result,
            captured,
            _record_dct_audit(captured),
            backend="DCT",
            phase=stage,
            runtime=dct_result.elapsed_seconds,
            frame_count=dct_result.frame_count,
        )
        if dct_row["positive"]:
            _enrich_positive(dct_row, captured)
        else:
            dct_row["input_candidate_count"] = len(captured)
            dct_row["effective_candidate_count"] = sum(
                candidate.visual_score >= ALIGNER_ADMISSION_SCORE for candidate in captured
            )
        rows.append(dct_row)

        encode_started = time.perf_counter()
        query_times, embeddings, batches = phase2b2.encode_query(Path(case["path"]), encoder)
        encode_seconds = time.perf_counter() - encode_started
        if query_times.tolist() != sorted({item.accused_time for item in captured}):
            raise ValueError("DCT and DINO query grids differ")
        retrieval_started = time.perf_counter()
        frames = phase2b2.retrieve_frames(query_times, embeddings, indexes["dino"])
        retrieval_seconds = time.perf_counter() - retrieval_started
        inference.append(
            {
                "case_id": case["case_id"],
                "actual_batch_lengths": batches,
                "encode_seconds": encode_seconds,
                "retrieval_seconds": retrieval_seconds,
            }
        )
        first_identity: tuple[tuple[Any, ...], ...] | None = None
        for policy in policies:
            stream = adapt_candidate_frames(frames, policy)
            identity = tuple(
                (a["accused_time"], a["source_time"], a["source_id"], a["rank"], a["raw_cosine"])
                for a in stream.audit
            )
            if first_identity is None:
                first_identity = identity
            elif identity != first_identity:
                raise ValueError("tau policy changed fixed candidate identity")
            align_started = time.perf_counter()
            alignment = TemporalAligner(config).align_with_diagnostics(list(stream.candidates), index_complete=True)
            row = phase2b2.record(
                case,
                alignment,
                list(stream.candidates),
                list(stream.audit),
                backend=policy.name,
                phase=stage,
                runtime=time.perf_counter() - align_started,
                frame_count=len(query_times),
            )
            if row["positive"]:
                _enrich_positive(row, list(stream.candidates))
            else:
                row["input_candidate_count"] = len(stream.candidates)
                row["effective_candidate_count"] = sum(
                    candidate.visual_score >= ALIGNER_ADMISSION_SCORE for candidate in stream.candidates
                )
            rows.append(row)
    return {
        "rows": rows,
        "inference": inference,
        "runtime_seconds": time.perf_counter() - started,
        "source_frame_count": len(indexes["dino"]["source_time"]),
    }


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": int(numerator),
        "denominator": int(denominator),
        "rate": numerator / denominator if denominator else None,
    }


def extended_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = phase2b2.summary(rows)
    positives = [row for row in rows if row["positive"]]
    all_rows = rows
    result.update(
        total_alternatives=sum(len(row["alternatives"]) for row in positives),
        ambiguous_case_count=sum(row["status"] == "AMBIGUOUS" for row in positives),
        wrong_primary_but_safe_alternative_count=sum(
            row.get("wrong_primary_but_safe_alternative", False) for row in positives
        ),
        wrong_primary_without_correct_alternative_count=sum(
            row.get("wrong_primary_without_correct_alternative", False) for row in positives
        ),
        input_candidate_count=sum(row.get("input_candidate_count", 0) for row in all_rows),
        effective_candidate_count=sum(row.get("effective_candidate_count", 0) for row in all_rows),
        raw_gt_supported_frame_count=sum(
            row["retrieval_attribution_detail"]["actual_supported_frame_count"] for row in positives
        ),
        effective_gt_supported_frame_count=sum(
            row["post_threshold_support"]["actual_supported_frame_count"] for row in positives
        ),
        raw_maximum_wrong_source_line_frames=sum(
            row["raw_wrong_source_line"]["actual_frame_count"] for row in positives
        ),
        effective_maximum_wrong_source_line_frames=sum(
            row["effective_wrong_source_line"]["actual_frame_count"] for row in positives
        ),
        failure_categories=dict(Counter(row.get("phase2b3_failure_category") for row in positives if row.get("phase2b3_failure_category"))),
    )
    return result


def summaries(rows: list[dict[str, Any]], backends: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    overall = {name: extended_summary([row for row in rows if row["backend"] == name]) for name in backends}
    by_transform = {
        name: {
            transform: extended_summary(
                [row for row in rows if row["backend"] == name and row["transformation"] == transform]
            )
            for transform in retrieval.TRANSFORMATIONS
        }
        for name in backends
    }
    return overall, by_transform


def a010_regression(rows: list[dict[str, Any]], backend: str) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for transform in A010_TRANSFORMS:
        matches = [
            row for row in rows
            if row["backend"] == backend
            and row["case_id"] == f"SOURCE_A-POSITION_010-{transform}"
        ]
        if len(matches) != 1:
            raise ValueError(f"missing unique A010 {transform} DEV regression")
        row = matches[0]
        failure = bool(
            row["outcome"] == "UNSAFE_WRONG_MATCH"
            and row["first_primary_wrong_source"]
            and not row["correct_complete_alternative"]
        )
        checks[transform] = {
            "passed": not failure,
            "outcome": row["outcome"],
            "status": row["status"],
            "first_primary_wrong_source": row["first_primary_wrong_source"],
            "correct_complete_alternative": row["correct_complete_alternative"],
            "wrong_primary_but_safe_alternative": row["wrong_primary_but_safe_alternative"],
            "raw_correct_source_supported_frames": row["retrieval_attribution_detail"]["actual_supported_frame_count"],
            "effective_correct_source_supported_frames": row["post_threshold_support"]["actual_supported_frame_count"],
            "raw_rival_supported_frames": row["raw_wrong_source_line"]["actual_frame_count"],
            "effective_rival_supported_frames": row["effective_wrong_source_line"]["actual_frame_count"],
        }
    return {"passed": all(item["passed"] for item in checks.values()), "cases": checks}


def policy_eligibility(dev: dict[str, Any]) -> dict[str, Any]:
    baseline = dev["summaries"]["DCT"]
    baseline_t = dev["by_transformation"]["DCT"]
    result: dict[str, Any] = {}
    for policy in POLICY_GRID:
        current = dev["summaries"][policy.name]
        current_t = dev["by_transformation"][policy.name]
        geometry_deltas = {
            transform: current_t[transform]["safe_success"]["numerator"]
            - baseline_t[transform]["safe_success"]["numerator"]
            for transform in ("MIRROR", "CENTER_CROP", "VERTICAL_9_16")
        }
        rival = a010_regression(dev["cases"], policy.name)
        criteria = {
            "total_safety": current["taxonomy"]["UNSAFE_WRONG_MATCH"]
            <= baseline["taxonomy"]["UNSAFE_WRONG_MATCH"],
            "total_coverage": current["safe_success"]["numerator"]
            >= baseline["safe_success"]["numerator"],
            "direct_stability": (
                current_t["DIRECT"]["safe_success"]["numerator"]
                >= baseline_t["DIRECT"]["safe_success"]["numerator"] - 1
                and current_t["DIRECT"]["taxonomy"]["UNSAFE_WRONG_MATCH"]
                <= baseline_t["DIRECT"]["taxonomy"]["UNSAFE_WRONG_MATCH"] + 1
            ),
            "h264_stability": (
                current_t["H264_REENCODE"]["safe_success"]["numerator"]
                >= baseline_t["H264_REENCODE"]["safe_success"]["numerator"] - 1
                and current_t["H264_REENCODE"]["taxonomy"]["UNSAFE_WRONG_MATCH"]
                <= baseline_t["H264_REENCODE"]["taxonomy"]["UNSAFE_WRONG_MATCH"] + 1
            ),
            "speed_stability": all(
                current_t[transform]["safe_success"]["numerator"]
                >= baseline_t[transform]["safe_success"]["numerator"] - 1
                for transform in ("SPEED_0_8X", "SPEED_1_25X")
            ),
            "geometry_signal": sum(delta >= 4 for delta in geometry_deltas.values()) >= 2,
            "dev_negative_safety": current["negatives"]["emitted"] == 0,
            "a010_rival_preservation": rival["passed"],
        }
        result[policy.name] = {
            "eligible": all(criteria.values()),
            "criteria": criteria,
            "geometry_safe_deltas": geometry_deltas,
            "a010": rival,
        }
    return result


def select_policy(dev: dict[str, Any]) -> dict[str, Any]:
    if dev.get("stage") != "DEV":
        raise ValueError("policy selection accepts DEV results only")
    if any(row["split"] != "DEV" for row in dev.get("cases", [])):
        raise ValueError("non-DEV record supplied to selection")
    eligibility = policy_eligibility(dev)
    choices: list[tuple[tuple[Any, ...], ScorePolicy]] = []
    for policy in POLICY_GRID:
        if not eligibility[policy.name]["eligible"]:
            continue
        overall = dev["summaries"][policy.name]
        by_transform = dev["by_transformation"][policy.name]
        geometry_safe = sum(
            by_transform[transform]["safe_success"]["numerator"]
            for transform in ("MIRROR", "CENTER_CROP", "VERTICAL_9_16")
        )
        source_mae = overall["source_boundary_mae_seconds"]
        accused_mae = overall["accused_boundary_mae_seconds"]
        key = (
            -overall["safe_success"]["numerator"],
            -geometry_safe,
            overall["taxonomy"]["UNSAFE_WRONG_MATCH"],
            overall["taxonomy"]["NO_MATCH"],
            source_mae if source_mae is not None else math.inf,
            accused_mae if accused_mae is not None else math.inf,
            -policy.tau,
        )
        choices.append((key, policy))
    chosen = min(choices, key=lambda item: item[0])[1] if choices else None
    return {
        "selected_policy": chosen.name if chosen else None,
        "selected_tau": chosen.tau if chosen else None,
        "eligible_policies": [policy.name for _, policy in sorted(choices, key=lambda item: item[0])],
        "eligibility": eligibility,
        "selection_reason": (
            "constraints first; then maximize total safe, maximize geometry safe, minimize unsafe, "
            "minimize no-match, minimize source/accused MAE, prefer higher tau"
        ),
    }


def run_dev(args: argparse.Namespace) -> None:
    lock = ensure_dev_lock(args)
    target = args.output / "dev.json"
    if target.exists():
        raise ValueError("DEV result already exists; refusing overwrite")
    corpus, negatives = read(args.corpus), read(args.negatives)
    validate_dev_inputs(corpus, negatives)
    result = run_cases(corpus["queries"] + negatives["queries"], corpus, list(POLICY_GRID), args, "DEV")
    backends = ["DCT", *[policy.name for policy in POLICY_GRID]]
    overall, by_transform = summaries(result["rows"], backends)
    payload = {
        "stage": "DEV",
        "protocol_commit": PROTOCOL_COMMIT,
        "identity_lock_digest": digest(lock),
        "implementation_identity": lock["execution_identity"],
        "completed_at": now(),
        "cases": result["rows"],
        "inference": result["inference"],
        "runtime_seconds": result["runtime_seconds"],
        "source_frame_count": result["source_frame_count"],
        "summaries": overall,
        "by_transformation": by_transform,
    }
    payload["eligibility"] = policy_eligibility(payload)
    write(target, payload, exclusive=True)


def freeze(args: argparse.Namespace) -> None:
    verify_protocol()
    output = _assert_workspace(args.output)
    target = output / "selected-score-contract.json"
    if target.exists():
        raise ValueError("selected score contract already exists; reselection forbidden")
    dev = read(output / "dev.json")
    if dev.get("stage") != "DEV" or dev.get("protocol_commit") != PROTOCOL_COMMIT:
        raise ValueError("invalid DEV result provenance")
    current = execution_identity(args)
    if dev["implementation_identity"] != current:
        raise ValueError("implementation/model/index changed since DEV")
    selection = select_policy(dev)
    if not selection["selected_policy"]:
        raise ValueError("NO ELIGIBLE GLOBAL SCORE CONTRACT; fresh data remains forbidden")
    selected_summary = dev["summaries"][selection["selected_policy"]]
    payload = {
        **selection,
        "protocol_commit": PROTOCOL_COMMIT,
        "score_mapping_version": SCORE_MAPPING_VERSION,
        "dino_batch_size": DINO_BATCH_SIZE,
        "dev_aggregate_digest": digest({"summaries": dev["summaries"], "eligibility": dev["eligibility"]}),
        "selected_metrics": selected_summary,
        "execution_identity": current,
        "model_public_identity": current["model_public_identity"],
        "source_index_provenance_reference": f"validated Phase 2B-1 arrays at {PHASE2B1_COMMIT}",
        "selection_source": "DEV_ONLY",
        "fresh_metrics_used": False,
        "frozen_at": now(),
    }
    write(target, payload, exclusive=True)
    print(json.dumps({k: v for k, v in payload.items() if k not in {"execution_identity", "selected_metrics"}}, indent=2))


def _legal_ranges(duration: float, old_starts: list[float]) -> list[tuple[float, float]]:
    lower, upper = 30.0, duration - 30.0 - FRESH_SPAN_SECONDS
    if upper <= lower:
        raise ValueError("source too short for registered fresh sampling domain")
    blocked = sorted(
        (max(lower, start - START_SEPARATION_SECONDS), min(upper, start + START_SEPARATION_SECONDS))
        for start in old_starts
        if min(upper, start + START_SEPARATION_SECONDS) >= max(lower, start - START_SEPARATION_SECONDS)
    )
    merged: list[tuple[float, float]] = []
    for start, end in blocked:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    ranges: list[tuple[float, float]] = []
    cursor = lower
    for start, end in merged:
        if start > cursor:
            ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < upper:
        ranges.append((cursor, upper))
    return [(start, end) for start, end in ranges if end - start > 1e-9]


def _map_legal_mass(ranges: list[tuple[float, float]], coordinate: float) -> float:
    remaining = coordinate
    for start, end in ranges:
        width = end - start
        if remaining <= width:
            return start + remaining
        remaining -= width
    raise ValueError("legal-mass coordinate outside ranges")


def select_fresh_positions(old_corpus: dict[str, Any]) -> list[dict[str, Any]]:
    """Registered deterministic selection; call only after external freeze guard."""

    rng = random.Random(FRESH_SEED)
    positions: list[dict[str, Any]] = []
    for source in sorted(old_corpus["sources"], key=lambda item: item["alias"]):
        alias = source["alias"]
        duration = float(source["duration_seconds"])
        old_starts = sorted(
            float(item["source_start_seconds"])
            for item in old_corpus["positions"]
            if item["source_alias"] == alias
        )
        ranges = _legal_ranges(duration, old_starts)
        mass = sum(end - start for start, end in ranges)
        if len(ranges) == 0 or mass <= 0:
            raise ValueError(f"{alias} has no legal fresh sampling mass")
        starts: list[float] = []
        for stratum in range(FRESH_POSITIONS_PER_SOURCE):
            u = 0.1 + 0.8 * rng.random()
            coordinate = (stratum + u) * mass / FRESH_POSITIONS_PER_SOURCE
            starts.append(round(_map_legal_mass(ranges, coordinate), 6))
        lower, upper = 30.0, duration - 38.0
        if any(not lower <= start <= upper for start in starts):
            raise ValueError(f"{alias} fresh start outside registered domain")
        if any(abs(start - old) < START_SEPARATION_SECONDS - 1e-6 for start in starts for old in old_starts):
            raise ValueError(f"{alias} fresh position violates old-content exclusion")
        if any(right - left < START_SEPARATION_SECONDS - 1e-6 for left, right in zip(starts, starts[1:])):
            raise ValueError(f"{alias} fresh positions violate fresh-fresh separation")
        for ordinal, start in enumerate(starts, 1):
            positions.append(
                {
                    "position_id": f"{alias}-FRESH_POSITION_{ordinal:03d}",
                    "source_alias": alias,
                    "source_start_seconds": start,
                    "source_end_seconds": round(start + FRESH_SPAN_SECONDS, 6),
                    "normalized_source_start": round(start / duration, 8),
                    "source_span_seconds": FRESH_SPAN_SECONDS,
                    "ordinal": ordinal,
                }
            )
    if len(positions) != 10:
        raise ValueError("fresh selector did not produce exactly 10 positions")
    return positions


def _generate_fresh_positive_corpus(old: dict[str, Any], positions: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    overlay = root / "assets" / "subtitle-overlay.png"
    retrieval.create_subtitle_overlay(overlay)
    source_paths = {item["alias"]: Path(item["path"]) for item in old["sources"]}
    queries: list[dict[str, Any]] = []
    total = len(positions) * len(retrieval.TRANSFORMATIONS)
    for index, position in enumerate(positions):
        for transform_index, transform in enumerate(retrieval.TRANSFORMATIONS):
            ordinal = index * len(retrieval.TRANSFORMATIONS) + transform_index + 1
            spec = retrieval.transformation_spec(transform)
            case_id = f"{position['position_id']}-{transform}"
            target = root / "queries" / transform / f"{case_id}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            command = retrieval._query_command(
                source_paths[position["source_alias"]], target,
                float(position["source_start_seconds"]), spec, overlay,
            )
            retrieval._run(command)
            metadata = probe_media(target)
            expected_duration = FRESH_SPAN_SECONDS / float(spec["speed_ratio"])
            if abs(metadata.duration - expected_duration) > 0.12:
                raise ValueError(f"fresh query duration violates registered transform: {case_id}")
            queries.append(
                {
                    "case_id": case_id,
                    "position_id": position["position_id"],
                    "source_alias": position["source_alias"],
                    "source_start_seconds": position["source_start_seconds"],
                    "source_end_seconds": position["source_end_seconds"],
                    "normalized_source_start": position["normalized_source_start"],
                    "source_span_seconds": FRESH_SPAN_SECONDS,
                    "query_duration_seconds": metadata.duration,
                    "speed_ratio": spec["speed_ratio"],
                    "transformation": transform,
                    "path": str(target.resolve()),
                    "transformation_spec": spec,
                }
            )
            print(f"fresh corpus {ordinal}/{total} {case_id}", flush=True)
    return {
        "schema_version": "phase2b3-fresh-corpus-v0.1",
        "seed": FRESH_SEED,
        "sources": old["sources"],
        "positions": positions,
        "queries": queries,
        "transformations": list(retrieval.TRANSFORMATIONS),
    }


def fresh_negative_parameters(index: int) -> dict[str, Any]:
    zero = int(index)
    return {
        "hue_degrees": (FRESH_SEED % 360 + 17 + 53 * zero) % 360,
        "grid_pixels": 70 + 14 * (zero % 5),
        "saturation": round(0.73 + 0.06 * (zero % 4), 2),
    }


def _generate_fresh_negatives(root: Path) -> dict[str, Any]:
    old_parameters = {
        ((index * 37) % 360, 64 + (index % 5) * 16, round(0.7 + (index % 4) * 0.15, 2))
        for index in range(20)
    }
    records: list[dict[str, Any]] = []
    for index in range(10):
        params = fresh_negative_parameters(index)
        signature = (params["hue_degrees"], params["grid_pixels"], params["saturation"])
        if signature in old_parameters:
            raise ValueError("fresh negative duplicates an old negative parameter tuple")
        case_id = f"FRESH_NEGATIVE_{index + 1:03d}"
        target = root / f"{case_id}.mp4"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            "testsrc2=size=1280x720:rate=25:duration=8", "-vf",
            f"hue=h={params['hue_degrees']}:s={params['saturation']:.2f},"
            f"drawgrid=w={params['grid_pixels']}:h={params['grid_pixels']}:t=2:c=white@0.35",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p", "-y", str(target),
        ]
        target.parent.mkdir(parents=True, exist_ok=True)
        retrieval._run(command)
        records.append(
            {
                "case_id": case_id,
                "position_id": case_id,
                "transformation": "NEGATIVE",
                "query_duration_seconds": probe_media(target).duration,
                "path": str(target.resolve()),
                "generator_parameters": params,
            }
        )
        print(f"fresh negative {index + 1}/10 {case_id}", flush=True)
    return {"schema_version": "phase2b3-fresh-negatives-v0.1", "seed": FRESH_SEED, "queries": records}


def make_fresh(args: argparse.Namespace) -> None:
    selected = verify_selected(args)
    fresh_root = args.output / "fresh"
    manifest_path = args.output / "fresh-corpus.json"
    negative_path = args.output / "fresh-negatives.json"
    if manifest_path.exists() or negative_path.exists() or fresh_root.exists():
        raise ValueError("fresh output already exists; refusing selection/regeneration")
    old = read(args.corpus)
    validate_dev_inputs(old, read(args.negatives))
    started_at = now()
    positions = select_fresh_positions(old)
    corpus = _generate_fresh_positive_corpus(old, positions, fresh_root / "corpus")
    negatives = _generate_fresh_negatives(fresh_root / "negatives")
    provenance = {
        "selected_policy_digest": digest(selected),
        "selection_frozen_at": selected["frozen_at"],
        "fresh_generation_started_at": started_at,
        "fresh_generation_completed_at": now(),
    }
    corpus["provenance"] = provenance
    negatives["provenance"] = provenance
    write(manifest_path, corpus, exclusive=True)
    write(negative_path, negatives, exclusive=True)


def held_out_policies(selected: dict[str, Any]) -> tuple[ScorePolicy, ...]:
    """Return the one frozen policy; an unregistered/unselected replay is impossible."""

    if selected.get("selection_source") != "DEV_ONLY" or selected.get("fresh_metrics_used") is not False:
        raise ValueError("selected policy was not frozen from DEV only")
    name = selected.get("selected_policy")
    if name not in POLICY_BY_NAME:
        raise ValueError("frozen selected policy is absent from the registered grid")
    return (POLICY_BY_NAME[name],)


def validate_fresh_inputs(
    old: dict[str, Any], corpus: dict[str, Any], negatives: dict[str, Any], selected: dict[str, Any]
) -> None:
    expected_positions = select_fresh_positions(old)
    if corpus.get("positions") != expected_positions:
        raise ValueError("fresh positions differ from the pre-registered deterministic selector")
    if corpus.get("transformations") != list(retrieval.TRANSFORMATIONS):
        raise ValueError("fresh transformation registry changed")
    expected_cells = {
        (position["position_id"], transform)
        for position in expected_positions
        for transform in retrieval.TRANSFORMATIONS
    }
    actual_cells = [
        (query["position_id"], query["transformation"])
        for query in corpus.get("queries", [])
    ]
    if len(actual_cells) != 110 or len(actual_cells) != len(set(actual_cells)) or set(actual_cells) != expected_cells:
        raise ValueError("fresh positive cells are incomplete or duplicated")
    negative_queries = negatives.get("queries", [])
    if [item.get("case_id") for item in negative_queries] != [
        f"FRESH_NEGATIVE_{index:03d}" for index in range(1, 11)
    ]:
        raise ValueError("fresh negative aliases differ from the registered sequence")
    if [item.get("generator_parameters") for item in negative_queries] != [
        fresh_negative_parameters(index) for index in range(10)
    ]:
        raise ValueError("fresh negative parameters differ from the pre-registered generator")
    provenance = corpus.get("provenance", {})
    if provenance != negatives.get("provenance"):
        raise ValueError("fresh positive/negative provenance differs")
    if provenance.get("selected_policy_digest") != digest(selected):
        raise ValueError("fresh inputs were not generated after this policy freeze")
    if not (
        selected["frozen_at"] <= provenance.get("fresh_generation_started_at", "")
        <= provenance.get("fresh_generation_completed_at", "")
    ):
        raise ValueError("fresh generation chronology violates the policy freeze")


def run_fresh_held_out(args: argparse.Namespace) -> None:
    selected = verify_selected(args)
    target = args.output / "fresh-held-out.json"
    if target.exists():
        raise ValueError("fresh held-out result exists; refusing overwrite")
    corpus = read(args.output / "fresh-corpus.json")
    negatives = read(args.output / "fresh-negatives.json")
    old = read(args.corpus)
    validate_dev_inputs(old, read(args.negatives))
    validate_fresh_inputs(old, corpus, negatives, selected)
    (policy,) = held_out_policies(selected)
    result = run_cases(corpus["queries"] + negatives["queries"], corpus, [policy], args, "FRESH_HELD_OUT")
    backends = ["DCT", policy.name]
    overall, by_transform = summaries(result["rows"], backends)
    payload = {
        "stage": "FRESH_HELD_OUT",
        "protocol_commit": PROTOCOL_COMMIT,
        "selected_policy": policy.name,
        "selected_tau": policy.tau,
        "selected_policy_digest": digest(selected),
        "implementation_identity": selected["execution_identity"],
        "fresh_generation_provenance": corpus["provenance"],
        "completed_at": now(),
        "cases": result["rows"],
        "inference": result["inference"],
        "runtime_seconds": result["runtime_seconds"],
        "source_frame_count": result["source_frame_count"],
        "summaries": overall,
        "by_transformation": by_transform,
    }
    write(target, payload, exclusive=True)


def promotion_gate(held: dict[str, Any]) -> dict[str, Any]:
    selected = held["selected_policy"]
    dct, dino = held["summaries"]["DCT"], held["summaries"][selected]
    dct_t, dino_t = held["by_transformation"]["DCT"], held["by_transformation"][selected]
    geometry = {
        transform: dino_t[transform]["safe_success"]["numerator"]
        - dct_t[transform]["safe_success"]["numerator"]
        for transform in ("MIRROR", "CENTER_CROP", "VERTICAL_9_16")
    }
    baselines = {row["case_id"]: row for row in held["cases"] if row["backend"] == "DCT" and row["positive"]}
    newly_wrong_green = []
    pip_wrong_green = []
    for row in held["cases"]:
        if row["backend"] != selected or not row["positive"] or row["status"] != "MATCH_FOUND":
            continue
        first = row["predictions"][0] if row["predictions"] else None
        wrong_green = bool(
            first
            and first["source_episode"] != row["truth"][0]["source_episode"]
            and first["candidate_tier"] == "GREEN"
        )
        if not wrong_green:
            continue
        if baselines[row["case_id"]]["outcome"] in {
            "CORRECT_UNAMBIGUOUS_MATCH", "SAFE_AMBIGUITY_CONTAINS_GT", "NO_MATCH"
        }:
            newly_wrong_green.append(row["case_id"])
        if row["transformation"] == "PIP_REDUCED_VIEWPORT":
            pip_wrong_green.append(row["case_id"])
    criteria = {
        "total_safe_coverage": dino["safe_success"]["numerator"] >= dct["safe_success"]["numerator"],
        "total_unsafe": dino["taxonomy"]["UNSAFE_WRONG_MATCH"] <= dct["taxonomy"]["UNSAFE_WRONG_MATCH"],
        "direct": (
            dino_t["DIRECT"]["safe_success"]["numerator"] >= dct_t["DIRECT"]["safe_success"]["numerator"] - 1
            and dino_t["DIRECT"]["taxonomy"]["UNSAFE_WRONG_MATCH"] <= dct_t["DIRECT"]["taxonomy"]["UNSAFE_WRONG_MATCH"] + 1
        ),
        "h264": (
            dino_t["H264_REENCODE"]["safe_success"]["numerator"] >= dct_t["H264_REENCODE"]["safe_success"]["numerator"] - 1
            and dino_t["H264_REENCODE"]["taxonomy"]["UNSAFE_WRONG_MATCH"] <= dct_t["H264_REENCODE"]["taxonomy"]["UNSAFE_WRONG_MATCH"] + 1
        ),
        "geometry": sum(delta >= 3 for delta in geometry.values()) >= 2,
        "speed": all(
            dino_t[transform]["safe_success"]["numerator"] >= dct_t[transform]["safe_success"]["numerator"] - 1
            for transform in ("SPEED_0_8X", "SPEED_1_25X")
        ),
        "fresh_negatives": dino["negatives"]["emitted"] == 0,
        "wrong_green_safety": not newly_wrong_green,
        "pip_safety": not pip_wrong_green,
    }
    return {
        "passed": all(criteria.values()),
        "criteria": criteria,
        "geometry_safe_deltas": geometry,
        "new_wrong_source_green_count": len(newly_wrong_green),
        "pip_wrong_source_green_count": len(pip_wrong_green),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("dev", "freeze", "make-fresh", "held-out"))
    parser.add_argument("--output", type=Path, default=ROOT / "workspace/phase2b3")
    parser.add_argument("--corpus", type=Path, default=ROOT / "workspace/phase2b1/raw/corpus.json")
    parser.add_argument("--negatives", type=Path, default=ROOT / "workspace/phase2b1/raw/negatives.json")
    parser.add_argument("--index-dir", type=Path, default=ROOT / "workspace/phase2b1/index")
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    args.output = _assert_workspace(args.output)
    if args.command == "dev":
        run_dev(args)
    elif args.command == "freeze":
        freeze(args)
    elif args.command == "make-fresh":
        make_fresh(args)
    else:
        run_fresh_held_out(args)


if __name__ == "__main__":
    main()
