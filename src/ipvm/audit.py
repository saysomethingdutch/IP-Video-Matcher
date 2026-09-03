from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .matching import AnalysisResult


def write_audit_result(result: AnalysisResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "0.4",
        "program_version": __version__,
        "created_at": datetime.now(UTC).isoformat(),
        "case_id": result.case_id,
        "status": result.status,
        "review_tier": result.review_tier,
        "reason": result.reason,
        "input": {
            "file_path": str(result.accused_path.resolve()),
            "file_size": result.accused_path.stat().st_size if result.accused_path.exists() else None,
            "sha256": result.accused_sha256,
            "media_metadata": (
                result.media_metadata.to_dict() if result.media_metadata else None
            ),
        },
        "original_assets": result.index_health.get("assets", []),
        "index_completeness": result.index_health,
        "matching": {
            "timestamp_basis": "nominal_grid_time",
            "authoritative_media_timeline": {
                "basis": "media_pts",
                "status": "NOT_YET_REFINED",
                "legal_delivery_allowed": False,
            },
            "segments": [segment.to_dict() for segment in result.segments],
            "alternative_candidates": [
                segment.to_dict() for segment in result.alternatives
            ],
            "top_competing_candidates": result.top_competing_candidates,
            "conflict_graph": result.conflict_graph,
            "matching_config": result.matching_config,
            "encoder": result.encoder,
            "visual_algorithm_version": result.matching_config.get("algorithm_version"),
            "temporal_algorithm_version": result.matching_config.get(
                "temporal_algorithm_version"
            ),
            "elapsed_seconds": result.elapsed_seconds,
            "sampled_frame_count": result.frame_count,
        },
        "not_implemented": [
            "audio_fingerprint", "asr", "semantic_analysis", "rule_engine", "excel_export"
        ],
    }
    path = output_dir / f"{result.case_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
