import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('wo110', Path(__file__).parents[1] / 'tools/wo110_audio_experiment.py')
wo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wo)


def test_failed_and_interrupted_submissions_count_and_reserve():
    events = [{'event':'submit_intent','attempt':i,'reserved_cny':.9} for i in range(1,121)]
    events.append({'event':'failed','attempt':1})
    with pytest.raises(ValueError):
        wo.budget(events,.02)


def test_unknown_usage_never_releases_reservation():
    events = [{'event':'submit_intent','attempt':i,'reserved_cny':.9} for i in range(1,56)]
    with pytest.raises(ValueError):
        wo.budget(events,.9)


def test_completed_usage_releases_only_unused_budget():
    events = [{'event':'submit_intent','attempt':1,'reserved_cny':.9},
              {'event':'completed','attempt':1,'estimated_cny':.001}]
    assert wo.budget(events,.9) == (1,.001)


def test_next_reservation_cannot_exceed_cap():
    events = [{'event':'submit_intent','attempt':1,'reserved_cny':49.2}]
    with pytest.raises(ValueError):
        wo.budget(events,.9)


def test_payload_contains_only_audio_and_fixed_question(tmp_path):
    clip = tmp_path/'fixture.wav'
    clip.write_bytes(b'local fixture')
    p = {'test':'B','clip':str(clip),'title':'NEVER SEND','reference':'NEVER SEND','row':'1234'}
    payload = wo.payload(p)
    assert payload['model'] == wo.MODELS['B']
    assert len(payload['messages'][0]['content']) == 2
    assert payload['messages'][0]['content'][1]['text'] == wo.PROMPT
    assert 'NEVER SEND' not in str(payload)
    assert '1234' not in str(payload)
    p['test']='A'
    assert len(wo.payload(p)['input']['messages'][0]['content']) == 1


def test_redirect_does_not_forward_authentication():
    assert wo.NoRedirect().redirect_request(None,None,302,'',{},'https://other.example') is None


def test_usage_missing_is_not_zero_cost():
    assert wo.usage_cost({'total_tokens':100}) is None
    assert wo.usage_cost({'input_tokens':-1,'output_tokens':0}) is None
    assert wo.usage_cost({'prompt_tokens':1000,'completion_tokens':100}) == pytest.approx(.00107)


def test_ocr_reference_is_independent_of_asr():
    frames=[{'t':i,'items':[{'text':'罗峰正在寻找坐山客','x':.25,'y':.7},
                            {'text':'吞噬星空','x':.7,'y':.7}]} for i in range(3)]
    ref=wo.ocr_reference(frames,0,3)
    assert ref['text']=='罗峰正在寻找坐山客'
    assert ref['coverage']==1


def test_supplement_cannot_access_holdout(tmp_path):
    root=tmp_path/'root';out=root/'workspace/audio-exp';out.mkdir(parents=True)
    wo.write(root/'cases/tsxk_regression_split.json',{'development':['1'],'holdout':['2']})
    wo.write(out/'plan.json',[{'id':'existing'}])
    wo.write(out/'supplement-preflight.json',{'frozen_files':{}})
    wo.write(out/'supplement-plan.json',[{'id':str(i),'test':'A','model':wo.MODELS['A'],'row':'2'} for i in range(4)])
    with pytest.raises(ValueError,match='Unauthorized supplement'):
        wo.validate_supplement(root,out)


def test_supplement_preflight_detects_tampering(tmp_path):
    wo.write(tmp_path/'supplement-plan.json',[])
    wo.write(tmp_path/'supplement-preflight.json',{'frozen_files':{'supplement-plan.json':'wrong hash'}})
    with pytest.raises(ValueError,match='Supplement artifact changed'):
        wo.validate_supplement(tmp_path,tmp_path)


def test_supplement_uses_existing_submission_and_cost_ledger():
    events=[]
    for i in range(1,84):
        events += [{'event':'submit_intent','attempt':i,'reserved_cny':.02},
                   {'event':'completed','attempt':i,'estimated_cny':.001}]
    assert wo.budget(events,.02)[0]==83
    assert wo.budget(events,.02)[1]==pytest.approx(.083)


def test_local_reference_ignores_higher_overlay(tmp_path):
    spec=importlib.util.spec_from_file_location('local_ref',Path(__file__).parents[1]/'tools/wo110_local_reference.py')
    local=importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0,str(Path(__file__).parents[1]/'tools'))
    spec.loader.exec_module(local)
    item=lambda t,y:{'text':t,'x':.25,'y':y,'w':.4,'h':.15,'conf':.9}
    p={'id':'A-fixture','row':'1','start':10,'sha256':'hash'}
    result=local.assemble(tmp_path,p,[{'frame':'crop-01.jpg','items':[item('较高的评论文字',.1),item('真正的原片字幕',.7)]}])
    assert result['text']=='真正的原片字幕'
