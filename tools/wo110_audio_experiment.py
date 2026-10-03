"""WO-110 only. Offline prepare -> validate -> explicitly run Bailian -> score.

Runtime data stays in ignored workspace/audio-exp; production inputs are read-only.
No SDK retries, redirects, credential output, titles or subtitle context in requests.
"""
from __future__ import annotations

import argparse
import base64
import collections
import csv
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave

MODELS = {"A": "qwen-audio-3.1-asr-flash", "B": "qwen3.8-omni-flash"}
RESERVE = {"A": 0.02, "B": 0.90}
MAX_CALLS, MAX_CNY = 120, 50.0
QUESTION = '这段人声是解说员在第三人称讲述、点评，还是动画角色之间在对话？有几个人在说？是不是合成配音？'
PROMPT = QUESTION + '\n只依据音频判断。只输出一个 JSON 对象：kind（解说/角色对白/其他/无可辨人声/无法判断），uploader_speech（true/false/null，表示是否存在解说员或 UP 主的讲述点评），speakers（整数或 null），synthetic（是/否/无法判断），evidence（简短声音依据）。歌唱归其他，不能确定时明确说无法判断。'
ADDED = '7815,7840,7855,7863,7865,7866,7881,7911,7923,7934,7938,7958,7963,7966,8023,8036,8037,8042,8055,8057,8059,8068,8069,8076,8083,8087,8088,8089,8092,8122,8128,8129,8131,8133,8138,8139,8140,8141,8143,8153'.split(',')


def read(p):
    return json.loads(Path(p).read_text())


def write(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def norm(t):
    return ''.join(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]', t)).lower()


def sim(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b), autojunk=False).ratio()


def ocr_reference(frames, start, end):
    """Time-only reference; never use ASR agreement to choose or clean subtitles."""
    groups, current = [], []
    nframes = nonempty = 0
    evidence = []
    for frame in frames:
        if not start <= frame['t'] < end:
            continue
        nframes += 1
        texts = []
        for item in sorted(frame['items'], key=lambda x: (x['y'], x['x'])):
            t = item['text'].strip()
            if not norm(t) or (len(norm(t)) <= 6 and item['x'] > .55):
                continue  # small bottom-right logo; rule frozen before model calls
            if len(norm(t)) <= 6 and t.endswith('空'):
                continue
            if re.search(r'发送|弹幕|哔哩|bilibili|关注|点赞|收藏|投币|倍速', t, re.I):
                continue
            texts.append(t)
        text = ' '.join(texts)
        evidence.append({'t': frame['t'], 'text': text})
        if not norm(text):
            continue
        nonempty += 1
        if current and sim(current[-1], text) < .65:
            groups.append(current)
            current = []
        current.append(text)
    if current:
        groups.append(current)
    lines = [collections.Counter(g).most_common(1)[0][0] for g in groups]
    return {'text': ''.join(lines), 'lines': lines, 'frames': evidence,
            'coverage': nonempty / max(nframes, 1), 'changes': len(lines)}


def load_row(data, row):
    audio = read(data / f'audio/{row}.json')
    ledger = read(data / f'webledger/{row}.json')
    files = sorted((data / 'audio/subtitles').glob(f'{row}-*.jsonl'))
    frames = [json.loads(x) for x in files[-1].read_text().splitlines()] if files else []
    return audio, ledger, frames


def windows(audio, ledger, frames, speech_type=None, reference_required=False):
    results = []
    lo, hi = float(ledger['content_start']), float(ledger['content_end'])
    for c in audio['audio_chunks']:
        start = float(c['recording_start'])
        end = start + 24.0
        if start < lo or end > hi:
            continue
        chunks = [x for x in audio['audio_chunks'] if start <= x['recording_start'] and x['recording_end'] <= end + .001]
        # Exact cached four-second chunks make the SenseVoice baseline comparable.
        if not chunks or abs(chunks[-1]['recording_end'] - end) > .01:
            continue
        segments = [x for x in audio['segments'] if start <= x['recording_start'] < end]
        ratio = sum(x.get('speech_type') == speech_type for x in segments) / max(len(segments), 1)
        ref = ocr_reference(frames, start, end)
        if speech_type and ratio < .6:
            continue
        if reference_required and (ref['coverage'] < .75 or ref['changes'] < 4 or len(norm(ref['text'])) < 60):
            continue
        results.append({'start': start, 'end': end, 'reference': ref,
                        'sensevoice': ''.join(x.get('text', '') for x in chunks),
                        'voice_chars': sum(len(norm(x.get('text', ''))) for x in chunks),
                        'speech_type_fraction': ratio})
    return results


