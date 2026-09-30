"""Regression checks for subtitle alignment and fact-only music output."""
import unittest
import numpy as np

from pilot.audio.tsxk_r1 import music_facts, redub_candidates, speech_selection
from pilot.audio.tsxk_subtitles import match_caption, pinyin_values, portrait_bounds


class TsxkAudioR1Tests(unittest.TestCase):
    def test_portrait_player_detection(self):
        frame = np.zeros((540, 960, 3), dtype=np.uint8)
        frame[129:437, 277:453] = 100
        self.assertEqual(portrait_bounds(frame), (277, 129, 453, 437))
        frame[129:437, 84:645] = 100
        self.assertIsNone(portrait_bounds(frame))

    def test_homophones_and_split_clauses_match_the_source_caption(self):
        frames = [{'t': 12, 'items': [{'text': '兽神之心就在这里'}]}]
        words = ['瘦身之先', '就在这里', '另一段讲解', '兽神之心就在这里']
        pinyin = pinyin_values(words)
        for spoken in words[:2]:
            score, caption, visible = match_caption(spoken, 11, 13, frames, pinyin)
            self.assertGreaterEqual(score, .78)
            self.assertEqual(caption, words[-1])
            self.assertTrue(visible)
        score, _, _ = match_caption(words[2], 11, 13, frames, pinyin)
        self.assertLess(score, .78)

    def test_music_facts_do_not_claim_uploader_origin(self):
        chunks = [{'recording_start': 0, 'recording_end': 4, 'tags': ['BGM'], 'text': ''}]
        frames = [{'t': 1, 'items': [{'text': '歌词字幕'}]},
                  {'t': 2, 'items': [{'text': '啊啊啊'}]}]
        clauses = [{'recording_start': 2, 'recording_end': 3,
                    'speech_type': '歌词', 'concurrent_subtitle': True}]
        facts = music_facts(chunks, frames, {'tags': {'items': []}}, clauses)
        self.assertEqual(facts['music_only_spans'], [[0, 4]])
        self.assertEqual(facts['lyric_subtitle_spans'], [[1, 3]])
        self.assertNotIn('uploader', facts)

    def test_redub_requires_same_moment_and_detects_changed_final_word(self):
        clauses = [
            {'text': '而是强者身边要左右都是你', 'recording_start': 61,
             'recording_end': 63.4, 'speech_type': '角色台词',
             'caption_match': '而是强者身边要左右都是女人',
             'caption_similarity': .974},
            {'text': '爽', 'recording_start': 100.3, 'recording_end': 101,
             'speech_type': '角色台词', 'caption_match': '三弟我也要',
             'caption_similarity': .33},
        ]
        frames = [{'t': 62, 'items': [{'text': '而是强者身边要左右都是女人'}]},
                  {'t': 103, 'items': [{'text': '三弟我也要'}]}]
        result = redub_candidates(clauses, frames)
        self.assertEqual([item['text'] for item in result], [clauses[0]['text']])
        self.assertEqual(result[0]['basis'], '整句末尾改词')

    def test_promotion_is_kept_out_of_commentary_seconds(self):
        clauses = [{'text': t, 'recording_start': a, 'recording_end': b,
                    'speech_type': '旁白讲解', 'caption_similarity': 0}
                   for t, a, b in [('逗你玩的喜欢的话会', 42, 45),
                                   ('本视频需要一个硬币才能观看', 49, 52),
                                   ('关注投币支持一下', 53, 57)]]
        uploader, promotions, seconds = speech_selection(clauses)
        self.assertEqual(uploader, [])
        self.assertEqual(len(promotions), 3)
        self.assertEqual(seconds, 0)

    def test_sustained_unscripted_commentary_remains(self):
        clauses = [{'text': t, 'recording_start': a, 'recording_end': a + 4,
                    'speech_type': '旁白讲解', 'caption_similarity': .1}
                   for t, a in [('这集剧情交代了反派的计划', 0),
                                ('我觉得这里角色动机仍不清楚', 4),
                                ('后来这一段的转折才解释了它', 8)]]
        uploader, promotions, seconds = speech_selection(clauses)
        self.assertEqual(len(uploader), 3)
        self.assertEqual(promotions, [])
        self.assertEqual(seconds, 12)

    def test_generic_redub_needs_a_continuous_mismatch(self):
        clauses = [{'text': '我现在要去见那个人', 'recording_start': 0,
                    'recording_end': 4, 'speech_type': '角色台词',
                    'caption_match': '敌军已经攻入城里', 'caption_similarity': .1},
                   {'text': '我们今天不会退缩', 'recording_start': 4,
                    'recording_end': 8, 'speech_type': '角色台词',
                    'caption_match': '王国明日准备迁都', 'caption_similarity': .2}]
        frames = [{'t': 1, 'items': [{'text': clauses[0]['caption_match']}]},
                  {'t': 5, 'items': [{'text': clauses[1]['caption_match']}]}]
        self.assertEqual(len(redub_candidates(clauses[:1], frames)), 0)
        self.assertEqual(len(redub_candidates(clauses, frames)), 2)


if __name__ == '__main__':
    unittest.main()
