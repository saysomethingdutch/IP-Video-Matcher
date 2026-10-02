"""Case-independent recording-clock evidence cards for review fields."""
from collections import defaultdict
import math
import re

DEFAULTS={'Q':'否','R':'是','S':'无','T':'否','U':'否','V':'否','W':'否','X':'否',
          'Y':'二创（单IP混剪类）','Z':'低','AA':'无法判断','AB':'无'}
FORM={'小说':r'原著|小说|书中|第.{1,8}章','有声书':r'有声书|听书|有声小说'}


def attach_evidence(fields, timeline, audio, ledger, *, thumbnail=None, defaults=None):
    from pilot.audio.attribution import repair_cached_audio
    from pilot.facts import form_evidence,visible_text,platform_kind,clean_platform_timeline
    from pilot.tsxk.sound import music_evidence
    audio=repair_cached_audio(audio) or {};defaults={**DEFAULTS,**(defaults or {})}
    timeline=clean_platform_timeline(timeline) if timeline else {}
    frames=[f for f in timeline.get('frames',[]) if not f.get('ignored')]
    cards=defaultdict(list)
    def add(col,t,quote,kind='原话',end=None,form=None):
        if t is None or not isinstance(t,(int,float)) or not math.isfinite(t) or t<0:return
        if not quote:return
        image=thumbnail(t) if thumbnail else None
        card={'t':t,'end':end,'quote':str(quote),'kind':kind,'thumbnail':image}
        if form:card['form']=form
        if card not in cards[col]:cards[col].append(card)
    for f in frames:
        words=visible_text(f,(ledger or {}).get('uploader_text') or '')
        for text in words:
            for form,pattern in FORM.items():
                if re.search(pattern,text):add('T',f['t'],text,form=form)
        if f.get('content')=='tsxk_other' and f.get('form') in form_evidence({'frames':[f]},None):
            add('T',f['t'],f.get('form'),'两家一致/本机确认',form=f['form'])
            add('W',f['t'],f['form'],'其他形式画面')
        if f.get('content') in ('unrelated','creator','traffic') or f.get('face') or f.get('ai_note'):
            quote=' / '.join(words[:3]) or f.get('content')
            if quote:add('W',f['t'],quote,'画面依据')
        if f.get('traffic') and not platform_kind(' '.join(f.get('ocr') or [])):
            proof=(f.get('local') or {}).get('evidence') or words
            for word in proof:add('X',f['t'],word,'引流文字/二维码')
    for c in audio.get('segments',[]):
        if c.get('speech_type')=='旁白讲解':
            for form,pattern in FORM.items():
                if re.search(pattern,c.get('text','')):add('T',c.get('recording_start'),c['text'],'语音转写',c.get('recording_end'),form=form)
    for c in audio.get('uploader_speech_evidence',[]):add('U',c['start'],c['quote'],c['source'],c['end'])
    for s in (timeline or {}).get('segments',[]):
        if s.get('content')=='unrelated':add('Y',s['start'],'连续无关内容区间','时间线（偷渡依据）',s['end'])
    music=music_evidence(audio,timeline)
    if music['verdict']=='是':
        for key in ('subtitle_without_speech_spans','lyric_subtitle_spans','music_only_spans'):
            for a,b in (audio.get('music_facts') or {}).get(key,[])[:3]:add('V',a,'；'.join(music['evidence']),'本机配乐旁证',b)
        for tag in (audio.get('music_facts') or {}).get('bilibili_music_tags',[]):
            if tag.get('recording_span'):add('V',tag['recording_span'][0],tag.get('text'),'页面音乐标签',tag['recording_span'][1])
    for t,p in (ledger or {}).get('progress') or []:add('R',t,f'细进度条 {p:.1%}','本机测量')
    # Simple descriptive fields can point to a recorded page/canvas observation.
    for field in fields:
        col,value=field['column'],str(field['value'] or '')
        entries=cards[col]
        missing_form=False
        if col=='T' and value.startswith('是'):
            requested={form for form in ('漫画','手游','小说','有声书') if form in value}
            entries=[c for c in entries if c.get('form') in requested]
            missing_form=bool(requested-{c.get('form') for c in entries})
        if col=='Y' and value!='偷渡' or col in ('Z','AC'):
            entries=list(cards['U']+cards['W']+cards['T'])
            if not entries and frames:
                f=next((f for f in frames if f.get('content')=='tsxk'),frames[0]);add(col,f['t'],'已采纳画面','画面依据');entries=cards[col]
        if col in ('D','F') and value in ((ledger or {}).get('title'),(ledger or {}).get('address')):
            add(col,(ledger or {}).get('play_start'),value,'网页台账');entries=cards[col]
        if col in ('S','AB'):
            for f in frames:
                for text in visible_text(f):
                    if len(text)>=3 and text in value:add(col,f['t'],text)
            entries=cards[col]
        missing_form=False
        if col=='T' and value.startswith('是'):
            requested={form for form in ('漫画','手游','小说','有声书') if form in value}
            entries=[c for c in entries if c.get('form') in requested]
            missing_form=bool(requested-{c.get('form') for c in entries})
        required=value not in ('','无') and col in defaults and value!=defaults[col]
        if col=='Y' and value=='偷渡':entries=[c for c in entries if c['kind']=='时间线（偷渡依据）']
        if col=='R' and len(entries)>6:entries=entries[:3]+entries[-3:]
        field['support']=entries[:12]
        field['evidence_missing']=bool(required and (not entries or missing_form))
        if field['evidence_missing']:
            field['flag_level']='red'
            if not any('非默认值缺少时间点' in r for r in field['flag_reasons']):field['flag_reasons'].append('非默认值缺少时间点和原话/画面依据')
    return fields
