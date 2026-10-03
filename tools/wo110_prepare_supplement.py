"""Replace four A references failing the original independent OCR quality gates.

Random seed unchanged; candidate order and every offline rejection are retained.
No cloud response is opened here. Original manifest and its hashes stay frozen.
"""
import csv
import json
from pathlib import Path
import random
import subprocess
import sys
import time
import wave

sys.path.insert(0,str(Path(__file__).resolve().parent))
import wo110_audio_experiment as exp
import wo110_local_reference as local


def main():
    root=Path(__file__).resolve().parents[1];out=(root/'workspace/audio-exp').resolve();data=(root/'workspace/tsxk').resolve()
    if (out/'supplement-plan.json').exists():raise ValueError('Supplement already frozen')
    original=exp.read(out/'plan.json');sel=exp.read(out/'selection.json')
    selected={p['row'] for p in original if p['group']=='A-dialogue'}
    candidates=sorted(set(sel['eligibility']['A-dialogue'])-selected)
    rng=random.Random(20261003);rng.shuffle(candidates)
    plan=[];refs={};audit=[]
    for i,row in enumerate(candidates):
        audio,l,frames=exp.load_row(data,row)
        ww=exp.windows(audio,l,frames,'角色台词',True)
        w=rng.choice(ww)
        p={'test':'A','group':'A-dialogue','row':row,'split':'development',**w,'id':f'A-extra-{i+1:03d}-{row}','model':exp.MODELS['A']}
        src=data/f'audio_tracks/evidence/{row}.m4a';clip=out/'clips'/f"{p['id']}.wav"
        proc=subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-y','-ss',str(p['start']),'-i',str(src),
                             '-t',str(p['end']-p['start']),'-ar','16000','-ac','1','-c:a','pcm_s16le',str(clip)],capture_output=True,text=True)
        if proc.returncode or proc.stderr:
            audit.append({'id':p['id'],'source_decode_error':proc.stderr,'selected':False});continue
        with wave.open(str(clip)) as f:duration=f.getnframes()/f.getframerate()
        p.update(source=str(src),source_sha256=exp.sha(src),ledger_sha256=exp.sha(data/f'webledger/{row}.json'),bv=l['bv'],address=l['address'],
                 content_start=l['content_start'],content_end=l['content_end'],clip=str(clip),sha256=exp.sha(clip),duration=duration,bytes=clip.stat().st_size)
        r=local.reference(root,out,p)
        good=r['coverage']>=.75 and r['changes']>=4 and len(exp.norm(r['text']))>=60
        audit.append({'id':p['id'],'row':row,'start':p['start'],'end':p['end'],'coverage':r['coverage'],'changes':r['changes'],
                      'characters':len(exp.norm(r['text'])),'selected':good})
        if good:
            p['reference']=r;plan.append(p);refs[p['id']]=r
        if len(plan)==4:break
    exp.write(out/'supplement-selection-audit.json',{'seed':20261003,'candidate_order':candidates,'audit':audit,
               'method':'Remaining original A-dialogue eligible development rows, shuffled seed; random original candidate window; same 75% coverage/4 changes/60 chars checks from independently regenerated OCR; no response files or accuracy used',
               'selected':len(plan),'created_at':time.time()})
    if len(plan)!=4:raise ValueError('Not enough independent eligible reference windows')
    exp.write(out/'supplement-plan.json',plan);exp.write(out/'references-supplement.json',refs)
    with (out/'upload-manifest.csv').open() as f:fields=next(csv.reader(f))
    with (out/'supplement-upload-manifest.csv').open('w',newline='') as f:
        wr=csv.DictWriter(f,fields);wr.writeheader();wr.writerows({k:p[k] for k in fields} for p in plan)
    with (out/'final-upload-manifest.csv').open('w',newline='') as f:
        wr=csv.DictWriter(f,fields);wr.writeheader();wr.writerows({k:p[k] for k in fields} for p in original+plan)
    excluded=exp.read(out/'reference-qc-exclusions.json')
    exp.write(out/'scoring-population.json',{'A_ids':[p['id'] for p in original if p['test']=='A' and p['id'] not in excluded]+[p['id'] for p in plan],
                                          'B_ids':[p['id'] for p in original if p['test']=='B'],'excluded':excluded,'frozen_at':time.time()})
    print(json.dumps({'supplement_ready':len(plan),'additional_reservation_cny':.08,'total_submissions_after_supplement':83,
                      'sample_ids':[p['id'] for p in plan],'candidate_rejections':len(audit)-4},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
