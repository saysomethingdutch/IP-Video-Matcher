"""D-078 safe defaults, explicit alternatives and coupled Z edits, using temporary data."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from pilot.tsxk.ac import assemble
from pilot.tsxk.ac_filter import filter_ac, parse, sync_z_opinion
from pilot.tsxk.safe_feedback import local_row, SINGLE, SPEECH
from pilot.tsxk.pipeline import process_row
from pilot.tsxk import classify as K
from pilot.tsxk.tests.test_tsxk import timeline, seg
from pilot.tsxk.tests import test_tsxk as fixtures
from pilot.tsxk.config import TsxkCase
from pilot.review.test_tsxk_review import fixture
from pilot.review.tsxk import TsxkReviewStore


def test_filter_removes_only_banned_clause():
    text='角色互相交手，水印提示来源；画面节奏紧凑。'
    cleaned, removed=filter_ac({'主题概括':text,'比对意见':'保留情节，建议核实。'})
    assert cleaned['主题概括']=='角色互相交手，画面节奏紧凑。'
    assert cleaned['比对意见']=='保留情节。'
    assert removed[0]['text']=='水印提示来源；'


def test_z_sync_only_changes_grade_in_opinion():
    text=assemble({'主题概括':'原说明合理使用可能性为高。','比对意见':'画面节奏紧凑，合理使用可能性较高。'},[])
    result=sync_z_opinion(text,'低')
    assert parse(result)['主题概括']==parse(text)['主题概括']
    assert parse(result)['比对意见']=='画面节奏紧凑，合理使用可能性为低。'
    assert sync_z_opinion(result,'低')==result
    assert '合理使用可能性为中等' in sync_z_opinion(sync_z_opinion(result,'不涉及'),'中')


def test_default_A_never_calls_model_or_changes_original_category(tmp_path):
    _,config,data=fixture(tmp_path);case=TsxkCase(config)
    old=json.loads((data/'rows/7813.json').read_text());old['fields']['Y']['value']=SPEECH
    old['fields']['U']['value']='是';old['fields']['Z']['value']='中'
    old['fields']['AC']['value']=assemble({'主题概括':'本视频剪辑多个角色互相交战的画面，水印可见。','段落情节':'角色追击对方。','比对意见':'原片剪辑再组。'},['模型写的其他'])
    tl=timeline([seg(0,100,'tsxk')])
    before=deepcopy(old)
    with patch('pilot.tsxk.classify.classify',side_effect=AssertionError('A must not call model')):
        row=local_row(case,'7813',old,tl,tl)
    assert row['safe_feedback']['group']=='A'
    assert all(row['fields'][c]==old['fields'][c] for c in ('Y','U','V','Z'))
    assert old==before
    assert parse(row['fields']['AC']['value'])['主题概括']=='本视频剪辑多个角色互相交战的画面。'


def test_default_C_when_original_category_loses_unrelated_basis(tmp_path):
    _,config,data=fixture(tmp_path);case=TsxkCase(config)
    old=json.loads((data/'rows/7813.json').read_text());old['fields']['Y']['value']='偷渡'
    old['fields']['AC']['value']=assemble({'主题概括':'片段剪辑。','段落情节':'交战。','比对意见':'原片表达。'},[])
    tl=timeline([seg(0,100,'tsxk')])
    row=local_row(case,'7813',old,tl,tl)
    assert row['safe_feedback']['group']=='C'


def test_creation_section_losing_majority_is_fixed_y_rewrite(tmp_path):
    _,config,data=fixture(tmp_path);case=TsxkCase(config)
    old=json.loads((data/'rows/7813.json').read_text())
    old['fields']['AC']['value']=assemble({'主题概括':'角色剪辑。','段落情节':'交战。','新增创作要素':['剧场版画面来源','UP主水印','音乐'], '比对意见':'重新编排原片。'},[])
    tl=timeline([seg(0,100,'tsxk')]);row=local_row(case,'7813',old,tl,tl)
    assert row['safe_feedback']['group']=='B'
    assert '新增创作要素' in row['safe_feedback']['rewrite_sections']
    assert row['fields']['Y']['value']==old['fields']['Y']['value']


def test_fixed_animation_category_uses_model_z_and_does_not_claim_no_animation():
    reply=fixtures.R3RulesTest().reply(Z='中')
    from pilot.tsxk.tests.test_tsxk import RULES
    tl=timeline([seg(0,100,'tsxk')]);rules=RULES
    res={'data':{**reply,'Y':SPEECH,'U':'是'},'errors':[],'calls':[],'prompt':'','speech':0,
         'music':{'verdict':'否','evidence':[],'series_music_seconds':0}}
    with patch.object(K,'classify',return_value=res) as mocked:
        row=process_row({'C':'标题'},None,tl,rules,'system',fixed_y=SPEECH)
    assert row['fields']['Y']['value']==SPEECH
    assert row['fields']['Z']['value']=='中'
    assert mocked.call_args.args[4]['y_fixed']==SPEECH
    assert '没有吞噬星空动画画面' not in K.user_prompt('facts',SPEECH)


def test_voice_restores_V_guard():
    data=fixtures.R3RulesTest().reply(Y=SINGLE,U='否',V='是',Z='低')
    errors=K.check(data,SINGLE,{'W':{'value':'否'}},timeline([seg(0,100,'tsxk')]),{'verdict':'是'},speech=2)
    assert any(e.startswith('V：') for e in errors)


def test_alternative_is_read_fresh_and_applied_only_on_owner_click(tmp_path):
    from pilot.tsxk.pipeline import save
    from pilot.tsxk.safe_feedback import VERSION
    _,config,data=fixture(tmp_path)
    original=json.loads((data/'rows/7813.json').read_text())
    original['fields']['Y']['value']=SPEECH;original['fields']['U']['value']='是'
    save(original,data/'rows/7813.json');before=(data/'rows/7813.json').read_bytes()
    store=TsxkReviewStore(TsxkCase(config),decisions_path=tmp_path/'review.jsonl')
    assert store.alternative('7813')=={'eligible':True,'ready':False}
    alt=deepcopy(original);alt['safe_feedback_version']=VERSION
    for c,new in {'Y':SINGLE,'U':'否','V':'否','Z':'低','AC':assemble({'主题概括':'混剪。','段落情节':'交战。','比对意见':'原片剪辑。'},[])}.items():alt['fields'][c]['value']=new
    save(alt,data/'v2/alt/7813.json')
    available=store.detail('7813')['alternative']
    assert available['ready'] and not store.decisions_path.exists()
    with pytest.raises(ValueError):store.apply_alternative('7813','Owner','stale')
    with pytest.raises(ValueError):store.apply_alternative('7813','WO104 临时试跑',available['token'])
    assert not store.decisions_path.exists()
    store.apply_alternative('7813','Owner',available['token'])
    assert len(store.events['7813'])==5
    assert store.latest['7813']['Y']['new']==SINGLE
    assert (data/'rows/7813.json').read_bytes()==before
    assert not store.alternative('7813')['eligible']


def test_z_edit_records_synchronized_opinion_together(tmp_path):
    from pilot.tsxk.pipeline import save
    _,config,data=fixture(tmp_path)
    row=json.loads((data/'rows/7813.json').read_text())
    row['fields']['Z']['value']='中'
    row['fields']['AC']['value']=assemble({'主题概括':'混剪。','比对意见':'角色向重组，合理使用可能性为中等。'},[])
    save(row,data/'rows/7813.json')
    store=TsxkReviewStore(TsxkCase(config),decisions_path=tmp_path/'review.jsonl')
    preview=sync_z_opinion(row['fields']['AC']['value'],'低')
    assert not store.decisions_path.exists()
    store.decide('7813','modify',{'Z':'低'},actor='Owner',opinion_preview=preview,opinion_base=row['fields']['AC']['value'])
    assert [e['field'] for e in store.events['7813']]==['Z','AC']
    assert '合理使用可能性为低' in store.latest['7813']['AC']['new']
    before=store.decisions_path.read_bytes()
    with pytest.raises(ValueError,match='已变化'):
        store.decide('7813','modify',{'Z':'高'},actor='Owner',opinion_base='stale')
    assert store.decisions_path.read_bytes()==before


def test_cleanup_proposes_owner_y_u_inconsistency_without_writing(tmp_path):
    _,config,_=fixture(tmp_path);store=TsxkReviewStore(TsxkCase(config),decisions_path=tmp_path/'review.jsonl')
    store.decide('7813','modify',{'Y':SPEECH},actor='Owner')
    store.decide('7813','approve',{'U':'否'},actor='Owner')
    store.decide('7813','approve',{'AC':'ignored'},actor='Owner')
    before=store.decisions_path.read_bytes();proposal=store.cleanup_preview()
    assert proposal['rows'][0]['edits']['U']['new']=='是'
    assert store.decisions_path.read_bytes()==before
    with pytest.raises(ValueError):store.cleanup_confirm(proposal['token'],'Owner',False)
    assert store.decisions_path.read_bytes()==before


def test_R1_gate_can_install_after_original_107_gate_failed(tmp_path):
    import os,time
    from pilot.tsxk.owner_feedback import digest
    from pilot.tsxk.feedback_install import install
    from pilot.tsxk.pipeline import save
    _,config,data=fixture(tmp_path);case=TsxkCase(config)
    save({'passed':False},data/'v2/check/score.json');save({'passed':True},data/'v2/r1check/score.json')
    record=data/'review/decisions.jsonl';record.parent.mkdir()
    record.write_text(json.dumps({'row':'7813','field':'D','new':'Owner value'})+'\n')
    os.utime(record,(time.time()-700,time.time()-700))
    before=digest(data/'rows/7813.json')
    row=json.loads((data/'rows/7814.json').read_text());row['owner_feedback_version']='D-077-1'
    row['fields']['AC']['value']=assemble({'主题概括':'片段剪辑。','段落情节':'交战。','比对意见':'原片重组。'},['录屏未显示简介区'])
    row['ac_code_lines']=['录屏未显示简介区'];save(row,data/'v2/rows/7814.json')
    save({'frames':[],'segments':[]},data/'timeline/7814/timeline.json')
    save({'frames':[],'segments':[]},data/'v2/timeline/7814.json')
    result=install(case,{'remaining':['7814'],'protected_rows_sha256':{'7813':before}},score_path=data/'v2/r1check/score.json')
    assert result['installed']==['7814'] and digest(data/'rows/7813.json')==before


def test_staging_preserves_explicit_ledger_R_pending_but_rejects_silent_blank(tmp_path):
    from pilot.tsxk.pipeline import save
    from pilot.tsxk.feedback_install import verify_staged
    _,config,data=fixture(tmp_path);case=TsxkCase(config)
    row=json.loads((data/'rows/7813.json').read_text());row['owner_feedback_version']='D-077-1'
    row['fields']['R']={'value':None,'by':'webledger','basis':'起止证据不足'}
    row['flags']=[{'level':'yellow','reason':'R：台账判不了是否完整播放'}]
    row['fields']['AC']['value']=assemble({'主题概括':'片段。','段落情节':'交战。','比对意见':'重组。'},[]);row['ac_code_lines']=[]
    save(row,data/'v2/rows/7813.json');save({},data/'v2/timeline/7813.json')
    verify_staged(case,['7813'])
    row['flags']=[];save(row,data/'v2/rows/7813.json')
    with pytest.raises(ValueError,match='blank staged'):verify_staged(case,['7813'])


def test_disputed_Q_preserves_linked_owner_pending_Y(tmp_path):
    from pilot.tsxk.pipeline import save
    from pilot.tsxk.feedback_install import verify_staged
    _,config,data=fixture(tmp_path);case=TsxkCase(config)
    row=json.loads((data/'rows/7813.json').read_text());row['owner_feedback_version']='D-077-1'
    row['fields']['Q']={'value':None,'by':'timeline','basis':'争议帧复核后仍待定'}
    row['fields']['Y']={'value':None,'by':'model','basis':'GLM 按指引选择类别'}
    row['flags']=[{'level':'red','reason':'争议帧复核后仍一帧都没采用：Q、Y 交人判断'}]
    row['fields']['AC']['value']=assemble({'主题概括':'片段。','段落情节':'交战。','比对意见':'重组。'},[]);row['ac_code_lines']=[]
    save(row,data/'v2/rows/7813.json');save({},data/'v2/timeline/7813.json')
    before=(data/'v2/rows/7813.json').read_bytes()
    verify_staged(case,['7813'])
    assert (data/'v2/rows/7813.json').read_bytes()==before
    # A Q-only flag does not authorize a silent blank Y.
    row['flags']=[{'level':'red','reason':'争议帧：Q 交人判断'}]
    save(row,data/'v2/rows/7813.json')
    with pytest.raises(ValueError,match='blank staged'):verify_staged(case,['7813'])
