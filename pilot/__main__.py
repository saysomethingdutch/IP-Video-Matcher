"""Case-facing commands: python -m pilot {run,review,export} cases/NAME.yaml."""
from __future__ import annotations

import argparse

from .case_config import CaseConfig


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m pilot")
    parser.add_argument("command", choices=("run", "review", "export"))
    parser.add_argument("case_config")
    parser.add_argument("--from", dest="from_step")
    parser.add_argument("--rows", help="comma-separated row IDs")
    args = parser.parse_args(argv)
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
