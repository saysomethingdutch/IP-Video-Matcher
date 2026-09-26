"""Played-interval boundary and output-shape checks."""
import unittest
import json
from pathlib import Path
import tempfile

from .pipeline import decode_tags, played_recording_spans
from pilot.semantic.audio import load_audio


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

    def test_wo004b_r2_can_read_sound_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "audio"
            audio.mkdir()
            (audio / "1663.json").write_text(json.dumps({
                "row_id": "1663", "engine": "SenseVoice-Small", "played_seconds": 59,
                "segments": [{"start": 1.2, "end": 3.4, "text": "我认为这段好笑", "kind": "uploader"}],
                "events": {"speech": .7, "bgm": .1, "laughter": .2},
                "uploader_music": {"suspected": False, "basis": "BGM不占多数"}}))
            result = load_audio(Path(folder), "1663")
            self.assertEqual(result["up_speech"][0]["text"], "我认为这段好笑")
            self.assertEqual(result["speech_ratio"], .7)


if __name__ == "__main__":
    unittest.main()
