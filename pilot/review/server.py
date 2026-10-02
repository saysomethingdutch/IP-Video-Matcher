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


def serve(case: Path, port: int = 8765, config=None, store=None):
    store = store or ReviewStore(case, config=config)
    tsxk = getattr(store, "kind", None) == "tsxk"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def local_request(self):
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            origin = self.headers.get("Origin")
            return self.headers.get("Host", "") in hosts and (
                not origin or origin in {f"http://{name}" for name in hosts}
            )

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

        def send_video(self, path: Path):
            size = path.stat().st_size
            start, end = 0, size - 1
            header = self.headers.get("Range")
            if header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
                if not match or not match.group(1) and not match.group(2):
                    return self.send_error(416)
                if match.group(1):
                    start = int(match.group(1))
                    end = min(int(match.group(2)), end) if match.group(2) else end
                else:
                    start = max(0, size - int(match.group(2)))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            self.send_response(206 if header else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            if header:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with path.open("rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        block = handle.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if tsxk and not self.local_request():
                return self.send_error(403)
            path = unquote(urlparse(self.path).path)
            if path in ("/", "/index.html") or re.fullmatch(r"/row/\d+", path):
                return self.send_file(STATIC / ("tsxk.html" if tsxk else "index.html"))
            if tsxk and path in ("/static/tsxk.js", "/static/tsxk.css"):
                return self.send_file(STATIC / path.removeprefix("/static/"))
            if path == "/static/app.js":
                return self.send_file(STATIC / "app.js")
            if path == "/static/style.css":
                return self.send_file(STATIC / "style.css")
            if path == "/api/rows":
                return self.send_json(store.all_summaries())
            if path == "/api/cleanup-other" and tsxk:
                return self.send_json(store.cleanup_preview())
            match = re.fullmatch(r"/api/row/(\d+)", path)
            if match:
                try:
                    return self.send_json(store.detail(match.group(1)))
                except KeyError:
                    return self.send_error(404)
            if tsxk:
                match = re.fullmatch(r"/api/video/(\d+)", path)
                if match:
                    video = store.video_path(match.group(1))
                    return self.send_video(video) if video else self.send_error(404)
                match = re.fullmatch(r"/api/thumb/(\d+)/([^/]+\.jpg)", path)
                if match:
                    image = store.thumbnail_path(match.group(1), match.group(2))
                    return self.send_file(image) if image else self.send_error(404)
            if path.startswith("/asset/"):
                asset = (store.case / path.removeprefix("/asset/")).resolve()
                if not asset.is_relative_to(store.case) or asset.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                    return self.send_error(403)
                return self.send_file(asset)
            if not tsxk and path.startswith("/vlm/"):
                root = store.vlm_dir.resolve()
                asset = (root / path.removeprefix("/vlm/")).resolve()
                if not asset.is_relative_to(root) or asset.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                    return self.send_error(403)
                return self.send_file(asset)
            match = re.fullmatch(r"/api/vlm/(\d+)", path)
            if match and not tsxk:
                rid = match.group(1)
                if rid not in store.rows:
                    return self.send_error(404)
                relative = Path("r3") / rid
                folder = store.vlm_dir / relative
                if not folder.is_dir():
                    relative = Path("full") / rid
                    folder = store.vlm_dir / relative
                return self.send_json([f"/vlm/{relative}/{p.name}" for p in sorted(folder.iterdir())
                                       if p.name.startswith("collage_") and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
                                      if folder.is_dir() else [])
            self.send_error(404)

        def do_POST(self):
            try:
                if tsxk and not self.local_request():
                    return self.send_error(403)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1000000:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == "/api/decision":
                    if tsxk:
                        answer = store.decide(str(payload["row_id"]), payload["action"],
                                              payload.get("edits"), payload.get("actor"),
                                              payload.get("retain_mark", False), payload.get("mark_note", ""),
                                              payload.get('opinion_preview'),payload.get('opinion_base'))
                    else:
                        answer = store.decide(str(payload["row_id"]), payload["action"], payload.get("edits"))
                elif path == "/api/approve-row" and tsxk:
                    answer = store.approve_row(str(payload["row_id"]), payload.get("actor"))
                elif path == "/api/cleanup-other" and tsxk:
                    answer = store.cleanup_confirm(payload.get("token", ""), payload.get("actor", ""), payload.get("confirmed"))
                elif path == '/api/alternative' and tsxk:
                    answer=store.apply_alternative(str(payload['row_id']),payload.get('actor',''),payload.get('token',''))
                elif path == '/api/opinion-preview' and tsxk:
                    from pilot.tsxk.ac_filter import sync_z_opinion
                    old=str(payload['AC']);answer={'AC':sync_z_opinion(old,str(payload['Z']))}
                elif path == "/api/batch":
                    if tsxk:
                        return self.send_error(404)
                    answer = store.batch_approve(str(payload["uploader"]))
                elif path == "/api/export":
                    if tsxk:
                        from .tsxk_export import export_tsxk
                        answer = {"path": str(export_tsxk(store))}
                    else:
                        answer = {"path": str(export_case(store))}
                else:
                    return self.send_error(404)
                self.send_json(answer)
            except (ValueError, KeyError, TypeError) as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Review: http://127.0.0.1:{server.server_address[1]} ({len(store.row_ids)} rows)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
