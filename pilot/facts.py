"""Reusable trust boundary for cached visual facts and platform UI text."""
from copy import deepcopy
from difflib import SequenceMatcher
import re

PLATFORM_PATTERNS={
 'login':r'登录.{0,8}(?:高清|视频|享)|登录免费享|试看\s*\d+\s*秒|登录观看|扫码登录|短信登录',
 'poll':r'投票|参与投票|投票已结束|你支持哪|请选择.{0,12}(?:角色|选项)',
 'ending':r'重播|播放结束|相关推荐|取消连播|自动连播|即将播放',
}


def platform_kind(text):
    text=re.sub(r'\s','',str(text or ''))
    if re.search(r'你觉得.{0,20}好看吗',text) and '还行' in text:
        return 'poll'  # question plus separate platform choice labels
    return next((name for name,pattern in PLATFORM_PATTERNS.items() if re.search(pattern,text)),None)


def normalized(text):return re.sub(r'[^a-z0-9\u4e00-\u9fff]','',str(text).lower())


def watermark(text,uploader='',item=None):
    value=normalized(text);name=normalized(uploader)
    words=re.findall(r'[a-z]{5,}',str(text).lower())
    bili='哔哩哔哩' in value or any(SequenceMatcher(None,w,'bilibili').ratio()>=.7 for w in words)
    near_name=bool(len(name)>=3 and len(value)>=3 and
        (name in value or SequenceMatcher(None,value,name).ratio()>=.72))
    if not (bili or near_name):return False
    if item and all(k in item for k in ('x','y','w','h')):
        x=item['x']+item['w']/2;y=item['y']+item['h']/2
        return (y<.25 or y>.85) and (x<.5 or x>.75)
    return True  # old caches lack geometry; fuzzy identity is the only evidence


def visible_text(frame,uploader=''):
    uploader=uploader or frame.get('uploader_identity') or ''
    source=frame.get('ocr_items') or frame.get('ocr') or []
    out=[]
    for item in source:
        raw=item.get('text','') if isinstance(item,dict) else str(item)
        poll_label=frame.get('platform_ui')=='poll' and bool(re.fullmatch(r'好看|还行|不好看|你觉得.{0,24}好看吗[？?]?',raw.strip()))
        if not watermark(raw,uploader,item if isinstance(item,dict) else None) and not platform_kind(raw) and not poll_label:out.append(raw)
    return list(dict.fromkeys(out))


def description(frame,uploader=''):
    """Free prose is usable only if both providers independently said it.

    Shared class/form/face/text conclusions are kept even when prose differs.
    A one-provider character/story/game/AI assertion is never promoted to fact.
    """
    answers=(frame.get('cloud') or {}).get('answers') or {}
    a,b=answers.get('zhipu'),answers.get('deepseek')
    if not a or not b or a.get('content')!=b.get('content') or a.get('content')!=frame.get('content'):return ''
    aa,bb=str(a.get('why') or ''),str(b.get('why') or '')
    if aa and normalized(aa)==normalized(bb) and not re.search(r'水印',aa) and not watermark(aa,uploader):return aa
    labels={'tsxk':'官方动画画面','creator':'自制画面','unrelated':'其他画面','blank':'黑屏','tsxk_other':'其他形式画面'}
    parts=[labels.get(a.get('content'),'')]
    if a.get('form') and a.get('form')==b.get('form'):parts.append(a['form'])
    if a.get('face') is True and b.get('face') is True:parts.append('有人物出镜')
    if a.get('text') is True and b.get('text') is True:parts.append('有文字')
    return '；'.join(p for p in parts if p)


