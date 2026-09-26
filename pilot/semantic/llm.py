"""Model adapter (WO-004b §3.3): local ollama models and two cloud multimodal models behind one call.

Cloud keys come only from the environment (DEEPSEEK_API_KEY, ZHIPU_API_KEY) and are never printed,
logged or written. Without a key the call falls back to the local model and says so in the result.
Launch cloud runs with `zsh -lic '<command>'` so the keys configured in ~/.zshrc are inherited.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
LOCAL_ALLOWED = True        # False while the local Ollama is reserved for other work (D-026): no local call, no fallback
LOCAL_TEXT = "qwen3:4b-instruct"
LOCAL_VISION = "qwen3-vl:8b-instruct-q4_K_M"


@dataclass(frozen=True)
class Backend:
    name: str
    model: str
    url: str
    key_env: str | None = None
    image_detail: str | None = None       # DeepSeek: "high", otherwise pictures are shrunk to 512x512


BACKENDS = {
    "local": Backend("local", LOCAL_TEXT, OLLAMA_URL),
    "local-vl": Backend("local-vl", LOCAL_VISION, OLLAMA_URL),
    "deepseek": Backend("deepseek", "deepseek-flash", "https://api.deepseek.com/chat/completions", "DEEPSEEK_API_KEY", "high"),
    "zhipu": Backend("zhipu", "glm-5.3-flash", "https://open.bigmodel.cn/api/paas/v4/chat/completions", "ZHIPU_API_KEY"),
}


@dataclass
class Reply:
    backend: str
    model: str
    text: str = ""
    data: dict | None = None
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)   # prompt / completion / reasoning tokens, summed over attempts
    error: str = ""
    fallback: str = ""
    attempts: int = 0


def extract_json(text: str, required: tuple[str, ...] = ()) -> dict | None:
    """The first JSON object in a reply that has the required keys (tolerates <think> blocks, ```json fences
    and text around it; an inner object of a truncated reply does not count)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict) and all(k in value for k in required):
            return value
    except ValueError:
        pass
    start = text.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:i + 1])
                    except ValueError:
                        break
                    if isinstance(value, dict) and all(k in value for k in required):
                        return value
                    break
        start = text.find("{", start + 1)
    return None


def _post(url: str, payload: dict, key: str | None, timeout: float) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def ask(backend_name: str, prompt: str, images: list[Path] = (), json_mode: bool = True,
        timeout: float = 300.0, retries: int = 1, max_tokens: int | None = None,
        required: tuple[str, ...] = ()) -> Reply:
    """max_tokens caps a local reply: qwen3:4b in JSON mode sometimes loops until the timeout, so a capped
    reply that does not parse is retried once without JSON mode. Cloud replies are not capped (their
    thinking tokens count against the cap)."""
    b = BACKENDS[backend_name]
    key = os.environ.get(b.key_env) if b.key_env else None
    fallback = ""
    if b.key_env and not key:
        fallback = f"{b.key_env} 未设置，退回本地模型"
        b = BACKENDS["local-vl" if images else "local"]
    reply = Reply(b.name, b.model, fallback=fallback)
    if b.url == OLLAMA_URL and not LOCAL_ALLOWED:
        reply.error = "本机模型暂停使用（D-026）" + (f"；{fallback}" if fallback else "")
        return reply
    if b.url == OLLAMA_URL:
        msg = {"role": "user", "content": prompt}
        if images:
            msg["images"] = [_image_b64(p) for p in images]
        payload = {"model": b.model, "messages": [msg], "stream": False,
                   "options": {"temperature": 0, "num_ctx": 8192}}
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        if json_mode and not images:
            payload["format"] = "json"
    else:
        content = []
        for p in images:
            url = {"url": f"data:image/jpeg;base64,{_image_b64(p)}"}
            if b.image_detail:
                url["detail"] = b.image_detail
            content.append({"type": "image_url", "image_url": url})
        content.append({"type": "text", "text": prompt})
        payload = {"model": b.model, "messages": [{"role": "user", "content": content}], "temperature": 0}
    reply.usage = {"prompt": 0, "completion": 0, "reasoning": 0}
    if b.key_env:
        retries = max(retries, 2)                  # cloud: proxy and rate-limit errors are usually transient
    for attempt in range(retries + 1):             # a retried call is billed twice: time and tokens add up
        reply.attempts = attempt + 1
        if attempt and b.url == OLLAMA_URL:
            payload.pop("format", None)
        t0 = time.monotonic()
        try:
            data = _post(b.url, payload, key, timeout)
        except urllib.error.HTTPError as exc:
            reply.error = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}"
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as exc:
            reply.error = f"{type(exc).__name__}: {str(exc)[:300]}"      # IncompleteRead etc.: a flaky proxy
        else:
            reply.error = ""
            if b.url == OLLAMA_URL:
                reply.text = (data.get("message") or {}).get("content", "")
                usage = {"prompt": data.get("prompt_eval_count"), "completion": data.get("eval_count"), "reasoning": 0}
            else:
                choice = (data.get("choices") or [{}])[0].get("message") or {}
                reply.text = choice.get("content") or ""
                u = data.get("usage") or {}
                details = u.get("completion_tokens_details") or {}
                usage = {"prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens"),
                         "reasoning": details.get("reasoning_tokens", 0)}
            reply.usage = {k: reply.usage[k] + (usage.get(k) or 0) for k in reply.usage}
            reply.data = extract_json(reply.text, required)
        reply.seconds = round(reply.seconds + time.monotonic() - t0, 2)
        if reply.data is not None:
            break
        reply.error = reply.error or "回复里没有可解析的 JSON" + (f"（需要 {'、'.join(required)}）" if required else "")
        if attempt < retries:
            transient = reply.error.startswith(("HTTP 429", "HTTP 5", "URLError", "TimeoutError", "IncompleteRead",
                                                "RemoteDisconnected", "ConnectionResetError"))
            time.sleep(10 * (attempt + 1) if transient else 2 * (attempt + 1))
    return reply
