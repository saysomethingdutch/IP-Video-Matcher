"""WO-107 regression gates; only synthetic rows and temporary review storage."""
from copy import deepcopy
import json

import pytest

from pilot.timeline.owner_feedback import repair, votes
from pilot.tsxk.ac_filter import filter_ac, parse
from pilot.tsxk.ac import assemble
from pilot.review.test_tsxk_review import fixture
from pilot.review.tsxk import TsxkReviewStore, normalize_flags
from pilot.tsxk.config import TsxkCase


def frame(t, content, why='', **kw):
    return {'t': t, 'content': content, 'form': '', 'source': '缓存', 'ocr': [],
            'cloud': {'answers': {b: {'content': content, 'form': '', 'why': why} for b in ('zhipu', 'deepseek')}}, **kw}


def timeline(frames):
    segs = [{'start': f['t'], 'end': f['t']+9, 'content': f['content'], 'form': f['form'], 'frames': 1, 'sources': ['缓存']}
            for f in frames]
    return {'frames': frames, 'segments': segs, 'span': [0, frames[-1]['t']+10], 'traffic': [], 'flags': []}


def test_cache_repair_preserves_source_and_drops_ending():
    source = timeline([frame(0, 'tsxk'), frame(10, 'tsxk'), frame(20, 'unrelated', ocr=['相关推荐'])])
    before = deepcopy(source)
    repaired = repair(source)
    assert source == before
    assert repaired['frames'][-1]['ignored'] == '结束页或连播'
    assert repaired['segments'][-1]['end'] == 19
    assert not repaired['summary']['unrelated_block']
    assert repair(repaired) == repaired


def test_prior_positive_vote_vetoes_later_unrelated_adoption():
    f = frame(10, 'unrelated')
    f['cloud']['answers']['zhipu']['content'] = 'tsxk'
    f['cloud']['arbitration'] = {'votes': {'zhipu': False, 'deepseek': False}}
    assert votes(f) == (True, False)
    result = repair(timeline([frame(0, 'unrelated'), f, frame(20, 'tsxk')]))
    assert [f['content'] for f in result['frames']] == ['unknown', 'unknown', 'tsxk']


def test_single_arbitration_vote_cannot_confirm_both_providers():
    f = frame(10, 'unrelated')
    f['cloud']['answers']['deepseek']['content'] = 'unknown'
    f['cloud']['arbitration'] = {'votes': {'zhipu': False}}
    assert votes(f) == (False, False)
    f['cloud']['arbitration']['votes']['deepseek'] = False
    assert votes(f) == (False, True)


def test_two_unrelated_frames_required_at_either_end():
    assert not repair(timeline([frame(0, 'unrelated'), frame(10, 'tsxk')]))['summary']['unrelated_block']
    assert repair(timeline([frame(0, 'unrelated'), frame(10, 'unrelated'), frame(20, 'tsxk')]))['summary']['unrelated_block']


def test_short_tail_exception_and_film_normalization():
    source = timeline([frame(0, 'tsxk_other', form='动画剧场版'), frame(10, 'creator')])
    source['segments'][0]['form'] = '动画剧场版'
    result = repair(source)
    assert result['frames'][0]['content'] == 'tsxk'
    assert not result['frames'][1].get('ignored')
    assert result['summary']['forms'] == []


def test_unknown_under_30_seconds_stays_technical():
    result = repair(timeline([frame(0, 'unrelated'), frame(10, 'tsxk')]))
    flag = result['flags'][0]
    assert flag['tier'] == 'technical'
    assert normalize_flags({'fields': {}, 'flags': [flag]}, None)[0]['tier'] == 'technical'


def test_ac_filter_removes_sentences_items_and_records_raw_text():
    ac = {'主题概括': '包含打斗。片尾为结束页。', '段落情节': '角色交手。',
          '新增创作要素': ['新增音乐', '有水印', '广告导流', '真人出镜', '其他国漫画面'],
          '比对意见': '表达依赖原片。建议核实。', '其他': ['模型说明']}
    clean, removed = filter_ac(ac, {'X': {'value': '否'}}, {'summary': {'face_frames': 1}, 'segments': []})
    assert clean['主题概括'] == '包含打斗。'
    assert clean['新增创作要素'] == ['新增音乐']
    assert clean['其他'] == []
    assert len(removed) == 7
    assert ac['其他'] == ['模型说明']


