from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "case_id": getattr(record, "case_id", None),
            "module": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
            "duration": getattr(record, "duration", None),
        }
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    if getattr(root, "_ipvm_configured", False):
        return
    formatter = JsonFormatter()
    file_handler = logging.FileHandler(log_dir / "ipvm.jsonl", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root._ipvm_configured = True  # type: ignore[attr-defined]