def writing_timeline(timeline,uploader=''):
    if not timeline:return timeline
    out=deepcopy(timeline)
    for f in out.get('frames',[]):
        if uploader:f['uploader_identity']=uploader
        kind=platform_kind(' '.join(f.get('ocr') or []))
        if kind:f['platform_ui']=kind
        f['ocr']=visible_text(f,uploader)
        if f.get('ocr_items'):
            f['ocr_items']=[item for item in f['ocr_items'] if item.get('text') in f['ocr']]
        if f.get('ignored'):continue
        agreed=description(f,uploader)
        for a in ((f.get('cloud') or {}).get('answers') or {}).values():
            if a:a['why']=agreed
    return out


def form_evidence(tl,audio):
    blob=' '.join(t for f in (tl or {}).get('frames',[]) if not f.get('ignored') for t in visible_text(f))
    blob+=' '+' '.join((audio or {}).get('uploader_speech') or [])
    blob+=' '+' '.join(c.get('text','') for c in (audio or {}).get('segments',[]) if c.get('speech_type')=='旁白讲解')
    forms=set()
    for f in (tl or {}).get('frames',[]):
        if f.get('ignored'):continue
        local=f.get('local') or {};a=(f.get('cloud') or {}).get('answers') or {}
        if local.get('form') in ('手游','漫画'):forms.add(local['form'])
        left,right=a.get('zhipu') or {},a.get('deepseek') or {}
        if left.get('form')==right.get('form') and left.get('form') in ('手游','漫画') and left.get('content')==right.get('content')=='tsxk_other':forms.add(left['form'])
    if re.search(r'原著|小说|书中|第.{1,8}章',blob):forms.add('小说')
    if re.search(r'有声书|有声小说|听书',blob):forms.add('有声书')
    if re.search(r'AI\s*(?:生成|创作|虚构)|人工智能生成',blob,re.I):forms.add('AI')
    return forms


FORM_WORDS={'手游':r'手游|游戏(?:画面|交互|界面|UI)','小说':r'原著|小说|书中','AI':r'AI\s*(?:生成|画面|创作|虚构)|人工智能生成'}


def supported_clause(text,support):
    return all(key in support or not re.search(pattern,text,re.I) for key,pattern in FORM_WORDS.items())


def clean_platform_timeline(tl):
    out=deepcopy(tl);changed=False
    for f in out.get('frames',[]):
        if f.get('ignored'):continue
        f.setdefault('source','缓存')
        words=' '.join(f.get('ocr') or [])
        kind=f.get('platform_ui') or platform_kind(words)
        if not kind:continue
        changed=True;f['platform_ui']=kind
        local=f.get('local') or {};f['local']=local
        # Filter the UI layer, not the content behind a login/trial strip or
        # poll. A real locally decoded link/QR can coexist with those overlays.
        tr=local.get('traffic') or {}
        if isinstance(tr,dict) and any(not platform_kind(p) for p in tr.get('evidence',[])):
            f['traffic']=tr.get('platforms',[]);f['kinds']=tr.get('kinds',[])
        else:f['traffic']=[];f['kinds']=[];local['traffic']=None
        a=(f.get('cloud') or {}).get('answers') or {}
        if kind=='ending':
            f.update(ignored='平台界面',content=None,form='',traffic=[],kinds=[],face=False,text=False)
        elif kind=='poll' and f.get('form')=='手游' and local.get('form')!='手游':
            f.update(content='unknown',form='')  # poll buttons are not a game UI
        f['ocr']=visible_text(f)
        for answer in a.values():
            if answer:answer['traffic']='none';answer['why']=''

    if changed:
        from pilot.timeline.owner_feedback import repair
        out.pop('owner_feedback_version',None);out=repair(out)
        out['traffic']=[s for s in out.get('traffic',[]) if any(
            not f.get('ignored') and f.get('traffic') and not f.get('platform_ui') and s['start']<=f['t']<=s['end']
            for f in out.get('frames',[]))]
        from pilot.timeline.segments import ac_lines,summary
        out['summary']=summary([f for f in out['frames'] if not f.get('ignored')],out['segments'])
        out['ac']=ac_lines(out['segments'],out['traffic'])
    return out
