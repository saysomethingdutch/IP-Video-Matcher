"""Checks for the D-043 source-dialogue and music corroboration rules."""
from __future__ import annotations

import unittest
import json
from pathlib import Path
import tempfile

from pilot.audio.pipeline import (_music_evidence, _sentence_segments, compare_dialogue,
                                  decode_tags, played_recording_spans)
from pilot.semantic.audio import load_audio
from pilot.textindex.search import Index, SubEntry
from pilot.textindex.text import normalize


class AudioFactsTest(unittest.TestCase):
    def test_only_play_events_are_extracted(self):
        row = {"playback_map": [
            {"event": "play", "video_start": 10, "video_end": 20},
            {"event": "pause", "video_start": 20, "video_end": 35},
            {"event": "seek", "video_start": 35, "video_end": 60},
            {"event": "play", "video_start": 60, "video_end": 66}]}
        self.assertEqual(played_recording_spans(row), [(10, 14), (14, 18), (18, 20), (60, 64), (64, 66)])

    def test_tags_are_separate_from_transcript(self):
        text, tags = decode_tags("<|zh|><|BGM|><|Speech|>你好。")
        self.assertEqual(text, "你好。")
        self.assertEqual(tags, ["zh", "BGM", "Speech"])

    def test_wo004b_r2_can_read_sound_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "audio"
            audio.mkdir()
            (audio / "1663.json").write_text(json.dumps({
                "row_id": "1663", "engine": "SenseVoice-Small", "played_seconds": 59,
                "segments": [{"start": 1.2, "end": 3.4, "text": "我认为这段好笑", "kind": "uploader"}],
                "events": {"speech": .7, "bgm": .1, "laughter": .2},
                "uploader_music": {"suspected": None, "basis": "缺少旁证"}}))
            result = load_audio(Path(folder), "1663")
            self.assertEqual(result["up_speech"][0]["text"], "我认为这段好笑")
            self.assertEqual(result["speech_ratio"], .7)


class AudioEvidenceTests(unittest.TestCase):
    def setUp(self):
        line = "你为什么还没有回来"
        self.index = Index([SubEntry(1, 1, 10, 12, line, normalize(line), 1, ())])

    def test_ocr_at_same_time_recovers_mistranscribed_source_line(self):
        events = [{"rec_start": 482.0, "rec_end": 483.0, "text": "日思夜想 想见一个人的时候"}]
        clause = compare_dialogue("我日思夜想想见一个人的时候", self.index, events, 481, 484)[0]
        self.assertEqual(clause["kind"], "原剧台词")
        self.assertEqual(clause["source_basis"], "同期画面字幕")
        far = compare_dialogue("我日思夜想想见一个人的时候", self.index, events, 490, 494)[0]
        self.assertEqual(far["kind"], "UP主口播候选")

    def test_index_match_and_short_uncertain(self):
        source = compare_dialogue("你为什么还没有回來", self.index, [], 10, 14)[0]
        self.assertEqual(source["kind"], "原剧台词")
        self.assertEqual(source["source_basis"], "原剧台词索引")
        short = compare_dialogue("那好吧", self.index, [], 10, 14)[0]
        self.assertEqual(short["kind"], "未定短句")
        no_screen = compare_dialogue("完全看不到字幕证据", self.index, [], 10, 14,
                                     visual_subtitles_available=False)[0]
        self.assertEqual(no_screen["kind"], "未定短句")
        segments = _sentence_segments({"playback_map": []}, [{"recording_start": 10, "recording_end": 14,
                                                               "clauses": [short]}])
        self.assertEqual(segments[0]["kind"], "uncertain")

    def test_bgm_alone_is_not_uploader_music(self):
        chunks = [{"recording_start": 0, "recording_end": 4, "tags": ["BGM"], "text": "台词", "clauses": []}]
        self.assertIsNone(_music_evidence({}, chunks, [], {}, False))
        lyric = [{"rec_start": 2, "rec_end": 2, "text": "♪歌词", "lyric": True}]
        self.assertEqual(_music_evidence({}, chunks, lyric, {}, False), "同期 OCR 歌词字幕")
        self.assertEqual(_music_evidence({}, chunks, [], {2: [{"text": "♪ 歌曲名"}]}, False),
                         "同期 B 站音乐标签")
        sustained = [{"recording_start": t, "recording_end": t + 4,
                      "tags": ["BGM"], "text": "", "clauses": []} for t in range(0, 20, 4)]
        self.assertIsNone(_music_evidence({}, sustained, [], {}, False))
        self.assertIn("连续至少20秒", _music_evidence({}, sustained, [], {}, True))


if __name__ == "__main__":
    unittest.main()