def protected_hashes(data):
    paths = list((data / 'rows').glob('*.json')) + list((data / 'review').rglob('*'))
    return {str(p.relative_to(data)): sha(p) for p in paths if p.is_file()}


def prepare(root, out):
    if (out / 'plan.json').exists():
        raise ValueError('Plan already exists; no reselection after model results')
    data = (root / 'workspace/tsxk').resolve()
    split = read(root / 'cases/tsxk_regression_split.json')
    dev, hold = set(map(str, split['development'])), set(map(str, split['holdout']))
    # Project labels are accessed only for development rows.
    raw_answers = read(data / 'wo108/regression-final/answers.json')['answers']
    answers = {row: raw_answers[row] for row in dev}
    del raw_answers
    rng = random.Random(20261003)
    cache = {row: load_row(data, row) for row in sorted(dev)}
    plan, eligibility = [], {}
    for group, label, speech in [('A-narration', '二创（解说类）', '旁白讲解'),
                                 ('A-dialogue', '二创（单IP混剪类）', '角色台词')]:
        eligible = {}
        for row in sorted(dev):
            if answers[row].get('Y') != label:
                continue
            ww = windows(*cache[row], speech, True)
            if ww:
                eligible[row] = ww
        eligibility[group] = {r: len(w) for r, w in eligible.items()}
        for row in rng.sample(sorted(eligible), 20):
            w = rng.choice(eligible[row])
            plan.append({'test': 'A', 'group': group, 'row': row, 'split': 'development', **w})
    known = ['7915', '7916', '7918', '7935', '7964']
    added = rng.sample(sorted(set(ADDED) & dev), 15)
    negative = ['7942', '8183', '8191', '8194']
    role_candidates = {}
    for row in sorted(dev - set(known + added + negative)):
        if answers[row].get('Y') == '二创（单IP混剪类）':
            ww = windows(*cache[row], '角色台词')
            if ww:
                role_candidates[row] = ww
    roles = rng.sample(sorted(role_candidates), 15)
    for group, rows, expected in [('B-known-narration', known, True), ('B-added-narration', added, True),
                                  ('B-no-uploader', negative, False), ('B-role-dialogue', roles, False)]:
        for row in rows:
            a, l, frames = cache[row] if row in dev else load_row(data, row)
            if row in roles:
                w = rng.choice(role_candidates[row])
            else:
                ww = windows(a, l, frames)
                if not ww:
                    raise ValueError(f'No safe full window: {row}')
                # Same label-independent voice-density rule for all named B rows.
                w = max(ww, key=lambda x: (x['voice_chars'], -x['start']))
            # No reference text is kept for B, especially the fixed held-out row.
            w = {k: v for k, v in w.items() if k not in ('reference', 'sensevoice')}
            plan.append({'test': 'B', 'group': group, 'row': row,
                         'split': 'development' if row in dev else 'holdout',
                         'expected_uploader': expected, **w})
    out.mkdir(parents=True, exist_ok=True)
    (out / 'clips').mkdir(exist_ok=True)
    write(out / 'protected-before.json', protected_hashes(data))
    for i, p in enumerate(plan, 1):
        row = p['row']
        l = read(data / f'webledger/{row}.json')
        source = data / f'audio_tracks/evidence/{row}.m4a'
        p.update(id=f"{p['test']}-{i:03d}-{row}", model=MODELS[p['test']],
                 source=str(source), source_sha256=sha(source),
                 ledger_sha256=sha(data / f'webledger/{row}.json'),
                 bv=l['bv'], address=l['address'], content_start=l['content_start'], content_end=l['content_end'])
        clip = out / f"clips/{p['id']}.wav"
        subprocess.run(['/opt/homebrew/bin/ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source),
                        '-ss', str(p['start']), '-t', str(p['end'] - p['start']), '-vn',
                        '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(clip)], check=True)
        with wave.open(str(clip)) as f:
            duration = f.getnframes() / f.getframerate()
        p.update(clip=str(clip), sha256=sha(clip), duration=duration, bytes=clip.stat().st_size)
    checked = rng.sample([p['id'] for p in plan if p['test'] == 'A'], 5)
    metadata = {'seed': 20261003, 'split_sha256': sha(root / 'cases/tsxk_regression_split.json'),
                'question': QUESTION, 'prompt': PROMPT, 'prompt_sha256': hashlib.sha256(PROMPT.encode()).hexdigest(),
                'manual_reference_ids': checked, 'eligibility': eligibility,
                'method': '24s, exact cached chunk boundaries; A >=75% OCR frames, >=4 captions, >=60 chars, >=60% required speech type; random rows/windows. B named: densest voice window; role random.'}
    write(out / 'selection.json', metadata)
    write(out / 'plan.json', plan)
    fields = ['id','test','group','row','split','bv','address','content_start','content_end',
              'start','end','duration','source','source_sha256','ledger_sha256','clip','sha256','bytes','model']
    with (out / 'upload-manifest.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows({k:p[k] for k in fields} for p in plan)
    validate(root, out)
    print(json.dumps({'prepared':len(plan), 'manual_reference_ids':checked}, ensure_ascii=False))


def validate(root, out):
    data = (root / 'workspace/tsxk').resolve()
    plan = read(out / 'plan.json')
    split = read(root / 'cases/tsxk_regression_split.json')
    dev, hold = set(map(str, split['development'])), set(map(str, split['holdout']))
    if len(plan) != 79 or len({p['id'] for p in plan}) != 79:
        raise ValueError('Expected A40+B39 unique sample IDs')
    for p in plan:
        source = Path(p['source']).resolve()
        if source != (data / f"audio_tracks/evidence/{p['row']}.m4a"):
            raise ValueError('Unauthorized source')
        ledger = data / f"webledger/{p['row']}.json"
        l = read(ledger)
        if not re.fullmatch(r'BV[0-9A-Za-z]+', l['bv']) or 'bilibili.com/video/' not in l['address']:
            raise ValueError('Not a public Bilibili ledger entry')
        if l['sources']['recording'] != f"{p['row']}.mp4":
            raise ValueError('Recording mismatch')
        if not (l['content_start'] <= p['start'] < p['end'] <= l['content_end']):
            raise ValueError('Outside ledger content interval')
        clip = Path(p['clip']).resolve()
        if clip.parent != (out / 'clips').resolve():
            raise ValueError('Clip path escaped output directory')
        with wave.open(str(clip)) as f:
            duration = f.getnframes() / f.getframerate()
            if f.getframerate() != 16000 or f.getnchannels() != 1 or f.getsampwidth() != 2:
                raise ValueError('Invalid audio format')
        if not (0 < duration <= 60 and abs(duration - (p['end']-p['start'])) < .05):
            raise ValueError('Invalid duration')
        if clip.stat().st_size * 4 / 3 > 10_000_000:
            raise ValueError('Base64 exceeds model limit')
        if sha(source) != p['source_sha256'] or sha(clip) != p['sha256'] or sha(ledger) != p['ledger_sha256']:
            raise ValueError('Provenance hash mismatch')
        if p['row'] not in dev and not (p['test']=='B' and p['row']=='7935' and p['row'] in hold):
            raise ValueError('Unauthorized held-out sampling')
        if p['model'] != MODELS[p['test']]:
            raise ValueError('Unauthorized model')
    # Stage 1 catalogs were free GETs: zero inference spend across all providers.
    worst = sum(RESERVE[p['test']] for p in plan)
    if worst >= MAX_CNY:
        raise ValueError('Budget exceeds limit')
    account = read(out / 'bailian-account-pricing.json')
    for m in account['model_pages']:
        if m['model'] in MODELS.values() and (m['input_cny_per_million_tokens'],m['output_cny_per_million_tokens']) != (.8,2.7):
            raise ValueError('Account price differs from budget')
    result = {'validated_segments':len(plan),'initial_submissions':len(plan), 'submission_cap_including_retries':120,
              'max_duration':max(p['duration'] for p in plan),'total_duration':sum(p['duration'] for p in plan),
              'conservative_reserved_cny':round(worst,6), 'cost_cap_cny':50,
              'reservation_basis': 'A: 7168 input/1024 output token model limit <0.02 CNY; B:991808 input/1024 output <0.90 CNY. Unknown usage retains reservation. No automatic retries.',
              'prior_inference_spend_cny':0, 'plan_sha256':sha(out/'plan.json'),
              'manifest_sha256':sha(out/'upload-manifest.csv'), 'selection_sha256':sha(out/'selection.json'),
              'account_pricing_sha256':sha(out/'bailian-account-pricing.json'), 'validated_at':time.time()}
    if (out / 'preflight.json').exists():
        old = read(out/'preflight.json')
        for k in ['plan_sha256','manifest_sha256','selection_sha256','account_pricing_sha256']:
            if result[k] != old[k]:
                raise ValueError('Frozen preflight changed')
    else:
        write(out/'preflight.json',result)
    return result


def ledger_events(out):
    p = out/'calls.jsonl'
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def append(out, event):
    with (out/'calls.jsonl').open('a') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def budget(events, reserve):
    intents = {e['attempt']:e for e in events if e['event']=='submit_intent'}
    ends = {e['attempt']:e for e in events if e['event']=='completed'}
    spent = sum(ends.get(k,{}).get('estimated_cny',v['reserved_cny']) for k,v in intents.items())
    if len(intents) >= MAX_CALLS or spent + reserve > MAX_CNY:
        raise ValueError('Submission or cumulative cost limit reached')
    return len(intents), spent


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def payload(p):
    audio = {'type':'input_audio','input_audio':{'data':'data:audio/wav;base64,' + base64.b64encode(Path(p['clip']).read_bytes()).decode()}}
    if p['test']=='A':
        return {'model':MODELS['A'],'input':{'messages':[{'role':'user','content':[audio]}]},
                'parameters':{'format':'wav','sample_rate':'16000'}}
    audio['input_audio']['format']='wav'
    return {'model':MODELS['B'],'messages':[{'role':'user','content':[audio,{'type':'text','text':PROMPT}]}],
            'reasoning_effort':'none','max_tokens':1024,'stream':True,'stream_options':{'include_usage':True}}


def request_one(p, account):
    # The value is consumed exclusively in authentication, never serialized to logs.
    endpoint = account['asr_endpoint'] if p['test']=='A' else account['omni_base_url']+'/chat/completions'
    if not endpoint.startswith('https://ws-sfke3xvib3ujugjk.cn-beijing.maas.aliyuncs.com/'):
        raise ValueError('Unauthorized endpoint')
    req = urllib.request.Request(endpoint, data=json.dumps(payload(p),ensure_ascii=False).encode(),
                                  headers={'Authorization':'Bearer '+os.environ['DASHSCOPE_API_KEY'],
                                           'Content-Type':'application/json','X-DashScope-SSE':'disable' if p['test']=='A' else 'enable'}, method='POST')
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=180) as response:
        if p['test']=='A':
            result = json.load(response)
            out = result.get('output',{})
            text = out.get('text') or out.get('sentence',{}).get('text')
            if not text:
                text = ''.join(s.get('text','') for s in out.get('sentences',[]))
            if not text:
                text = ''.join(c.get('text','') for m in out.get('choices',[]) for c in m.get('message',{}).get('content',[]))
            return {'text':text or '', 'usage':result.get('usage',{}), 'request_id':result.get('request_id') or response.headers.get('x-request-id'), 'output_keys':list(out)}
        content, usage, request_id, finish = [], {}, None, None
        for line in response:
            if not line.startswith(b'data:'):
                continue
            datum = line[5:].strip()
            if datum==b'[DONE]':
                break
            event = json.loads(datum)
            request_id=event.get('id',request_id)
            if event.get('usage'):
                usage=event['usage']
            for choice in event.get('choices',[]):
                content.append(choice.get('delta',{}).get('content') or '')
                finish=choice.get('finish_reason') or finish
        return {'text':''.join(content),'usage':usage,'request_id':request_id,'finish_reason':finish}


def usage_cost(usage):
    inp=usage.get('input_tokens',usage.get('prompt_tokens'))
    out=usage.get('output_tokens',usage.get('completion_tokens'))
    if not isinstance(inp,(int,float)) or not isinstance(out,(int,float)) or min(inp,out)<0:
        return None
    return (inp*.8+out*2.7)/1_000_000


def validate_supplement(root,out):
    extra=read(out/'supplement-plan.json')
    ready=read(out/'supplement-preflight.json')
    for name,expected in ready['frozen_files'].items():
        if sha(out/name)!=expected:raise ValueError('Supplement artifact changed')
    data=(root/'workspace/tsxk').resolve()
    dev=set(map(str,read(root/'cases/tsxk_regression_split.json')['development']))
    base_ids={p['id'] for p in read(out/'plan.json')}
    if len(extra)!=4 or len({p['id'] for p in extra})!=4:
        raise ValueError('Expected exactly four independently QC-selected replacement A clips')
    for p in extra:
        if p['test']!='A' or p['model']!=MODELS['A'] or p['row'] not in dev or p['id'] in base_ids:
            raise ValueError('Unauthorized supplement')
        src=(data/f"audio_tracks/evidence/{p['row']}.m4a").resolve()
        if Path(p['source']).resolve()!=src or sha(src)!=p['source_sha256']:
            raise ValueError('Supplement source mismatch')
        l=read(data/f"webledger/{p['row']}.json")
        if 'bilibili.com/video/' not in l['address'] or not re.fullmatch(r'BV[0-9A-Za-z]+',l['bv']):
            raise ValueError('Supplement not public Bilibili evidence')
        if l['sources']['recording']!=f"{p['row']}.mp4" or sha(data/f"webledger/{p['row']}.json")!=p['ledger_sha256']:
            raise ValueError('Supplement ledger mismatch')
        if not l['content_start']<=p['start']<p['end']<=l['content_end']:
            raise ValueError('Supplement outside content interval')
        clip=Path(p['clip']).resolve()
        if clip.parent!=(out/'clips').resolve() or sha(clip)!=p['sha256']:
            raise ValueError('Supplement clip mismatch')
        with wave.open(str(clip)) as f:
            duration=f.getnframes()/f.getframerate()
            if (f.getframerate(),f.getnchannels(),f.getsampwidth())!=(16000,1,2):
                raise ValueError('Supplement format invalid')
        if not 0<duration<=60 or abs(duration-(p['end']-p['start']))>.05 or clip.stat().st_size*4/3>10_000_000:
            raise ValueError('Supplement duration/size invalid')
        ref=read(out/'references-supplement.json')[p['id']]
        if ref['coverage']<.75 or ref['changes']<4 or len(norm(ref['text']))<60:
            raise ValueError('Supplement reference quality insufficient')
    return extra


def run(root, out, limit=None, supplement=False):
    validate(root,out)
    ready=read(out/'ready-to-upload.json')
    for name, expected in ready['frozen_files'].items():
        if sha(out/name)!=expected:
            raise ValueError('Pre-upload approval artifact changed: '+name)
    reviews=read(out/'ocr-manual-review.json')
    if set(reviews)!=set(read(out/'selection.json')['manual_reference_ids']):
        raise ValueError('Five reference checks required before upload')
    if any(x['exit_code'] or x['decode_messages'] for x in read(out/'decode-audit.json')):
        raise ValueError('Unresolved source decoding error')
    plan=validate_supplement(root,out) if supplement else read(out/'plan.json')
    account=read(out/'bailian-account-pricing.json')
    (out/'responses').mkdir(exist_ok=True)
    with (out/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        events=ledger_events(out)
        completed={e['id'] for e in events if e['event']=='completed'}
        unresolved=[e for e in events if e['event']=='submit_intent' and e['id'] not in completed]
        if unresolved:
            raise ValueError('Unresolved prior attempt: no automatic retry')
        count=0
        # Two development smoke requests first; same frozen prompt for the entire run.
        ordered=list(plan) if supplement else [next(p for p in plan if p['test']=='A'),next(p for p in plan if p['test']=='B' and p['split']=='development')]
        if not supplement:ordered += [p for p in plan if p not in ordered]
        for p in ordered:
            if p['id'] in completed:
                continue
            if limit is not None and count>=limit:
                break
            n, spent=budget(ledger_events(out),RESERVE[p['test']])
            event={'event':'submit_intent','attempt':n+1,'id':p['id'],'provider':'bailian','model':p['model'],
                   'sha256':p['sha256'],'duration':p['duration'],'reserved_cny':RESERVE[p['test']], 'started_at':time.time(),
                   'prompt_sha256': hashlib.sha256(PROMPT.encode()).hexdigest() if p['test']=='B' else None}
            append(out,event)  # Durable reservation BEFORE transmitting any audio bytes.
            started=time.monotonic()
            try:
                result=request_one(p,account)
            except BaseException as exc:
                safe={'event':'failed','attempt':n+1,'id':p['id'],'elapsed_seconds':time.monotonic()-started,
                      'error_type':type(exc).__name__,'http_status':getattr(exc,'code',None),
                      'reserved_cny':RESERVE[p['test']],'ended_at':time.time()}
                append(out,safe)
                print(json.dumps(safe),flush=True)
                raise SystemExit(2) from None
            elapsed=time.monotonic()-started
            cost=usage_cost(result['usage'])
            result.update(id=p['id'],model=p['model'],sha256=p['sha256'],elapsed_seconds=elapsed)
            write(out/f"responses/{p['id']}.json",result)
            append(out,{'event':'completed','attempt':n+1,'id':p['id'],'provider':'bailian','model':p['model'],
                        'sha256':p['sha256'],'duration':p['duration'],'request_id':result['request_id'],
                        'elapsed_seconds':elapsed,'usage':result['usage'],
                        'estimated_cny':cost if cost is not None else RESERVE[p['test']],
                        'usage_price_estimate_available':cost is not None,'ended_at':time.time()})
            if cost is None or cost>RESERVE[p['test']] or not result['text']:
                raise SystemExit('Usage/response unexpected; stopped for review')
            count+=1
            print(json.dumps({'completed_count':len(completed)+count,'test':p['test'],
                              'elapsed_seconds':round(elapsed,3),'estimated_cny':round(cost,8)}),flush=True)
        print(json.dumps({'state':'finished','new_submissions':count,'total_submissions':budget(ledger_events(out),0)[0]}),flush=True)


def score(root,out):
    sys.path.insert(0,str(root))
    from pilot.audio.attribution import repair_cached_audio
    import yaml
    data=(root/'workspace/tsxk').resolve()
    original=read(out/'plan.json')
    excluded=read(out/'reference-qc-exclusions.json') if (out/'reference-qc-exclusions.json').exists() else {}
    plan=[p for p in original if p['id'] not in excluded]
    if (out/'supplement-plan.json').exists():plan+=read(out/'supplement-plan.json')
    references=read(out/'references-v2.json') if (out/'references-v2.json').exists() else {}
    if (out/'references-supplement.json').exists():references.update(read(out/'references-supplement.json'))
    names=list(dict.fromkeys(yaml.safe_load((root/'pilot/semantic/rules_tsxk.yaml').read_text())['work']['characters'] + ['坐山客','星辰塔','混沌城主','宇宙尊者','宇宙之主','真神']))
    corrections=read(out/'ocr-manual-review.json') if (out/'ocr-manual-review.json').exists() else {}
    if (out/'supplement-manual-review.json').exists():corrections.update(read(out/'supplement-manual-review.json'))
    a_details,b_details,held=[],[],[]
    for p in plan:
        response=read(out/f"responses/{p['id']}.json")
        if p['test']=='A':
            ref=corrections.get(p['id'],{}).get('corrected_text',references.get(p['id'],p['reference'])['text'])
            counts={name:norm(ref).count(norm(name)) for name in names if norm(name) in norm(ref)}
            detail={'id':p['id'],'row':p['row'],'group':p['group'],'reference':ref,'sensevoice':p['sensevoice'],'cloud':response['text'],
                    'reference_names':counts}
            for model,text in [('sensevoice',p['sensevoice']),('cloud',response['text'])]:
                detail[model+'_similarity']=sim(ref,text)
                detail[model+'_names_correct']=sum(min(count,norm(text).count(norm(name))) for name,count in counts.items())
            detail['names_total']=sum(counts.values())
            a_details.append(detail)
        else:
            audio=read(data/f"audio/{p['row']}.json")
            repaired=repair_cached_audio(audio)
            try:
                t=response['text'].strip()
                t=re.sub(r'^```(?:json)?\s*|\s*```$','',t)
                obj=json.loads(t)
            except (ValueError,TypeError):
                obj={'kind':'无法判断','uploader_speech':None}
            expected=p['expected_uploader']
            baseline=bool(audio.get('uploader_speech'))
            fixed=bool(repaired.get('uploader_speech'))
            cloud=obj.get('uploader_speech') is expected
            if p['group']=='B-role-dialogue':
                cloud=cloud and obj.get('kind')=='角色对白'
            detail={'id':p['id'],'row':p['row'],'group':p['group'],'expected_uploader':expected,
                    'baseline_correct':baseline==expected,'repaired_correct':fixed==expected,'cloud_correct':cloud,
                    'baseline_uploader':baseline,'repaired_uploader':fixed,'cloud':obj}
            if p['split']=='holdout':
                held.append({k:detail[k] for k in ['baseline_correct','repaired_correct','cloud_correct']})
            else:
                b_details.append(detail)
    a_summary={}
    for group in ['A-narration','A-dialogue','all']:
        rows=[d for d in a_details if group=='all' or d['group']==group]
        vals={'total':len(rows),'names_total':sum(d['names_total'] for d in rows)}
        for model in ['sensevoice','cloud']:
            vals[model]={'median_similarity':statistics.median(d[model+'_similarity'] for d in rows),
                         'below_06':sum(d[model+'_similarity']<.6 for d in rows),
                         'names_correct':sum(d[model+'_names_correct'] for d in rows)}
        a_summary[group]=vals
    b_summary={}
    for group in ['B-known-narration','B-added-narration','B-no-uploader','B-role-dialogue']:
        rows=[d for d in b_details if d['group']==group]
        b_summary[group]={'development_total':len(rows), **{m:sum(d[m+'_correct'] for d in rows) for m in ['baseline','repaired','cloud']}}
    events=ledger_events(out);ends=[e for e in events if e['event']=='completed']
    summary={'A':a_summary,'B_development':b_summary,
             'B_holdout_aggregate':{'total':len(held),**{m:sum(d[m+'_correct'] for d in held) for m in ['baseline','repaired','cloud']}},
             'calls':len([e for e in events if e['event']=='submit_intent']), 'completed':len(ends),
             'elapsed_seconds_sum':sum(e['elapsed_seconds'] for e in ends),
             'estimated_cny':sum(e['estimated_cny'] for e in ends),
             'manual_reference_reviews':sum(p['id'] in corrections for p in plan if p['test']=='A'),
             'excluded_original_A':list(excluded),'original_submissions':len(original),
             'heldout_policy':'one workorder-fixed row only; no held-out error files or inspection; no tuning',
             'protected_unchanged':protected_hashes(data)==read(out/'protected-before.json')}
    write(out/'A-details.json',a_details); write(out/'B-development-details.json',b_details)
    write(out/'results-summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','validate','run','score'])
    parser.add_argument('--out',type=Path)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--supplement',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    out=(args.out or root/'workspace/audio-exp').resolve()
    if args.command=='prepare':prepare(root,out)
    elif args.command=='validate':print(json.dumps(validate(root,out),ensure_ascii=False))
    elif args.command=='run':run(root,out,args.limit,args.supplement)
    else:score(root,out)


if __name__=='__main__':
    main()
