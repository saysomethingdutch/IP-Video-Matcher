from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import click

from .audit import write_audit_result
from .benchmarking import (
    BenchmarkRunner,
    SyntheticBenchmarkGenerator,
    prepare_demo_benchmark,
    resource_snapshot,
)
from .config import AppConfig, load_config
from .database import Database
from .doctor import doctor_as_dict
from .indexer import OriginalIndexer
from .logging_utils import configure_logging
from .matching import VideoMatcher
from .media import find_video_files
from .project import initialize_project
from .retrieval_benchmark import (
    RetrievalBenchmark,
    anonymized_aggregate_report,
    write_report,
)
from .temporal_benchmark import (
    IdealTemporalBenchmark,
    RealSegmentBenchmark,
    write_temporal_report,
)


class Context:
    def __init__(self, root: Path, config_path: Path | None, verbose: bool):
        self.config = load_config(root, config_path)
        configure_logging(self.config.paths.output / "logs", verbose)
        self.database = Database(self.config.paths.database)


def _emit(payload: object) -> None:
    click.echo(json.dumps(
        payload, ensure_ascii=False, indent=2, default=str, allow_nan=False
    ))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--root", type=click.Path(path_type=Path), default=Path.cwd(), show_default=True)
@click.option("--config", "config_path", type=click.Path(exists=True, path_type=Path))
@click.option("--verbose", is_flag=True)
@click.pass_context
def main(ctx: click.Context, root: Path, config_path: Path | None, verbose: bool) -> None:
    """IP Video Matcher — local-first auditable matching CLI."""
    try:
        ctx.obj = Context(root, config_path, verbose)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("init-project")
@click.pass_obj
def init_project(context: Context) -> None:
    """Create runtime directories and initialize SQLite."""
    created = initialize_project(context.config)
    _emit({"status": "OK", "created": [str(path) for path in created]})


@main.command("index-originals")
@click.option("--force", is_flag=True, help="Rebuild cached frame features.")
@click.pass_obj
def index_originals(context: Context, force: bool) -> None:
    """Build the reusable 2 FPS visual index for all originals."""
    initialize_project(context.config)
    try:
        results = OriginalIndexer(context.config, context.database).index_all(force=force)
    except (FileNotFoundError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    complete = all(result.status == "COMPLETE" for result in results)
    _emit({
        "status": "COMPLETE" if complete else "INCOMPLETE_INDEX",
        "episodes": [asdict(result) for result in results],
    })


def _accused_path(config: AppConfig, case_id: str) -> Path:
    case_dir = config.paths.cases / case_id
    preferred = case_dir / "accused.mp4"
    if preferred.exists():
        return preferred
    candidates = find_video_files(case_dir)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"No accused video found for {case_id} in {case_dir}")
    raise ValueError(f"Multiple video files found for {case_id}; name the target accused.mp4")


def _analyze(context: Context, case_id: str) -> dict[str, object]:
    path = _accused_path(context.config, case_id)
    result = VideoMatcher(context.config, context.database).analyze_file(case_id, path)
    audit_path = write_audit_result(result, context.config.paths.output / "results")
    return {
        "case_id": case_id,
        "status": result.status,
        "review_tier": result.review_tier,
        "reason": result.reason,
        "segments": [segment.to_dict() for segment in result.segments],
        "alternative_candidates": [segment.to_dict() for segment in result.alternatives],
        "conflict_graph": result.conflict_graph,
        "audit_path": str(audit_path),
        "elapsed_seconds": result.elapsed_seconds,
    }


