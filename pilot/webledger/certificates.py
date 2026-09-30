"""Read certificate dates from PDFs reached through visible Chrome block-view pages."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import time

import fitz
from playwright.sync_api import sync_playwright

from pilot.ezcun.fetch import STATE as EZCUN_STATE, entries

OUT = EZCUN_STATE / 'certificates'
DATE = re.compile(r'(20\d{2})年(\d{1,2})月(\d{1,2})日(\d{1,2})时(\d{1,2})分(\d{1,2})秒')


def certificate_time(content: bytes) -> str:
    if not content.startswith(b'%PDF'):
        raise ValueError('Certificate response was not PDF')
    with fitz.open(stream=content, filetype='pdf') as pdf:
        text = '\n'.join(page.get_text() for page in pdf)
    section = text.split('取证时间:', 1)
    if len(section) != 2:
        raise ValueError('Certificate has no 取证时间 field')
    matches = DATE.findall(section[1].split('证据名称:', 1)[0])
    if not matches:
        raise ValueError('Certificate has no dated time interval')
    return datetime(*map(int, matches[-1])).strftime('%Y/%-m/%-d %H:%M:%S')


def collect():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(EZCUN_STATE / 'cert-chrome-profile'), channel='chrome', headless=False,
            accept_downloads=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            last_visit = 0.0
            for entry in entries():
                row = entry['row']
                output = OUT / f'{row}.json'
                if output.exists():
                    continue
                wait = 5.5 - (time.monotonic() - last_visit)
                if wait > 0:
                    time.sleep(wait)
                responses = []
                def on_response(response):
                    if response.status == 418:
                        responses.append(('challenge', response.url))
                    elif '.pdf?' in response.url and response.url.startswith('https://'):
                        responses.append(('pdf', response.url))
                page.on('response', on_response)
                last_visit = time.monotonic()
                try:
                    page.goto(entry['certificate_url'], wait_until='domcontentloaded', timeout=60000)
                    deadline = time.monotonic() + 40
                    while not responses and time.monotonic() < deadline:
                        page.wait_for_timeout(250)
                        visible = ' '.join(frame.locator('body').inner_text(timeout=2000)[:600]
                                           for frame in page.frames if frame.url.startswith('https://www.ezcun.com/'))
                        if any(term in visible for term in ('验证码', '人机验证', '安全验证', 'CloudWAF')):
                            raise RuntimeError('STOPPED_CAPTCHA')
                    if any(kind == 'challenge' for kind, _ in responses):
                        raise RuntimeError('STOPPED_CAPTCHA HTTP 418')
                    pdf_url = next((url for kind, url in responses if kind == 'pdf'), None)
                    if pdf_url is None:
                        raise ValueError('No PDF link in certificate page responses')
                    fetched = context.request.get(pdf_url, timeout=60000)
                    if fetched.status != 200:
                        raise ValueError(f'PDF HTTP {fetched.status}')
                    stamp = certificate_time(fetched.body())
                    data = {'row': row, 'certificate_finished': stamp,
                            'certificate_url': entry['certificate_url']}
                    temp = output.with_suffix('.tmp')
                    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                    temp.replace(output)
                    print(f'CERT {row} {stamp}', flush=True)
                except Exception as exc:
                    if 'STOPPED_CAPTCHA' in str(exc):
                        print(str(exc), flush=True)
                        raise
                    print(f'CERT_FAILED {row} {type(exc).__name__}: {str(exc)[:180]}', flush=True)
                finally:
                    page.remove_listener('response', on_response)
        finally:
            context.close()


if __name__ == '__main__':
    collect()
