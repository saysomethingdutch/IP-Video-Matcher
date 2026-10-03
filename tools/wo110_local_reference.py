"""Independent local OCR references for WO-110, no cloud imports or labels.

Full-resolution, upright player subtitle band; fixed filtering for every A clip.
Original cache sometimes chose 180-degree rotation based on watermark OCR noise.
Outputs are experiment-only; no production subtitle cache is changed.
"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parent))
from wo110_audio_experiment import norm, sim, write


def reference(root,out,p):
    folder=out/'local-ocr-v2'/p['id']
    folder.mkdir(parents=True,exist_ok=True)
    video=Path('/Volumes/Element/ip-video-matcher-field-test/evidence')/(p['row']+'.mp4')
    result=subprocess.run(['/opt/homebrew/bin/ffmpeg','-nostdin','-v','error','-y','-ss',str(p['start']),
                           '-i',str(video),'-t',str(p['end']-p['start']),'-vf','fps=1','-q:v','2',str(folder/'%02d.jpg')],capture_output=True,text=True)
    if result.returncode:
        raise ValueError('Local reference frame extraction failed: '+p['id'])
    timeline=json.loads((root/'workspace/tsxk/timeline'/p['row']/'timeline.json').read_text())
    x,y,w,h=timeline['player']['box']
    files=[]
    for file in sorted(folder.glob('[0-9][0-9].jpg')):
        im=Image.open(file)
        # box is x,y,width,height, not x0,y0,x1,y1.
        im=im.crop((x,y+int(h*.62),x+w,y+h))
        crop=file.with_name('crop-'+file.name)
        im.save(crop,quality=95)
        files.append(crop)
    proc=subprocess.run([str(root/'pilot/bin/ocr_vision'),*map(str,files)],capture_output=True,text=True)
    if proc.returncode or 'OCR failed' in proc.stderr:
        raise ValueError('Local Apple Vision OCR failed: '+p['id'])
    raw=[json.loads(line) for line in proc.stdout.splitlines() if line.startswith('{')]
    write(folder/'ocr-raw.json',raw)
    return assemble(out,p,raw,result.stderr.strip())


def assemble(out,p,raw,decode_messages=''):
    frames=[]
    for rec in raw:
        keep=[]
        for item in sorted(rec['items'],key=lambda x:(x['y'],x['x'])):
            text=item['text'].strip();centre=item['x']+item['w']/2
            if item.get('conf',0)<.3 or not norm(text):continue
            if not .20<=centre<=.80:continue
            if item['x']>.64 and len(norm(text))<=8:continue
            if item['h']<.045:continue
            if any(u in text.lower() for u in ['登录','注册','弹幕','bilibili','抖音','点赞','关注','收藏','投币','试看','发送']):continue
            if len(norm(text))<=6 and text.endswith('空'):continue
            keep.append(item)
        # Original captions are the lowest central line; higher text is commonly
        # uploader commentary or a player tooltip. No ASR text is used here.
        bottom=max((i['y'] for i in keep),default=0)
        text=' '.join(i['text'].strip() for i in keep if i['y']>=bottom-.06)
        frames.append({'t':p['start']+int(rec['frame'].split('-')[-1].split('.')[0])-1,'text':text})
    frames.sort(key=lambda f:f['t'])
    groups=[];current=[]
    for f in frames:
        text=f['text']
        if not norm(text):continue
        if current and sim(current[-1],text)<.65:
            groups.append(current);current=[]
        current.append(text)
    if current:groups.append(current)
    lines=[collections.Counter(g).most_common(1)[0][0] for g in groups]
    ref={'id':p['id'],'row':p['row'],'clip_sha256':p['sha256'],'text':''.join(lines),'lines':lines,'frames':frames,
         'coverage':sum(bool(norm(f['text'])) for f in frames)/max(len(frames),1),'changes':len(lines),
         'method':'fixed full-resolution upright player bottom 38%; lower central line, adjacent fuzzy dedup; no ASR or owner labels used',
         'original_video':str(Path('/Volumes/Element/ip-video-matcher-field-test/evidence')/(p['row']+'.mp4')),'frame_decode_messages':decode_messages}
    write(out/'local-ocr-v2'/p['id']/'reference.json',ref)
    return ref


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path);parser.add_argument('--out',type=Path)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];out=(args.out or root/'workspace/audio-exp').resolve()
    plan=json.loads((args.plan or out/'plan.json').read_text());samples=[p for p in plan if p['test']=='A']
    with ThreadPoolExecutor(max_workers=3) as ex:
        refs=list(ex.map(lambda p:reference(root,out,p),samples))
    write(out/'references-v2.json',{r['id']:r for r in refs})
    print(json.dumps({'local_references':len(refs),'quality_failures':[{'id':r['id'],'coverage':r['coverage'],'changes':r['changes'],'chars':len(norm(r['text']))} for r in refs if r['coverage']<.75 or r['changes']<4 or len(norm(r['text']))<60]},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
