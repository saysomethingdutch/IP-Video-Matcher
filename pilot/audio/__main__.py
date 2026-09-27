"""Run local audio: python -m pilot.audio run cases/example.yaml [--rows 1,2]."""
import argparse
import json

from pilot.case_config import CaseConfig
from .pipeline import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run"])
    parser.add_argument("case_config")
    parser.add_argument("--rows")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    config = CaseConfig(args.case_config)
    print(json.dumps(run(config, config.selected_rows(args.rows), force=args.force), ensure_ascii=False))


if __name__ == "__main__":
    main()
