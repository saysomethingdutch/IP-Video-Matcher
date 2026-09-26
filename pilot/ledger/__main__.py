"""Run a capture-ledger case: python -m pilot.ledger run pilot/cases/qsjx_e18.yaml"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time

from .pipeline import (assign_screenshots, build_rows, build_sessions, compile_ocr,
                       frame_ocr, link_without_readings, match_sessions, observations,
                       quality_audit, resolve_nearby_identity_readings,
                       resolve_truncated_readings, screenshot_ocr, video_duration)
from .report import write_outputs, write_r2_changes
from .workbook import read_qsjx, read_zhuyu


def run_case(config_path, only_videos=None):
    config = json.loads(Path(config_path).read_text())  # JSON syntax is valid YAML.
    case = config["case"]
    rows = read_qsjx(Path(config["workbook"])) if case == "qsjx" else read_zhuyu(Path(config["workbook"]))
    names = [name for name in config["videos"] if not only_videos or name in only_videos]
    rows = [row for row in rows if row["video"] in names]
    if not rows:
        raise ValueError("no workbook rows selected")
    fps = config.get("fps", 1)
    if fps not in (1, 2):
        raise ValueError("fps must be 1 or 2")
    compile_ocr()
    modes = [False, True] if config.get("probe_full_and_crop") else [False]
    summaries = {}
    for force_full in modes:
        mode = "full" if force_full else "crop" if len(modes) == 2 else ""
        case_id = config["case_id"] + ("_" + mode if mode else "")
        output = Path("workspace/pilot") / case_id
        output.mkdir(parents=True, exist_ok=True)
        previous_summary = json.loads((output / "run_summary.json").read_text()) if (output / "run_summary.json").exists() else None
        started = time.monotonic()
        screens = screenshot_ocr(sorted(Path(config["screenshot_dir"]).glob("*")), case,
                                 Path("workspace/pilot") / config["case_id"] / "screenshot_cache")
        video_paths = {name: Path(config["video_dir"]) / config["videos"][name].get("file", name + ".MP4")
                       for name in names}
        obs_by_video = {}
        durations = {}
        frame_count = 0
        sessions = []
        for name, path in video_paths.items():
            durations[name] = video_duration(path)
            with tempfile.TemporaryDirectory(prefix="wo001-frames-") as directory:
                records = frame_ocr(path, config["videos"][name], fps, output / "ocr_cache",
                                    Path(directory), force_full=force_full)
                frame_count += len(records)
                obs = observations(records, {row["duration"] for row in rows if row["video"] == name})
                if case == "qsjx":
                    resolve_nearby_identity_readings(obs, [row for row in rows if row["video"] == name])
                obs_by_video[name] = obs
                sessions.extend(build_sessions(obs, fps, name))
            print(f"{case_id}: {name} {len(records)} frames, {sum(s['video']==name for s in sessions)} sessions", flush=True)
        mapped, offsets = assign_screenshots(screens, obs_by_video)
        from .pipeline import add_screenshots
        for _ in range(2 if case == "qsjx" else 1):
            for name, shots in mapped.items():
                add_screenshots([s for s in sessions if s["video"] == name], shots)
            linked, unmatched = match_sessions(sessions, rows)
            if case != "qsjx":
                break
            adopted = sum(resolve_truncated_readings(obs_by_video[name],
                [row for row in rows if row["video"] == name], linked) for name in names)
            if not adopted:
                break
            sessions = [session for name in names
                        for session in build_sessions(obs_by_video[name], fps, name)]
        for name, shots in mapped.items():
            add_screenshots([s for s in sessions if s["video"] == name], shots)
        linked, unmatched = match_sessions(sessions, rows)
        ledger = build_rows(rows, linked, output, video_paths, fps=fps, video_obs=obs_by_video)
        link_without_readings(ledger, mapped, obs_by_video, output, video_paths)
        audit = quality_audit(ledger, obs_by_video)
        summary = write_outputs(output, ledger, unmatched, names, durations,
                                time.monotonic() - started, frame_count, case_id, fps, audit)
        if case == "qsjx" and not only_videos:
            summary["r2_changed_rows_gt10_seconds"] = write_r2_changes(output, ledger)
        summary["screenshots_ocr"] = len(screens)
        summary["screenshots_aligned"] = sum(len(v) for v in mapped.values())
        summary["clock_offsets"] = offsets
        summary["cold_run_elapsed_seconds"] = (previous_summary.get("cold_run_elapsed_seconds", previous_summary["elapsed_seconds"])
                                               if previous_summary else summary["elapsed_seconds"])
        if previous_summary and "fresh_run_measurement" in previous_summary:
            summary["fresh_run_measurement"] = previous_summary["fresh_run_measurement"]
        (output / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        summaries[case_id] = summary
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run"])
    parser.add_argument("case")
    parser.add_argument("--videos", nargs="*", help="developer subset for a smoke run")
    args = parser.parse_args()
    run_case(args.case, args.videos)


if __name__ == "__main__":
    main()
