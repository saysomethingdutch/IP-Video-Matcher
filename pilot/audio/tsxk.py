"""Local SenseVoice facts for the TSXK web recordings (WO-103).

Only the web ledger's played recording interval is decoded.  Neither audio
nor transcripts are sent to a network service.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import csv
import fcntl
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile

from pilot.audio.pipeline import _extract_wav, _model, decode_tags, SPLIT
from pilot.content.extract import MUSIC_TAG
from pilot.textindex.text import normalize, similarity

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'workspace/tsxk'
MODEL = ROOT / 'models/sensevoice/models/iic--SenseVoiceSmall/snapshots/master'
SAMPLES = Path('/Volumes/Element/ip-video-matcher-field-test/field-test/videos')
EVIDENCE = Path('/Volumes/Element/ip-video-matcher-field-test/evidence_check30')
VERSION = 'WO-103-v3'
CHUNK_SECONDS = 4.0
MATCH_THRESHOLD = 0.50
MIN_SPEECH_CHUNKS = 3
MIN_UPLOADER_CHARS = 6
UI = ('登录', '注册', '试看', '弹幕', '倍速', '自动连播', '高清视频',
      '发送', '举报', '播放量', '订阅合集', '点赞', '关注')
LYRIC = re.compile(r'[♪♫]|歌词|作词|作曲')


def recording_duration(video: Path) -> float:
    return float(subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=nw=1:nk=1', str(video)],
        check=True, capture_output=True, text=True).stdout.strip())


def ocr_frames(video: Path, seconds: float) -> list[dict]:
    fingerprint = hashlib.sha256(
        f'{video.resolve()}:{video.stat().st_size}:{seconds:.3f}:v1'.encode()).hexdigest()[:16]
    path = STATE / 'webledger' / f'{video.stem}-{fingerprint}.jsonl'
    if not path.is_file():
        raise FileNotFoundError(f'WO-101 OCR cache missing for {video.name}: {path}')
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def caption_items(frame: dict) -> list[str]:
    """Keep the player canvas's subtitle line, excluding Chrome/Bilibili controls."""
    result = []
    for item in frame['items']:
        text = item['text'].strip()
        if not (.17 <= item['x'] <= .67 and .66 <= item['y'] <= .82):
            continue
        if len(normalize(text)) < 4 or any(word in text for word in UI):
            continue
        result.append(text)
    return result


def score_caption(text: str, frames: list[dict], start: float, end: float) -> tuple[float, str | None, bool]:
    midpoint = (start + end) / 2
    nearby = [caption for frame in frames if abs(frame['t'] - midpoint) <= 5.0
              for caption in caption_items(frame)]
    if not nearby:
        return 0.0, None, False
    ntext = normalize(text)
    score, caption = max((similarity(ntext, normalize(s)), s) for s in nearby)
    return round(score, 3), caption, True


def classify_chunks(chunks: list[dict], frames: list[dict]) -> list[dict]:
    """Subtitle matches are source dialogue; sustained mismatches are revoicing candidates."""
    for chunk in chunks:
        pieces = [s.strip() for s in SPLIT.split(chunk['text']) if normalize(s)]
        total = sum(max(1, len(normalize(s))) for s in pieces)
        elapsed = 0
        chunk['clauses'] = []
        for piece in pieces:
            weight = max(1, len(normalize(piece)))
            span = chunk['recording_end'] - chunk['recording_start']
            a = chunk['recording_start'] + span * elapsed / total
            elapsed += weight
            b = chunk['recording_start'] + span * elapsed / total
            score, caption, seen = score_caption(piece, frames, a, b)
            kind = ('source' if score >= MATCH_THRESHOLD else 'candidate'
                    if seen and len(normalize(piece)) >= MIN_UPLOADER_CHARS else 'uncertain')
            chunk['clauses'].append({'text': piece, 'recording_start': round(a, 2),
                                     'recording_end': round(b, 2), 'kind': kind,
                                     'caption_similarity': score, 'caption_match': caption,
                                     'concurrent_subtitle': seen})
    # SenseVoice sometimes transcribes song lyrics as ordinary Chinese speech
    # with a BGM tag.  Sustained music-only passages identify that context.
    music_only_run = 0.0
    music_context = False
    for chunk in chunks:
        music_only = ('BGM' in chunk['tags'] or 'Music' in chunk['tags']) and len(normalize(chunk['text'])) < 4
        music_only_run = music_only_run + chunk['recording_end'] - chunk['recording_start'] if music_only else 0.0
        music_context |= music_only_run >= 20
    def speech_candidate(chunk):
        if 'Singing' in chunk['tags']:
            return False
        if music_context and ('BGM' in chunk['tags'] or 'Music' in chunk['tags']):
            return False
        return any(clause['kind'] == 'candidate' for clause in chunk['clauses'])
    # A single OCR miss or a lyric is not enough to call uploader speech.
    candidates = [c for c in chunks if speech_candidate(c)]
    sustained = len(candidates) >= MIN_SPEECH_CHUNKS and sum(
        c['recording_end'] - c['recording_start'] for c in candidates) >= 10
    for chunk in chunks:
        for clause in chunk['clauses']:
            if clause['kind'] == 'candidate':
                clause['kind'] = 'uploader' if sustained and speech_candidate(chunk) else 'uncertain'
            clause['classification_basis'] = (
                f'同期字幕相似度 {clause["caption_similarity"]:.3f}' if clause['kind'] == 'source' else
                '持续人声与同期字幕不符，至少 3 个片段累计 10 秒' if clause['kind'] == 'uploader' else
                '歌词/字幕/人声证据不足，暂不归为 UP 主口播')
        kinds = {c['kind'] for c in chunk['clauses']}
        chunk['kind'] = next(iter(kinds)) if len(kinds) == 1 else 'mixed' if kinds else 'uncertain'
    return chunks


