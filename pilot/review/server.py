"""Loopback-only review server using the Python standard library."""
from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .data import ReviewStore
from .export import export_case

STATIC = Path(__file__).parent / "static"


def serve(case: Path, port: int = 8765):
    store = ReviewStore(case)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def send_json(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path: Path):
            if not path.is_file():
                return self.send_error(404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path in ("/", "/index.html") or re.fullmatch(r"/row/\d+", path):
                return self.send_file(STATIC / "index.html")
            if path == "/static/app.js":
                return self.send_file(STATIC / "app.js")
            if path == "/static/style.css":
                return self.send_file(STATIC / "style.css")
            if path == "/api/rows":
                return self.send_json(store.all_summaries())
            match = re.fullmatch(r"/api/row/(\d+)", path)
            if match:
                try:
                    return self.send_json(store.detail(match.group(1)))
                except KeyError:
                    return self.send_error(404)
            if path.startswith("/asset/"):
                asset = (store.case / path.removeprefix("/asset/")).resolve()
                if not asset.is_relative_to(store.case) or asset.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                    return self.send_error(403)
                return self.send_file(asset)
            if path.startswith("/vlm/"):
                root = (store.case.parent / "vlm" / "full").resolve()
                asset = (root / path.removeprefix("/vlm/")).resolve()
                if not asset.is_relative_to(root) or asset.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                    return self.send_error(403)
                return self.send_file(asset)
            match = re.fullmatch(r"/api/vlm/(\d+)", path)
            if match:
                rid = match.group(1)
                if rid not in store.rows:
                    return self.send_error(404)
                folder = store.case.parent / "vlm" / "full" / rid
                return self.send_json([f"/vlm/{rid}/{p.name}" for p in sorted(folder.iterdir()) if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")] if folder.is_dir() else [])
            self.send_error(404)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1000000:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == "/api/decision":
                    answer = store.decide(str(payload["row_id"]), payload["action"], payload.get("edits"))
                elif path == "/api/batch":
                    answer = store.batch_approve(str(payload["uploader"]))
                elif path == "/api/export":
                    answer = {"path": str(export_case(store))}
                else:
                    return self.send_error(404)
                self.send_json(answer)
            except (ValueError, KeyError, TypeError) as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Review: http://localhost:{port} ({len(store.rows)} rows)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
