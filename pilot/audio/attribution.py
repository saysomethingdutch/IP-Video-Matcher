"""Text-only, conservative attribution from cached sentence functions and captions.

Caption agreement proves spoken words, not their owner. A continuing explanation
may be the uploader's dubbing even when its words match the subtitles. Isolated
calls, character dialogue and uncorroborated ASR fragments remain candidates.
"""
from copy import deepcopy
import re

VERSION='narration-caption-v1'
EXPLANATION=re.compile(r'为什么|为何|究竟|这是因为|原因|意味着|可以看出|其实|首先|其次|最后|第一|第二|第三|也就是说|不过|但是|所以|因为|由此|相比|解释|分析|动机|转折|我觉得|我们来|计划')


def explanation_clause(c):
    if c.get('speech_type')!='旁白讲解' or len(re.findall(r'[\u4e00-\u9fff]',c.get('text',''))) < 6:
        return False
    # A cached role alone is not an attribution. Require the transcript to be
    # confirmed by captions, or an explicit explanatory language function.
    return float(c.get('caption_similarity') or 0)>=.78 or bool(EXPLANATION.search(c['text'])) or bool(re.search(r'关注|投币|硬币|喜欢的话|支持一下',c['text']))


def continuous(clauses, gap=10, minimum=10):
    groups=[]
    for c in sorted(clauses,key=lambda c:c['recording_start']):
        if groups and c['recording_start']-groups[-1][-1]['recording_end']<=gap:groups[-1].append(c)
        else:groups.append([c])
    return [c for g in groups if sum(c['recording_end']-c['recording_start'] for c in g)>=minimum
            and any(EXPLANATION.search(c.get('text','')) for c in g) for c in g]


def repair_cached_audio(audio):
    """Return new facts; never mutate the cached analysis or call ASR/GLM."""
    if not audio or audio.get('status')!='ok':return deepcopy(audio)
    from .tsxk_r1 import speech_selection
    out=deepcopy(audio);clauses=out.get('segments') or []
    speech,promotions,seconds,candidates=speech_selection(clauses,out.get('played_seconds') or 0)
    for c in clauses:
        if c in speech:c['kind']='uploader'
        elif c in promotions:c['kind']='promotion'
        elif c.get('speech_type')=='角色台词' and float(c.get('caption_similarity') or 0)>=.78:c['kind']='source'
        elif c.get('kind') in ('uploader','source'):c['kind']='uncertain'
    out.update(uploader_speech=[c['text'] for c in speech],uploader_speech_seconds=seconds,
        uploader_candidate_seconds=candidates,uploader_candidate_ratio=round(candidates/(out.get('played_seconds') or 1),4),
        promotional_speech=[{k:c[k] for k in ('text','recording_start','recording_end')} for c in promotions],
        speech_policy_version=VERSION,
        uploader_speech_evidence=[{'start':c['recording_start'],'end':c['recording_end'],'quote':c['text'],
          'source':'语句功能+字幕核对' if float(c.get('caption_similarity') or 0)>=.78 else '语句功能+连续讲述'} for c in speech])
    return out
