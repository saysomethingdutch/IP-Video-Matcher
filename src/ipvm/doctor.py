from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _command_version(command: str) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    result = subprocess.run(
        [executable, "-version"], check=False, capture_output=True, text=True
    )
    return (result.stdout or result.stderr).splitlines()[0] if result.returncode == 0 else None


def _memory_detail() -> tuple[str, int | None]:
    if platform.system() != "Darwin":
        return "Unified memory check is macOS-only", None
    result = subprocess.run(
        ["sysctl", "-n", "hw.memsize"], check=False, capture_output=True, text=True
    )
    if result.returncode:
        return "Unable to read hw.memsize", None
    gib = int(result.stdout.strip()) / (1024**3)
    return f"{gib:.1f} GiB unified memory", round(gib)


def run_doctor(root: Path) -> list[Check]:
    checks: list[Check] = []
    system = platform.system()
    checks.append(Check(
        "operating_system", "PASS" if system == "Darwin" else "WARN",
        f"{system} {platform.release()} (target: macOS)",
    ))
    machine = platform.machine()
    checks.append(Check(
        "apple_silicon", "PASS" if machine == "arm64" else "WARN",
        f"{machine} (target: arm64)",
    ))
    memory_detail, memory_gib = _memory_detail()
    memory_status = "PASS" if memory_gib and memory_gib >= 16 else "WARN"
    checks.append(Check("memory", memory_status, memory_detail))
    checks.append(Check(
        "python", "PASS" if sys.version_info >= (3, 11) else "FAIL",
        platform.python_version(),
    ))
    for command in ("ffmpeg", "ffprobe"):
        version = _command_version(command)
        checks.append(Check(command, "PASS" if version else "FAIL", version or "not found"))
    usage = shutil.disk_usage(root)
    free_gib = usage.free / (1024**3)
    checks.append(Check(
        "disk_space", "PASS" if free_gib >= 10 else "WARN", f"{free_gib:.1f} GiB free"
    ))
    for module in ("faiss", "mlx"):
        available = importlib.util.find_spec(module) is not None
        checks.append(Check(
            module, "PASS" if available else "WARN",
            "available" if available else "not installed; optional/deferred after Phase 1",
        ))
    model_dir = root / "models"
    has_models = model_dir.exists() and any(model_dir.iterdir())
    checks.append(Check(
        "gemma_runtime", "PASS" if has_models else "WARN",
        "model files present" if has_models else "not configured; intentionally out of Phase 1 scope",
    ))
    return checks


def doctor_as_dict(root: Path) -> list[dict[str, str]]:
    return [asdict(check) for check in run_doctor(root)]
