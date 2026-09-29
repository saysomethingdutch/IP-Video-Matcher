"""Case-facing commands: python -m pilot {run,review,export} cases/NAME.yaml."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from .case_config import CaseConfig


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m pilot")
    parser.add_argument("command", choices=("run", "review", "export"))
    parser.add_argument("case_config")
    parser.add_argument("--from", dest="from_step")
    parser.add_argument("--rows", help="comma-separated row IDs")
    args = parser.parse_args(argv)
    if yaml.safe_load(Path(args.case_config).read_text(encoding="utf-8")).get("schema") == "tsxk-web-1":
        if args.command == "run":
            parser.error("吞噬星空分类请用 python3 -m pilot.tsxk run")
        from .review.tsxk import TsxkReviewStore
        from .review.tsxk_export import export_tsxk
        from .tsxk.config import TsxkCase
        store = TsxkReviewStore(TsxkCase(args.case_config))
        if args.command == "review":
            from .review.server import serve
            serve(store.case, store=store)
        else:
            print(export_tsxk(store))
        return
    config = CaseConfig(args.case_config)
    if args.command == "run":
        from .integration import run
        run(config, args.from_step, args.rows)
    elif args.command == "review":
        from .review.server import serve
        serve(config.case_dir, config=config)
    else:
        from .review.data import ReviewStore
        from .review.export import export_case
        print(export_case(ReviewStore(config.case_dir, config=config)))


if __name__ == "__main__":
    main()
