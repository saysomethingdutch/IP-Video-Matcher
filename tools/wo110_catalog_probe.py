"""WO-110 free catalog probes; no audio upload or inference endpoint exists here.

Run via zsh -lic using the owner's existing credential environment. Credentials
are consumed only for authentication; no request/header/error-body is logged.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

ENDPOINTS = (
    ("zhipu", "https://open.bigmodel.cn/api/paas/v4/models", "ZHIPU_API_KEY"),
    ("deepseek", "https://api.deepseek.com/models", "DEEPSEEK_API_KEY"),
    ("bailian", "https://dashscope.aliyuncs.com/compatible-mode/v1/models", "DASHSCOPE_API_KEY"),
)
MODEL_FIELDS = (
    "id", "name", "object", "owned_by", "input_modalities", "output_modalities",
    "modalities", "context_window", "max_output_tokens", "capabilities",
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward an authenticated request to a different endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(spec: tuple[str, str, str]) -> dict:
    provider, url, env_name = spec
    result = {
        "provider": provider, "url": url, "method": "GET",
        "audio_uploaded": False, "inference_calls": 0,
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if env_name not in os.environ:
        return {**result, "status": "credential_not_inherited"}
    started = time.monotonic()
    try:
        request = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + os.environ[env_name]}, method="GET",
        )
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=30) as response:
            data = json.load(response)
            result["http_status"] = response.status
            result["request_id"] = response.headers.get("x-request-id")
        result["models"] = [
            {k: model[k] for k in MODEL_FIELDS if k in model}
            for model in data.get("data", [])
        ]
        result["status"] = "ok"
    except urllib.error.HTTPError as error:
        result.update(status="http_error", http_status=error.code)
    except Exception as error:
        result.update(status="transport_error", error_type=type(error).__name__)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, ENDPOINTS))
    (args.out / "catalog-probes.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    for result in results:
        print(json.dumps({
            "provider": result["provider"], "status": result["status"],
            "http_status": result.get("http_status"),
            "model_count": len(result.get("models", [])),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "audio_uploaded": False, "inference_calls": 0,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
