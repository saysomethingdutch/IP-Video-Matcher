"""Regression checks for the two ASR boundaries that affect evidence timing."""
import sys
import unittest

from .mapping import audio_chunks, content_at, playback_segments, recording_spans
from .run import (HALLUCINATION_RESIDUE, assemble_segments, command,
                  repeated_within_segment, select_language)


class PlaybackMappingTests(unittest.TestCase):
    def test_seek_excludes_drag_second_and_maps_pre_seek_playback(self):
        row = {
            "video_start": 100, "video_end": 130, "video_duration": 200,
            "played_intervals": [[0, 19], [100, 110]],
            "seeks": [{"at": 120, "from": 19, "to": 100}],
            "playback_map": [
                {"video_start": 100, "video_end": 110, "content_start": 0,
                 "content_end": 10, "event": "play"},
                {"video_start": 110, "video_end": 120, "content_start": 10,
                 "content_end": 100, "event": "seek"},
                {"video_start": 120, "video_end": 130, "content_start": 100,
                 "content_end": 110, "event": "play"},
            ],
        }
        mapping = playback_segments(row)
        self.assertEqual([(x["start"], x["end"])
                          for x in recording_spans(mapping)], [(100, 119), (120, 130)])
        self.assertEqual(content_at(mapping, 115), 15)
        self.assertIsNone(content_at(mapping, 119.5))
        self.assertEqual(len(audio_chunks(recording_spans(mapping))), 2)

    def test_overlap_deduplicates_near_identical_chunk_text(self):
        mapping = [{"recording_start": 0, "recording_end": 45,
                    "content_start": 0, "content_end": 45}]
        chunks = [{"start": 0, "end": 40, "number": 0},
                  {"start": 38, "end": 45, "number": 1}]
        cached = [
            {"native": None, "zh_baseline": [{"text": "我们依然会冷静地告诉大家",
                                              "offsets": {"from": 38000, "to": 40000}}]},
            {"native": None, "zh_baseline": [{"text": "我们依然会稳定地告诉大家",
                                              "offsets": {"from": 0, "to": 3000}}]},
        ]
        result = assemble_segments(chunks, cached, mapping,
                                   {"language": "zh", "confidence": .95})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["chunk_number"], 1)

    def test_thai_title_recovers_low_confidence_detection(self):
        row = {"plaintiff": {"title": "【七时吉祥】〔泰配〕EP.31"}}
        detected, language = select_language(row, {"language": "th", "confidence": .407})
        self.assertEqual((detected["language"], language, detected["selection"]),
                         ("th", "th", "title_hint"))
        detected, language = select_language(row, {"language": "zh", "confidence": .91})
        self.assertEqual((detected["language"], language, detected["selection"]),
                         ("zh", "zh", "acoustic"))
        ordinary = {"plaintiff": {"title": "普通视频"}}
        detected, language = select_language(ordinary, {"language": "en", "confidence": .201})
        self.assertEqual((detected["language"], language, detected["acoustic_language"]),
                         ("zh", "zh", "en"))

    def test_repeated_words_within_one_model_segment_are_suspect(self):
        self.assertTrue(repeated_within_segment("อื้อ อื้อ อื้อ อื้อ"))
        self.assertTrue(repeated_within_segment("欢迎收看欢迎收看欢迎收看"))
        self.assertFalse(repeated_within_segment("欢迎收看今天的故事"))
        self.assertIsNotNone(HALLUCINATION_RESIDUE.search(
            "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"))

    def test_malformed_model_console_bytes_do_not_abort_a_row(self):
        result = command([sys.executable, "-c", "import os; os.write(1, bytes([255]))"])
        self.assertEqual(result.stdout, "\ufffd")


if __name__ == "__main__":
    unittest.main()
