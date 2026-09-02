from __future__ import annotations

from pathlib import Path

from .config import AppConfig
from .database import Database


def initialize_project(config: AppConfig) -> list[Path]:
    created: list[Path] = []
    configured = [
        config.paths.originals,
        config.paths.cases,
        config.paths.golden,
        config.paths.workspace / "media",
        config.paths.workspace / "frames",
        config.paths.workspace / "audio",
        config.paths.workspace / "embeddings",
        config.paths.workspace / "transcripts",
        config.paths.workspace / "indexes",
        config.paths.database.parent,
        config.paths.output / "results",
        config.paths.output / "review",
        config.paths.output / "reports",
        config.paths.output / "logs",
    ]
    for path in configured:
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    Database(config.paths.database).initialize(config.paths.root)
    return created