def music_judgment(chunks: list[dict], frames: list[dict]) -> dict:
    music = [c for c in chunks if 'BGM' in c['tags'] or 'Music' in c['tags']]
    if not music:
        return {'suspected': None, 'basis': '未检出 BGM 标签，缺少判断配乐归属的旁证'}
    for frame in frames:
        if not any(c['recording_start'] - 2 <= frame['t'] <= c['recording_end'] + 2 for c in music):
            continue
        for item in frame['items']:
            value = item['text'].strip()
            if LYRIC.search(value) and .08 <= item['x'] <= .68 and .30 <= item['y'] <= .82:
                return {'suspected': True, 'basis': '同期歌词字幕'}
            if MUSIC_TAG.match(value):
                return {'suspected': True, 'basis': '同期 B 站音乐标签'}
    continuous = 0.0
    previous_end = None
    for chunk in chunks:
        a, b = chunk['recording_start'], chunk['recording_end']
        only_music = ('BGM' in chunk['tags'] or 'Music' in chunk['tags']) and len(normalize(chunk['text'])) < 4
        if only_music and previous_end is not None and a <= previous_end + .25:
            continuous += b - a
        else:
            continuous = b - a if only_music else 0.0
        if continuous >= 20:
            return {'suspected': True, 'basis': '连续至少 20 秒仅检出音乐、未检出人声（配乐归属需人工复核）'}
        previous_end = b if only_music else None
    return {'suspected': None, 'basis': '仅有 BGM 标签，缺少歌词、音乐标签或连续纯音乐旁证'}


def video_time(recording_time: float, ledger: dict) -> tuple[float, str]:
    anchors = ledger.get('anchors') or []
    if anchors:
        anchor = min(anchors, key=lambda a: abs(a[0] - recording_time))
        return round(max(0.0, anchor[1] + recording_time - anchor[0]), 2), '最近播放器读数线性外推'
    return round(max(0.0, recording_time - ledger['play_start']), 2), '按播放起点从零估计；未见播放器读数'


