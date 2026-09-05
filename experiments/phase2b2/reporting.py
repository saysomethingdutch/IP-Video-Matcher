"""Aggregate-only export, never copies raw rows or exact media timelines to Git."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from experiments.phase2b2.benchmark import (
    PROTOCOL, TRANSFORMS, ROOT, read, write, summary, digest,
)


def validate_sanitized(payload):
    forbidden = {"path", "file_path", "sha256", "frames", "embedding", "embeddings",
                 "candidate_audit", "source_time", "accused_time", "source_start",
                 "source_end", "accused_start", "accused_end", "predictions", "alternatives",
                 "source_start_seconds", "source_end_seconds", "truth", "implementation_identity"}
    def visit(value):
        if isinstance(value, dict):
            if forbidden.intersection(value):
                raise ValueError("raw/private key in aggregate")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            if any(s in value for s in ("/Users/", "real_test", ".mp4", ".mov", "EP01", "EP02")):
                raise ValueError("private path/media identity in aggregate")
            if re.search(r"\b[0-9a-f]{64}\b", value):
                raise ValueError("hash in aggregate")
    visit(payload)


def run_summary(rows):
    return {"overall": summary(rows), "by_transformation": {
        t: summary([r for r in rows if r["transformation"] == t]) for t in TRANSFORMS},
        "by_source": {s: summary([r for r in rows if r.get("source_alias") == s])
                      for s in ("SOURCE_A", "SOURCE_B")}}


def position_matrix(rows, names):
    matrix = []
    for position in sorted({r["position_id"] for r in rows if r["positive"]}):
        items = [r for r in rows if r["position_id"] == position]
        matrix.append({"position_alias": position,
            "normalized_source_position": items[0]["normalized_source_start"],
            "outcomes": {b: {r["transformation"]: r["outcome"] for r in items if r["backend"] == b}
                         for b in names}})
    return {"independent_position_count": len(matrix), "matrix": matrix,
            "all_transforms_safe": {b: sum(all(v in {
                "CORRECT_UNAMBIGUOUS_MATCH", "SAFE_AMBIGUITY_CONTAINS_GT"}
                for v in p["outcomes"][b].values()) for p in matrix) for b in names}}


def negative_matrix(rows):
    return [{"negative_alias": r["case_id"], "backend": r["backend"],
             "status": r["status"], "segment_count": len(r["predictions"]),
             "candidate_tiers": dict(Counter(p["candidate_tier"] for p in r["predictions"])),
             "review_tier": r["review_tier"], "best_visual_score": r["best_visual_score"],
             "maximum_support": r["maximum_support"],
             "maximum_temporal_score": r["maximum_temporal_score"],
             "source_concentration": r["source_concentration"],
             "warnings": [w["code"] for w in r["warnings"]]}
            for r in rows if not r["positive"]]


def aggregate(calibration, selected, held):
    if digest(calibration["summaries"]) != selected["calibration_metrics_digest"]:
        raise ValueError("calibration changed after freeze")
    if digest(selected) != held["selected_policy_digest"]:
        raise ValueError("held-out used a different selection")
    if not calibration["completed_at"] <= selected["frozen_at"] < held["completed_at"]:
        raise ValueError("invalid stage chronology")
    name = selected["selected_policy"]
    result = {"schema_version": "phase2b2-dino-adapter-v0.1",
        "protocol_commit": PROTOCOL, "production_commit": "93bf13b",
        "phase2b1_commit": "dccec2820750126b46baabcacb14ce0caf828c7b",
        "configuration": {"source_fps": 2.0, "query_fps": 2.0, "top_k": 8,
            "dino_batch_size": 16, "final_batch": "natural short tail; no padding",
            "backend": "DINO_RAW", "model": "dinov2-small-int8", "retrieval": "exact NumPy cosine",
            "auto_release_enabled": False, "candidate_green": "internal alignment quality only"},
        "sample_scope": {"episode_n": 2, "content_positions": 20,
            "calibration_positions": 10, "held_out_positions": 10,
            "calibration_queries": 110, "held_out_queries": 110,
            "calibration_negatives": 10, "held_out_negatives": 10,
            "independence_unit": "content position; 11 transformations paired"},
        "selection": {k: selected[k] for k in ("selected_policy", "selected_floor", "selection_source",
                       "selection_reason", "rejected", "held_out_metrics_used")},
        "chronology": {"calibration_completed": calibration["completed_at"],
                       "policy_frozen": selected["frozen_at"], "held_out_completed": held["completed_at"],
                       "implementation_identity_verified": True},
        "calibration": {b: run_summary([r for r in calibration["cases"] if r["backend"] == b])
                        for b in calibration["summaries"]},
        "held_out": {b: run_summary([r for r in held["cases"] if r["backend"] == b])
                     for b in ("DCT", name)},
        "calibration_positions": position_matrix(calibration["cases"], ("DCT", name)),
        "held_out_positions": position_matrix(held["cases"], ("DCT", name)),
        "negative_cases": {"calibration": negative_matrix(calibration["cases"]),
                           "held_out": negative_matrix(held["cases"])},
        "runtime": {"calibration_seconds": calibration["runtime_seconds"],
                    "held_out_seconds": held["runtime_seconds"],
                    "source_index": "reused validated Phase 2B-1 arrays"},
        "privacy": {"contains_media_identity": False, "contains_paths": False,
                    "contains_private_hashes": False, "contains_per_frame_records": False}}
    validate_sanitized(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT / "workspace/phase2b2")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write(args.output, aggregate(read(args.workspace / "calibration.json"),
                                read(args.workspace / "selected-adapter.json"),
                                read(args.workspace / "held_out.json")))


if __name__ == "__main__":
    main()
