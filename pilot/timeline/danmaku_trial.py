"""Local half-second motion experiment, deliberately outside the pipeline."""
from difflib import SequenceMatcher
import json
from pathlib import Path
import re

import cv2
from pilot.ledger.pipeline import compile_ocr,parallel_ocr
from pilot.timeline.frames import grab
from pilot.tsxk.pipeline import save
from pilot.tsxk.config import TsxkCase


def match_motion(before,after):
    found=[];used=set()
    for left in before:
        text=re.sub(r'\s','',left['text'])
        if len(text)<4:continue
        candidates=[]
        for j,right in enumerate(after):
            if j in used or abs(left['y']-right['y'])>.025:continue
            score=SequenceMatcher(None,text,re.sub(r'\s','',right['text'])).ratio()
            if score<.8 or not .75<=right['w']/max(left['w'],.001)<=1.33:continue
            dx=right['x']-left['x']
            if .025<=abs(dx)<=.25:candidates.append((score,j,right,dx))
        if candidates:
            score,j,right,dx=max(candidates,key=lambda x:x[0]);used.add(j)
            found.append({'text':left['text'],'next_text':right['text'],'dx':round(dx,4),
                          'y':left['y'],'similarity':score,'before':left,'after':right,
                          'caption_band':left['y']>.68})
    return found


def run():
    case=TsxkCase('cases/tsxk.yaml');root=case.case_dir/'wo108/danmaku';root.mkdir(parents=True,exist_ok=True)
    ids=[line.split('\t')[0] for line in (case.case_dir/'keep_videos.txt').read_text().splitlines() if '\t' in line and '弹幕' in line]
    if len(ids)!=6:raise ValueError('expected six fixed teaching cases')
    compile_ocr();results=[]
    for rid in ids:
        video=case.evidence(rid);tl=json.loads((case.case_dir/'timeline'/rid/'timeline.json').read_text())
        if not video.exists():raise FileNotFoundError(f'preserved teaching video missing: {rid}')
        dest=root/rid;dest.mkdir(exist_ok=True);pairs=[]
        for f in tl['frames']:
            t=f['t']
            if f.get('ignored') or t+.5>tl['content_span'][1]:continue
            paths=[dest/f'{t:.2f}-{n}.jpg' for n in range(2)]
            for n,path in enumerate(paths):
                if not path.exists():
                    img=grab(video,t+n*.5,box=tl['player']['box'])
                    if img is None:raise RuntimeError(f'cannot decode {rid} at {t}')
                    cv2.imwrite(str(path),img)
            pairs.append({'t':t,'paths':paths})
        cache=dest/'ocr.json'
        if cache.exists():records=json.loads(cache.read_text())
        else:
            records=parallel_ocr([str(p) for pair in pairs for p in pair['paths']]);save(records,cache)
        by_name={r['frame']:r['items'] for r in records};detections=[]
        for pair in pairs:
            a,b=(str(p) for p in pair['paths'])
            for detection in match_motion(by_name[Path(a).name],by_name[Path(b).name]):
                detections.append({'t':pair['t'],**detection,'images':[a,b]})
        unique={re.sub(r'\s','',d['text']):d for d in detections}
        result={'row':rid,'pairs':len(pairs),'moving_detections':len(detections),'unique_texts':len(unique),
                'caption_band_candidates':sum(d['caption_band'] for d in unique.values()),'detections':detections}
        save(result,dest/'result.json');results.append({k:v for k,v in result.items() if k!='detections'})
        print(result['row'],result['pairs'],result['unique_texts'],flush=True)
    save({'offset_seconds':.5,'not_connected_to_pipeline':True,'rows':results},root/'summary.json')


if __name__=='__main__':run()
