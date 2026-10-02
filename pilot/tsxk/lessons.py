"""WO-108 deterministic cache replays; all candidate files are staged separately."""
from copy import deepcopy
import json
from pathlib import Path
import re

from pilot.audio.attribution import repair_cached_audio
from .ac import assemble
from .ac_filter import parse
from .config import TsxkCase
from .pipeline import save
from .regress import freeze, evaluate

from .sound import audio_sections


def stage(case,number,transform):
    root=case.case_dir/'wo108';previous=case.case_dir/'rows' if number==1 else root/'stages'/f'{number-1:02d}'/'rows'
    dest=root/'stages'/f'{number:02d}'/'rows'
    for rid in case.rows:
        row=json.loads((previous/f'{rid}.json').read_text())
        audio=json.loads((case.case_dir/'audio'/f'{rid}.json').read_text())
        timeline=json.loads((case.case_dir/'timeline'/rid/'timeline.json').read_text())
        row=transform(row,audio,timeline,rid,case)
        row['lessons_version']=f'WO-108-{number}';save(row,dest/f'{rid}.json')
    groups,snapshot=freeze(case)
    result=evaluate(dest,snapshot['answers'],groups)
    save(result,root/'regression'/f'step{number}.json')
    print(json.dumps({'step':number,'sets':result['sets']},ensure_ascii=False))
    return result


def audio_fix(row,audio,tl,rid,case):
    repaired=repair_cached_audio(audio)
    save(repaired,case.case_dir/'wo108/audio'/f'{rid}.json')
    original=parse(row['fields']['AC']['value'])
    sections=audio_sections(original,repaired,row['fields']['Y']['value'])
    if sections!=original:row['fields']['AC']['value']=assemble(sections,[])
    row.setdefault('sound',{})['uploader_speech']=len(repaired.get('uploader_speech') or [])
    row['speech_evidence']=repaired.get('uploader_speech_evidence',[])
    return row




def watermark_fix(row,audio,tl,rid,case):
    sections=parse(row['fields']['AC']['value']);old=deepcopy(sections)
    for k in ('主题概括','段落情节','比对意见'):
        from .ac_filter import CLAUSES
        sections[k]=''.join(s for s in CLAUSES.findall(sections[k]) if not re.search(r'水印|变体.{0,12}字样|规避(?:平台)?识别',s))
    sections['新增创作要素']=[s for s in sections['新增创作要素'] if not re.search(r'水印|变体.{0,12}字样|规避(?:平台)?识别',s)]
    if old!=sections:row['fields']['AC']['value']=assemble(sections,[])
    from pilot.facts import writing_timeline
    ledger=case.webledger(rid) or {}
    save(writing_timeline(tl,ledger.get('uploader_text') or ''),case.case_dir/'wo108/timeline'/f'{rid}.json')
    return row


def trust_fix(row,audio,tl,rid,case):
    from pilot.facts import form_evidence,supported_clause
    from .ac_filter import CLAUSES
    repaired=json.loads((case.case_dir/'wo108/audio'/f'{rid}.json').read_text())
    safe_tl=json.loads((case.case_dir/'wo108/timeline'/f'{rid}.json').read_text())
    support=form_evidence(safe_tl,repaired)
    sections=parse(row['fields']['AC']['value']);old=deepcopy(sections)
    for k in ('主题概括','段落情节','比对意见'):
        chunks=CLAUSES.findall(sections[k]);kept=[s for s in chunks if supported_clause(s,support)]
        sections[k]=''.join(kept)
        if kept!=chunks:sections[k]=re.sub(r'[，,；;]+$','。',sections[k])
    sections['新增创作要素']=[s for s in sections['新增创作要素'] if supported_clause(s,support)]
    if old!=sections:row['fields']['AC']['value']=assemble(sections,[])
    value=str(row['fields']['T']['value'] or '');forms=[f for f in ('漫画','手游','小说','有声书') if f in value and f in support]
    row['fields']['T']['value']='是（'+'、'.join(forms)+'）' if forms else '否'
    row['facts_support']=sorted(support)
    return row


def platform_fix(row,audio,tl,rid,case):
    from pilot.facts import clean_platform_timeline
    from .fields import timeline_fields,time_lines,page_fields,episode_note
    source=json.loads((case.case_dir/'wo108/timeline'/f'{rid}.json').read_text())
    clean=clean_platform_timeline(source);save(clean,case.case_dir/'wo108/timeline'/f'{rid}.json')
    if clean==source:return row
    led=case.webledger(rid) or {};rules=case.rules()
    fields,_,_=timeline_fields(clean,led,str(row['fields']['D']['value']),rules)
    row['fields']['X']=fields['X'];row['fields']['W']=fields['W']
    sections=parse(row['fields']['AC']['value']);sections['其他']=[]
    _,notes,_=page_fields(led,rules)
    lines=time_lines(clean,row['fields'],row['fields']['Y']['value'])+notes
    if (note:=episode_note(led.get('title'))):lines.append(note)
    row['fields']['AC']['value']=assemble(sections,lines);row['ac_code_lines']=lines
    return row


def z_sync_fix(row,audio,tl,rid,case):
    from .ac_filter import sync_z_opinion
    z=row['fields']['Z']['value']
    if z in ('高','中','低','不涉及'):row['fields']['AC']['value']=sync_z_opinion(row['fields']['AC']['value'],z)
    return row


def program_lines_fix(row,audio,tl,rid,case):
    from .ac_filter import CODE_OTHER
    sections=parse(row['fields']['AC']['value'])
    row['program_other_lines']=[s for s in sections['其他'] if CODE_OTHER.match(s)]
    return row


def forbidden_fix(row,audio,tl,rid,case):
    from .ac_filter import filter_ac
    sections=parse(row['fields']['AC']['value']);code=sections['其他']
    cleaned,removed=filter_ac(sections,row['fields'],tl)
    # Staging only removes forbidden subclauses. Program/owner Other separation
    # is recorded; no cleanup of formal approved text is executed here.
    from .ac_filter import BANNED
    code=[s for s in code if not BANNED.search(s)]
    row['fields']['AC']['value']=assemble(cleaned,code)
    row['lessons_removed']=removed
    return row


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',default='cases/tsxk.yaml');p.add_argument('--through',type=int,choices=range(1,8),default=7)
    args=p.parse_args();case=TsxkCase(args.case)
    for number,transform in enumerate((audio_fix,watermark_fix,trust_fix,platform_fix,z_sync_fix,program_lines_fix,forbidden_fix),1):
        if number>args.through:break
        stage(case,number,transform)


if __name__=='__main__':main()
