"""WO-108 behavior tests; owner records, workbook and cloud remain untouched."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pilot.facts import description,visible_text,watermark,form_evidence,clean_platform_timeline,supported_clause
from pilot.audio.attribution import repair_cached_audio
from pilot.review.batch import preview,apply,read_list
from pilot.review.evidence import attach_evidence
from pilot.review.test_tsxk_review import fixture
from pilot.review.tsxk import TsxkReviewStore
from pilot.tsxk.ac import assemble
from pilot.tsxk.ac_filter import CODE_OTHER,sync_z_opinion,BANNED,filter_ac,parse
from pilot.tsxk.config import TsxkCase
from pilot.tsxk.pipeline import save,process_row
from pilot.tsxk.regress import freeze,evaluate,COLUMNS
from pilot.tsxk.tests.test_tsxk import timeline,seg,RULES,R3RulesTest
from pilot.timeline.danmaku_trial import match_motion


def test_attribution_matching_narration_is_not_source_dialogue():
    lines=[{'text':s,'recording_start':i*4,'recording_end':i*4+4,'speech_type':'旁白讲解','caption_similarity':1,'kind':'source'}
      for i,s in enumerate(['为何他会选择这条道路','这其实是另一个计划','所以他必须继续修炼'])]
    original={'status':'ok','segments':lines,'played_seconds':100}
    out=repair_cached_audio(original)
    assert out['uploader_speech_seconds']==12 and all(c['kind']=='uploader' for c in out['segments'])
    assert all(c['kind']=='source' for c in original['segments'])
    for c in lines:c['speech_type']='角色台词'
    assert not repair_cached_audio(original)['uploader_speech']
    from pilot.tsxk.sound import audio_sections
    parts={'主题概括':'','段落情节':'','比对意见':'','新增创作要素':['添加字幕，新增口播配音。'],'其他':[]}
    assert audio_sections(parts,{'uploader_speech':[]},'')==parts  # non-detection is not proof of absence
    parts['新增创作要素']=['添加字幕，未检出UP主口播。']
    assert audio_sections(parts,{'uploader_speech':['为何这样']},'')['新增创作要素']==['添加字幕。']
    parts['新增创作要素']=['添加字幕']
    assert audio_sections(parts,{'uploader_speech':['为何这样']},'二创（解说类）')['新增创作要素']==['添加字幕']


def test_watermark_is_filtered_but_actual_title_and_AI_declaration_remain():
    frame={'ocr_items':[{'text':'测试UP bilibili','x':.05,'y':.05,'w':.25,'h':.04},
         {'text':'AI虚构画面','x':.3,'y':.05,'w':.3,'h':.05},
         {'text':'测试UP','x':.3,'y':.7,'w':.2,'h':.04}]}
    assert visible_text(frame,'测试UP')==['AI虚构画面','测试UP']
    assert watermark('blibli') and watermark('测试UP','测试UP')
    from pilot.facts import writing_timeline
    copy=writing_timeline({'frames':[frame]},'测试UP')
    assert len(copy['frames'][0]['ocr_items'])==2 and len(frame['ocr_items'])==3


def test_one_provider_prose_is_not_a_fact_even_with_same_content_label():
    a={'content':'tsxk','form':'','text':True,'face':False,'why':'手游交互UI'}
    b={**a,'why':'动画角色交战'}
    f={'content':'tsxk','cloud':{'answers':{'zhipu':a,'deepseek':b}}}
    assert '手游' not in description(f) and '角色交战' not in description(f)
    assert not form_evidence({'frames':[f]},None)
    a['why']=b['why']='角上UP名 bilibili水印与原片画面'
    assert '水印' not in description(f,'UP名')
    assert supported_clause('以原作情节为基础的讲解',set())
    assert form_evidence({'frames':[f]},{'segments':[{'speech_type':'旁白讲解','text':'原著里这一段不同'}]})=={'小说'}


def test_platform_words_are_not_traffic_or_game_facts():
    from pilot.facts import writing_timeline,platform_kind
    from pilot.timeline.local import Screen,load_rules
    screen=Screen(load_rules())
    assert screen.traffic([{'text':'登录免费享高清视频 试看30秒'}],[]) is None
    tl=timeline([seg(0,20,'tsxk')]);tl['frames']=[{'t':5,'content':'tsxk','ocr':['参与投票'],'traffic':['other'],
        'local':{'content':'tsxk'},'form':'','face':False,'text':True,'kinds':[]}]
    out=clean_platform_timeline(tl)
    assert out['frames'][0]['content']=='tsxk' and not out['frames'][0]['traffic']
    assert tl['frames'][0]['traffic']==['other']
    login=timeline([seg(0,20,'tsxk')]);login['frames']=[{'t':5,'content':'tsxk','ocr':['登录免费享高清视频','试看30秒'],
        'traffic':['other'],'local':{'content':'tsxk'},'form':'','face':False,'text':False,'kinds':[]}]
    safe=writing_timeline(login)
    assert not safe['frames'][0]['ocr']
    assert not clean_platform_timeline(safe)['traffic']
    assert not clean_platform_timeline(safe)['frames'][0]['traffic']
    assert platform_kind('你觉得吞噬星空好看吗？ 好看 还行')=='poll'
    creator=timeline([seg(0,20,'creator')]);creator['frames']=[{'t':5,'content':'creator','source':'云端',
        'ocr':['登录免费享高清视频','UP自制片头'],'traffic':[],'local':{},'face':False,'text':True,'form':''}]
    kept=clean_platform_timeline(creator)
    assert kept['frames'][0]['content']=='creator' and not kept['frames'][0].get('ignored')


def test_program_lines_and_owner_written_cleanup_are_safe(tmp_path):
    assert CODE_OTHER.match('02:20-02:40为引流画面')
    assert CODE_OTHER.match('00:20–00:40为UP主自制画面')
    _,config,data=fixture(tmp_path);store=TsxkReviewStore(TsxkCase(config),decisions_path=tmp_path/'review.jsonl')
    store.decide('7813','modify',{'AC':assemble({'主题概括':'角色交战。','比对意见':'片段使用。'},['所有者亲手写的事实。'])},actor='Owner')
    before=store.decisions_path.read_bytes();proposal=store.cleanup_preview()
    assert not any('所有者亲手写的事实。' in r['removed'] for r in proposal['rows'])
    assert store.decisions_path.read_bytes()==before


def test_Z_sync_preserves_same_grade_synonyms_and_changes_actual_mismatch():
    text=assemble({'比对意见':'合理使用可能性较低。'},[])
    assert sync_z_opinion(text,'低')==text
    assert '合理使用可能性为中等' in sync_z_opinion(text,'中')


def test_pipeline_code_Z_downgrade_synchronizes_opinion():
    from pilot.tsxk import classify as K
    data=R3RulesTest().reply(Y='纯cut',Z='高')
    data['AC']['比对意见']='原片表达，合理使用可能性较高。'
    res={'data':data,'errors':[],'calls':[],'prompt':'','speech':0,'music':{'verdict':'否','evidence':[],'series_music_seconds':0}}
    with patch.object(K,'classify',return_value=res):row=process_row({'C':'标题'},None,timeline([seg(0,100,'tsxk')]),RULES,'s')
    assert row['fields']['Z']['value']=='低'
    assert '合理使用可能性为低' in row['fields']['AC']['value']


def test_nondefault_T_has_timed_quote_and_missing_X_is_red():
    fields=[{'column':c,'value':v,'flag_reasons':[]} for c,v in [('T','是（小说）'),('X','是（其他）')]]
    audio={'status':'ok','segments':[{'text':'原著里他选择另一条路','speech_type':'旁白讲解','recording_start':42,'recording_end':46,'caption_similarity':1}], 'played_seconds':100}
    attach_evidence(fields,{'frames':[],'segments':[]},audio,{})
    assert fields[0]['support'][0]['t']==42 and '原著' in fields[0]['support'][0]['quote']
    assert not fields[0]['evidence_missing'] and fields[1]['evidence_missing'] and fields[1]['flag_level']=='red'
    combined=[{'column':'T','value':'是（手游、小说）','flag_reasons':[]}]
    attach_evidence(combined,{'frames':[],'segments':[]},audio,{})
    assert combined[0]['evidence_missing']  # novel quote cannot prove the game part



def test_voice_music_traffic_other_and_smuggling_have_recording_clock_evidence():
    audio={'status':'ok','played_seconds':100,'segments':[
        {'text':text,'recording_start':30+i*4,'recording_end':34+i*4,'speech_type':'旁白讲解','caption_similarity':1}
        for i,text in enumerate(['为何他会选择这条道路','这其实是另一个计划','所以他必须继续修炼'])],
        'music_facts':{'music_only_spans':[[10,16]],'bilibili_music_tags':[]}}
    tl={'frames':[{'t':10,'content':'creator','ocr':['QQ群123456'],'traffic':['tencent'],
                   'local':{'evidence':['QQ群123456']}},
                  {'t':20,'content':'unrelated','ocr':[]}],
        'segments':[{'start':10,'end':19,'content':'creator'}, {'start':20,'end':29,'content':'unrelated'}]}
    fields=[{'column':col,'value':value,'flag_reasons':[]} for col,value in
            [('U','是'),('V','是'),('W','是'),('X','是（腾讯）'),('Y','偷渡')]]
    attach_evidence(fields,tl,audio,{},thumbnail=lambda t:f'/thumb/{t}.jpg')
    assert all(f['support'] and not f['evidence_missing'] for f in fields)
    assert fields[0]['support'][0]['t']==30
    assert fields[-1]['support'][0]['t']==20 and fields[-1]['support'][0]['end']==29
    assert fields[3]['support'][0]['quote']=='QQ群123456'
    assert all(c['thumbnail'] for f in fields for c in f['support'])

def test_delegated_batch_requires_confirmation_skips_stale_old_and_logs_reason(tmp_path):
    _,config,data=fixture(tmp_path);store=TsxkReviewStore(TsxkCase(config),decisions_path=tmp_path/'review.jsonl')
    items=[{'row':'7813','column':'U','old':'否','new':'是','reason':'所有者确认有讲述'},
           {'row':'7814','column':'U','old':'是','new':'否','reason':'原片对白'}]
    proposal=preview(store,items)
    assert proposal['matched']==1 and not store.decisions_path.exists()
    with pytest.raises(ValueError):apply(store,items,delegate='协助者',preview_token=proposal['token'])
    result=apply(store,items,delegate='协助者',confirmed=True,preview_token=proposal['token'])
    assert result['applied']==1 and len(result['skipped'])==1
    event=json.loads(store.decisions_path.read_text())
    assert event['who']=='Owner 代办' and event['delegate']=='协助者' and event['reason']==items[0]['reason']
    again=apply(store,items,delegate='协助者',confirmed=True,preview_token=proposal['token'])
    assert again['applied']==0


def test_batch_observes_other_process_changes_under_file_lock(tmp_path):
    _,config,data=fixture(tmp_path);path=tmp_path/'review.jsonl';store=TsxkReviewStore(TsxkCase(config),decisions_path=path)
    items=[{'row':'7813','column':'U','old':'否','new':'是','reason':'已确认'}];p=preview(store,items)
    other=TsxkReviewStore(TsxkCase(config),decisions_path=path);other.decide('7813','modify',{'U':'是'},actor='Owner')
    before=path.read_bytes()
    assert apply(store,items,delegate='协助者',confirmed=True,preview_token=p['token'])['applied']==0
    assert path.read_bytes()==before


def test_regression_final_delegated_event_wins_and_holdout_errors_are_hidden(tmp_path):
    ids=[str(n) for n in range(1,401)];case=SimpleNamespace(rows=ids,case_dir=tmp_path)
    record=tmp_path/'review/decisions.jsonl';record.parent.mkdir();rows=tmp_path/'machine';rows.mkdir()
    events=[]
    for rid in ids:
        events += [{'row':rid,'field':c,'new':'否'} for c in COLUMNS]
        save({'fields':{c:{'value':'否'} for c in COLUMNS}},rows/f'{rid}.json')
    events.append({'row':'1','field':'U','new':'是','who':'Owner 代办'})
    record.write_text('\n'.join(json.dumps(e) for e in events))
    groups,snapshot=freeze(case,split_path=tmp_path/'split.json',output=tmp_path/'regression')
    assert snapshot['answers']['1']['U']=='是'
    result=evaluate(rows,snapshot['answers'],groups,development_errors=True)
    assert result['sets']['all']['agreement']['U']==399
    assert all(e['row'] in groups['development'] for e in result['development_errors'])
    assert 'holdout_errors' not in result
    record.write_text(record.read_text()+'\n'+json.dumps({'row':'2','field':'U','new':'是'}))
    with pytest.raises(ValueError,match='changed'):freeze(case,split_path=tmp_path/'split.json',output=tmp_path/'regression')


def test_motion_experiment_separates_horizontal_motion_from_stationary_captions():
    a={'text':'这一条横向移动的弹幕','x':.5,'y':.2,'w':.2,'h':.03};b={**a,'x':.42}
    subtitle={'text':'固定的UP主字幕','x':.2,'y':.75,'w':.4,'h':.03}
    found=match_motion([a,subtitle],[b,subtitle])
    assert len(found)==1 and found[0]['dx']==-.08