def test_dropdown_rejects_invalid_modification_and_old_invalid_approval(tmp_path):
    _, config, data = fixture(tmp_path)
    store = TsxkReviewStore(TsxkCase(config), decisions_path=tmp_path/'decisions.jsonl')
    with pytest.raises(ValueError, match='不合规'):
        store.decide('7813', 'modify', {'Y': '二创（单IP混剪）'}, actor='Owner')
    assert '不涉及' in store.allowed_values('Z')
    path = data/'rows/7813.json'
    row = json.loads(path.read_text());row['fields']['Y']['value']='二创（单IP混剪）';path.write_text(json.dumps(row))
    assert next(f for f in store.fields('7813') if f['column']=='Y')['invalid_value']
    with pytest.raises(ValueError, match='不合规'):
        store.approve_row('7813', actor='Owner')
    assert not store.decisions_path.exists()


def test_cleanup_requires_explicit_owner_confirmation_and_no_stale_token(tmp_path):
    _, config, data = fixture(tmp_path)
    store = TsxkReviewStore(TsxkCase(config), decisions_path=tmp_path/'decisions.jsonl')
    text = assemble({'主题概括':'打斗。', '新增创作要素':['音乐'], '其他':['模型说明']}, ['录屏未显示简介区'])
    store.decide('7813', 'modify', {'AC': text}, actor='Owner')
    before = store.decisions_path.read_bytes()
    proposal = store.cleanup_preview()
    assert proposal['row_count'] == proposal['sentence_count'] == 1
    assert store.decisions_path.read_bytes() == before
    with pytest.raises(ValueError):store.cleanup_confirm(proposal['token'], 'Owner', False)
    with pytest.raises(ValueError):store.cleanup_confirm(proposal['token'], 'WO104 临时试跑', True)
    with pytest.raises(ValueError):store.cleanup_confirm('stale', 'Owner', True)
    assert store.cleanup_confirm(proposal['token'], 'Owner', True)['rows'] == 1
    assert parse(store.latest['7813']['AC']['new'])['其他'] == ['录屏未显示简介区']
    assert store.events['7813'][-1]['action'] == 'modify'
    assert len(store.events['7813']) == 2


def test_install_rechecks_newly_reviewed_row_and_backs_up_before_swap(tmp_path):
    import os
    import time
    from pilot.tsxk.owner_feedback import digest, save
    from pilot.tsxk.feedback_install import install
    _, config, data = fixture(tmp_path)
    case = TsxkCase(config)
    original = {r: digest(data/'rows'/f'{r}.json') for r in case.rows}
    save({'passed': True}, data/'v2/check/score.json')
    (data/'fields.csv').write_text('old CSV')
    (data/'audit').mkdir();(data/'audit/findings.csv').write_text('old audit')
    # The plan was drawn before any review. A newly reviewed row is protected at
    # installation, even though it appears on the original remaining list.
    record = data/'review/decisions.jsonl';record.parent.mkdir()
    record.write_text(json.dumps({'row':'7813','field':'D','new':'owner value'})+'\n')
    os.utime(record, (time.time()-700, time.time()-700))
    for rid in case.rows:
        row = json.loads((data/'rows'/f'{rid}.json').read_text())
        row['owner_feedback_version']='D-077-1'
        row['fields']['AC']['value']=assemble({'主题概括':'新说明。','比对意见':'低。','其他':[]},['录屏未显示标签区'])
        row['ac_code_lines']=['录屏未显示标签区']
        save(row,data/'v2/rows'/f'{rid}.json')
        save({'frames':[],'segments':[]}, data/'v2/timeline'/f'{rid}.json')
    (data/'timeline/7814').mkdir();save({'frames':[],'segments':[]},data/'timeline/7814/timeline.json')
    receipt = install(case, {'remaining':case.rows,'protected_rows_sha256':{}})
    assert receipt['installed'] == ['7814']
    assert digest(data/'rows/7813.json') == original['7813']
    assert digest(data/'v1/rows/7814.json') == original['7814']
    assert (data/'v1/fields.csv').read_text() == 'old CSV'
    assert (data/'v1/audit/findings.csv').read_text() == 'old audit'
    assert 'owner value' in record.read_text()


def test_install_failed_gate_cannot_write_formal_files(tmp_path):
    from pilot.tsxk.owner_feedback import digest, save
    from pilot.tsxk.feedback_install import install
    _, config, data = fixture(tmp_path)
    before = digest(data/'rows/7813.json')
    save({'passed': False}, data/'v2/check/score.json')
    with pytest.raises(ValueError, match='self-check failed'):
        install(TsxkCase(config), {'remaining':['7813']})
    assert digest(data/'rows/7813.json') == before
    assert not (data/'v1').exists()