def analyze(video: Path, ledger: dict, model) -> dict:
    seconds = recording_duration(video)
    start, end = ledger.get('play_start'), ledger.get('play_end')
    base = {'row_id': str(ledger['row']), 'engine': 'SenseVoice-Small',
            'status': 'ok', 'analysis_version': VERSION, 'video': str(video),
            'audio_uploaded': False, 'recording_file_seconds': round(seconds, 2)}
    if start is None or end is None or end <= start:
        return {**base, 'status': 'skipped', 'reason': '网页台账没有有效播放区间',
                'segments': [], 'played_seconds': 0}
    start, end = max(0.0, float(start)), min(seconds, float(end))
    if end <= start:
        raise ValueError('Played span is outside the recording')
    frames = ocr_frames(video, seconds)
    chunks = []
    with tempfile.TemporaryDirectory(prefix='tsxk-audio-') as temp:
        wav = Path(temp) / 'chunk.wav'
        t = start
        while t < end - .1:
            stop = min(end, t + CHUNK_SECONDS)
            _extract_wav(video, t, stop, wav)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                output = model.generate(input=str(wav), language='auto', use_itn=True,
                                        batch_size_s=30)
            raw = str(output[0].get('text') or '') if output else ''
            text, tags = decode_tags(raw)
            chunks.append({'recording_start': round(t, 2), 'recording_end': round(stop, 2),
                           'text': text, 'tags': tags, 'raw_model_text': raw})
            t = stop
    classify_chunks(chunks, frames)
    played = end - start
    def share(predicate):
        return round(sum(c['recording_end'] - c['recording_start'] for c in chunks
                         if predicate(c)) / played, 3)
    segments = []
    for chunk in chunks:
        for clause in chunk['clauses']:
            a, basis = video_time(clause['recording_start'], ledger)
            b, _ = video_time(clause['recording_end'], ledger)
            segments.append({'start': a, 'end': b,
                         'recording_start': clause['recording_start'],
                         'recording_end': clause['recording_end'],
                         'timestamp_basis': basis,
                         'text': clause['text'], 'kind': clause['kind'],
                         'episode': None, 'similarity': clause['caption_similarity'],
                         'source_basis': '同期画面字幕' if clause['kind'] == 'source' else None,
                         'caption_match': clause['caption_match'],
                         'classification_basis': clause['classification_basis']})
    speech = share(lambda c: 'Speech' in c['tags'] or len(normalize(c['text'])) >= 4)
    bgm = share(lambda c: 'BGM' in c['tags'] or 'Music' in c['tags'])
    laughter = share(lambda c: 'Laughter' in c['tags'])
    music = music_judgment(chunks, frames)
    return {**base, 'playback_spans': [[round(start, 2), round(end, 2)]],
            'played_seconds': round(played, 2), 'played_recording_seconds': round(played, 2),
            'audio_chunks': chunks, 'segments': segments,
            'transcript': ''.join(c['text'] for c in chunks),
            'uploader_speech': [s['text'] for s in segments if s['kind'] == 'uploader'],
            'speech_ratio': speech, 'bgm_ratio': bgm, 'laughter_ratio': laughter,
            'events': {'speech': speech, 'bgm': bgm, 'laughter': laughter},
            'uploader_music': music, 'suspected_uploader_music': music['suspected'],
            'speech_ratio_basis': 'SenseVoice Speech 标签或至少 4 字的转写片段，片段级估计',
            'interpretation_limit': '无逐词时间戳；原剧对白靠同期画面字幕匹配，缺字幕的语音保持未定'}


def selected_rows(examples: bool, check30: bool) -> list[tuple[str, Path, Path]]:
    if examples:
        return [(str(n), SAMPLES / f'{n}.mp4', STATE / 'webledger/samples' / f'{n}.json')
                for n in range(13, 18)]
    if check30:
        manifest = STATE / 'ezcun/manifest_check30.csv'
        rows = [row['row'] for row in csv.DictReader(manifest.open())]
        return [(row, EVIDENCE / f'{row}.mp4', STATE / 'webledger' / f'{row}.json')
                for row in rows]
    raise ValueError('Choose --examples or --check30')


def run(examples: bool, check30: bool, rows: set[str] | None = None) -> dict:
    output = STATE / 'audio' / ('samples' if examples else '')
    output.mkdir(parents=True, exist_ok=True)
    lock = STATE / 'audio/.run.lock'
    counts = {'ok': 0, 'cached': 0, 'skipped': 0, 'error': 0}
    with lock.open('w') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another TSXK audio run is active') from exc
        model = None
        for row, video, ledger_path in selected_rows(examples, check30):
            if rows and row not in rows:
                continue
            dest = output / f'{row}.json'
            ledger = json.loads(ledger_path.read_text())
            fingerprint = hashlib.sha256(json.dumps(
                [VERSION, str(video), video.stat().st_size, ledger.get('play_start'),
                 ledger.get('play_end'), ledger.get('anchors')], sort_keys=True).encode()).hexdigest()
            if dest.exists():
                prior = json.loads(dest.read_text())
                if prior.get('fingerprint') == fingerprint and prior.get('status') in ('ok', 'skipped'):
                    counts['cached'] += 1
                    print(f'AUDIO {row} cached', flush=True)
                    continue
            if model is None and ledger.get('play_start') is not None:
                model = _model(MODEL)
            try:
                result = analyze(video, ledger, model)
            except Exception as exc:
                result = {'row_id': row, 'status': 'error', 'engine': 'SenseVoice-Small',
                          'audio_uploaded': False, 'error': f'{type(exc).__name__}: {exc}',
                          'analysis_version': VERSION}
            result['fingerprint'] = fingerprint
            temp = dest.with_suffix('.json.tmp')
            temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(dest)
            counts[result['status']] += 1
            print(f'AUDIO {row} {result["status"]} '
                  f'played={result.get("played_seconds")} '
                  f'up={len(result.get("uploader_speech") or [])} '
                  f'music={result.get("suspected_uploader_music")}', flush=True)
        summary = STATE / 'audio' / ('summary_samples.json' if examples else 'summary_check30.json')
        summary.write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'AUDIO_COMPLETE {json.dumps(counts, ensure_ascii=False)}', flush=True)
    return counts


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--examples', action='store_true')
    group.add_argument('--check30', action='store_true')
    parser.add_argument('--rows', help='Comma-separated row IDs for a targeted retry')
    args = parser.parse_args()
    counts = run(args.examples, args.check30, set(args.rows.split(',')) if args.rows else None)
    if counts['error']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