@main.command("analyze")
@click.argument("case_id")
@click.pass_obj
def analyze(context: Context, case_id: str) -> None:
    """Match one input/cases/CASE_ID/accused.mp4 against originals."""
    initialize_project(context.config)
    try:
        _emit(_analyze(context, case_id))
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("analyze-all")
@click.pass_obj
def analyze_all(context: Context) -> None:
    """Analyze all case directories without guessing on failure."""
    initialize_project(context.config)
    if not context.config.paths.cases.exists():
        raise click.ClickException(f"Cases directory not found: {context.config.paths.cases}")
    results = []
    failures = []
    for case_dir in sorted(path for path in context.config.paths.cases.iterdir() if path.is_dir()):
        try:
            results.append(_analyze(context, case_dir.name))
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            failures.append({"case_id": case_dir.name, "error": str(exc)})
    _emit({"status": "OK" if not failures else "PARTIAL", "results": results, "failures": failures})


@main.command("benchmark")
@click.option("--manifest", type=click.Path(exists=True, path_type=Path))
@click.option("--generate", is_flag=True, help="Generate A–M from input/originals before running.")
@click.option("--demo", is_flag=True, help="Create deterministic synthetic originals under workspace.")
@click.option("--warm-index", is_flag=True, help="Reuse valid index cache instead of forcing a cold rebuild.")
@click.option("--all-diagnostic", is_flag=True, help="Also run Phase 2 cases as non-gating diagnostics.")
@click.option("--seed", default=42, show_default=True, type=int)
@click.option("--report", type=click.Path(path_type=Path))
@click.pass_obj
def benchmark(
    context: Context,
    manifest: Path | None,
    generate: bool,
    demo: bool,
    warm_index: bool,
    all_diagnostic: bool,
    seed: int,
    report: Path | None,
) -> None:
    """Generate and/or run the synthetic benchmark."""
    config = context.config
    database = context.database
    initialize_project(config)
    try:
        if demo:
            config, manifest = prepare_demo_benchmark(config)
            database = Database(config.paths.database)
            database.initialize(config.paths.root)
        elif generate:
            originals = find_video_files(config.paths.originals)
            output_dir = config.paths.workspace / "benchmark" / "cases"
            manifest = SyntheticBenchmarkGenerator(originals, output_dir, seed=seed).generate()
        if manifest is None:
            default_manifest = config.paths.workspace / "benchmark" / "cases" / "ground_truth.json"
            demo_manifest = config.paths.workspace / "benchmark_demo" / "cases" / "ground_truth.json"
            manifest = default_manifest if default_manifest.exists() else demo_manifest
        if not manifest.exists():
            raise FileNotFoundError("No benchmark manifest; use --generate or --demo")
        index_resource_before = resource_snapshot()
        indexing_results = OriginalIndexer(config, database).index_all(
            force=demo and not warm_index
        )
        result = BenchmarkRunner(config, database).run(
            manifest,
            phase1_only=not all_diagnostic,
            indexing_results=indexing_results,
            index_resource_before=index_resource_before,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    default_name = (
        "benchmark_all_diagnostic.json" if all_diagnostic
        else "benchmark_phase1_5_gate.json"
    )
    report_path = report or config.paths.output / "reports" / default_name
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    result["report_path"] = str(report_path.resolve())
    _emit(result)


@main.command("retrieval-benchmark")
@click.option("--samples-per-episode", default=3, show_default=True, type=click.IntRange(min=1))
@click.option("--query-duration", default=6.0, show_default=True, type=click.FloatRange(min=1.0))
@click.option(
    "--mode", "modes", multiple=True,
    type=click.Choice(["direct", "reencode", "p1", "p2", "p3"]),
    default=("direct", "p1", "p2", "p3"),
    show_default=True,
)
@click.option("--seed", default=20260902, show_default=True, type=int)
@click.option("--tolerance", default=0.26, show_default=True, type=click.FloatRange(min=0.0))
@click.option("--json-report", type=click.Path(path_type=Path))
@click.option("--aggregate-json-report", type=click.Path(path_type=Path))
@click.option("--markdown-report", type=click.Path(path_type=Path))
@click.pass_obj
def retrieval_benchmark(
    context: Context,
    samples_per_episode: int,
    query_duration: float,
    modes: tuple[str, ...],
    seed: int,
    tolerance: float,
    json_report: Path | None,
    aggregate_json_report: Path | None,
    markdown_report: Path | None,
) -> None:
    """Measure real-media frame retrieval recall@k without changing the matcher."""
    initialize_project(context.config)
    try:
        report = RetrievalBenchmark(context.config, context.database).run(
            samples_per_episode=samples_per_episode,
            query_duration_seconds=query_duration,
            modes=modes,
            seed=seed,
            tolerance_seconds=tolerance,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    output_dir = context.config.paths.output / "retrieval"
    json_path = json_report or output_dir / "retrieval_characterization.json"
    aggregate_json_path = (
        aggregate_json_report
        or output_dir / "retrieval_characterization_aggregate.json"
    )
    markdown_path = markdown_report or output_dir / "retrieval_characterization.md"
    write_report(report, json_path, markdown_path, aggregate_json_path)
    _emit({
        **anonymized_aggregate_report(report),
        "json_report": str(json_path.resolve()),
        "aggregate_json_report": str(aggregate_json_path.resolve()),
        "markdown_report": str(markdown_path.resolve()),
    })


@main.command("temporal-benchmark")
@click.option(
    "--real-media", is_flag=True,
    help="Also run the segment-level harness against the configured ignored real originals.",
)
@click.option("--samples-per-episode", default=10, show_default=True, type=click.IntRange(min=1))
@click.option("--query-duration", default=6.0, show_default=True, type=click.FloatRange(min=3.0))
@click.option("--include-speed-profiles", is_flag=True)
@click.option("--ideal-report", type=click.Path(path_type=Path))
@click.option("--real-report", type=click.Path(path_type=Path))
@click.pass_obj
def temporal_benchmark(
    context: Context,
    real_media: bool,
    samples_per_episode: int,
    query_duration: float,
    include_speed_profiles: bool,
    ideal_report: Path | None,
    real_report: Path | None,
) -> None:
    """Characterize Temporal Alignment v2 without changing visual retrieval."""
    initialize_project(context.config)
    output_dir = context.config.paths.output / "temporal"
    ideal = IdealTemporalBenchmark(context.config.matching).run()
    ideal_path = ideal_report or output_dir / "ideal_candidate_benchmark.json"
    write_temporal_report(ideal, ideal_path)
    payload: dict[str, object] = {
        "ideal_report": str(ideal_path.resolve()),
        "ideal_passed": ideal["passed"],
        "ideal_aggregate": ideal["aggregate"],
    }
    if real_media:
        try:
            real = RealSegmentBenchmark(context.config, context.database).run(
                samples_per_episode=samples_per_episode,
                source_duration_seconds=query_duration,
                include_speed_profiles=include_speed_profiles,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            raise click.ClickException(str(exc)) from exc
        real_path = real_report or output_dir / "real_segment_benchmark.json"
        write_temporal_report(real, real_path)
        payload.update({
            "real_report": str(real_path.resolve()),
            "real_aggregate": real["aggregate"],
            "real_outcome_taxonomy": real["outcome_taxonomy"],
            "real_by_profile": real["by_profile"],
        })
    _emit(payload)


@main.command("doctor")
@click.pass_obj
def doctor(context: Context) -> None:
    """Check the local runtime and optional future dependencies."""
    checks = doctor_as_dict(context.config.paths.root)
    required_failure = any(
        check["status"] == "FAIL" and check["name"] in {"python", "ffmpeg", "ffprobe"}
        for check in checks
    )
    _emit({"status": "FAIL" if required_failure else "PASS", "checks": checks})
    if required_failure:
        raise click.exceptions.Exit(1)


@main.command("export")
def export() -> None:
    """Reserved for Phase 7; intentionally does not modify any workbook."""
    _emit({
        "status": "PHASE_NOT_IMPLEMENTED",
        "message": "Excel export is excluded from the current implementation scope.",
    })


if __name__ == "__main__":
    main()
