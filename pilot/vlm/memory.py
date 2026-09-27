"""Sample Ollama runner RSS during one representative inference per model."""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import time

from pilot.vlm.infer import MODELS, OUTPUT, api, ask, validate


def runner_rss_bytes():
    output = subprocess.check_output(["ps", "-axo", "rss=,command="], text=True)
    return sum(int(line.split(maxsplit=1)[0]) * 1024 for line in output.splitlines()
               if "/llama-server " in line)


def stop_loaded():
    for item in api("ps").get("models", []):
        subprocess.run(["/opt/homebrew/bin/ollama", "stop", item["name"]],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    with (OUTPUT / "sample_manifest.csv").open(encoding="utf-8-sig", newline="") as file:
        sample = next(row for row in csv.DictReader(file) if row["row_id"] == "1051")
    results = {}
    for model in MODELS:
        stop_loaded()
        peak = 0
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ask, model, sample)
            while not future.done():
                peak = max(peak, runner_rss_bytes())
                time.sleep(.25)
            peak = max(peak, runner_rss_bytes())
            response = future.result()
        validate(json.loads(response["message"]["content"]))
        results[model] = {"row_id": "1051", "peak_runner_rss_mb": round(peak / 1e6, 1),
                          "seconds": round(time.monotonic() - started, 2),
                          "method": "llama-server RSS sampled every 0.25s during one inference"}
        print(model, results[model], flush=True)
    stop_loaded()
    (OUTPUT / "memory_benchmark.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
