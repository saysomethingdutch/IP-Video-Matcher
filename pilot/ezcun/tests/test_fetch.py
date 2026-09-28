"""Checks for resumable OBS downloads without contacting the evidence site."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pilot.ezcun import fetch


class FetchTests(unittest.TestCase):
    def test_http_resume_keeps_partial_and_only_adds_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._check_resume(Path(tmp))

    def _check_resume(self, tmp_path):
        payload = b'first-half-second-half'
        target = tmp_path / '8233.mp4'
        partial = target.with_suffix('.mp4.part')
        partial.write_bytes(payload[:11])
        seen = []

        class Response:
            status = 206

            def __init__(self):
                self.data = payload[11:]

            def getheader(self, name):
                return f'bytes 11-{len(payload)-1}/{len(payload)}' if name == 'Content-Range' else None

            def read(self, size):
                data, self.data = self.data[:size], self.data[size:]
                return data

        class Connection:
            def __init__(self, host, port, timeout):
                seen.append(('host', host))

            def putrequest(self, method, path, skip_accept_encoding):
                seen.append(('request', method, path, skip_accept_encoding))

            def putheader(self, name, value):
                seen.append(('header', name, value))

            def endheaders(self):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with patch.object(fetch.http.client, 'HTTPSConnection', Connection), patch.object(fetch, 'MIN_FREE', 0):
            got_path, digest = fetch.download_http('https://example.com/video.mp4?signature=short',
                                                   target, len(payload))
        self.assertEqual(got_path, partial)
        self.assertEqual(partial.read_bytes(), payload)
        self.assertEqual(digest, hashlib.md5(payload).hexdigest())
        self.assertEqual([x for x in seen if x[0] == 'header'], [('header', 'Range', 'bytes=11-')])
