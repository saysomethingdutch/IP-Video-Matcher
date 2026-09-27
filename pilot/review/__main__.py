"""Run the local review server or export the reviewed workbook."""
from __future__ import annotations

import argparse
from pathlib import Path

from .data import ReviewStore
from .export import export_case
from .server import serve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "export"))
    parser.add_argument("case", type=Path)
    args = parser.parse_args()
    if args.command == "serve":
        serve(args.case)
    else:
        print(export_case(ReviewStore(args.case)))


if __name__ == "__main__":
    main()
