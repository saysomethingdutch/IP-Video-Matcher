"""Played-interval boundary and output-shape checks."""
import unittest

from .pipeline import decode_tags, played_recording_spans


class AudioFactsTest(unittest.TestCase):
    def test_only_play_events_are_extracted(self):
        row = {"playback_map": [
            {"event": "play", "video_start": 10, "video_end": 20},
            {"event": "pause", "video_start": 20, "video_end": 35},
            {"event": "seek", "video_start": 35, "video_end": 60},
            {"event": "play", "video_start": 60, "video_end": 66}]}
        self.assertEqual(played_recording_spans(row), [(10, 20), (60, 66)])

    def test_tags_are_separate_from_transcript(self):
        text, tags = decode_tags("<|zh|><|BGM|><|Speech|>你好。")
        self.assertEqual(text, "你好。")
        self.assertEqual(tags, ["zh", "BGM", "Speech"])


if __name__ == "__main__":
    unittest.main()
