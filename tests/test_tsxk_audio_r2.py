import unittest
from pathlib import Path
import json
import tempfile
import wave
from unittest.mock import patch

from pilot.audio.finish_r2 import incomplete
from pilot.audio.tsxk_r1 import VERSION, _fingerprint, run, selected, speech_selection, transcribe
from pilot.audio.tsxk_subtitles import match_caption


class TsxkAudioR2Tests(unittest.TestCase):
    def test_formal_selection_and_frozen_check30_guard(self):
        self.assertEqual(selected('formal'), list(range(7813, 8213)))
        with self.assertRaisesRegex(RuntimeError, 'frozen'):
            run('check30')

    def test_partial_caption_match_with_four_second_window(self):
        spoken = '你的星空之兽还是兽神的后'
        caption = '才是兽神的后裔'
        romanized = {spoken: 'nideixingkongzhishouhaishishoushendehou',
                     caption: 'caishishoushendehouyi'}
        frames = [{'t': 14, 'items': [{'text': caption}]}]
        score, value, visible = match_caption(spoken, 10, 10, frames, romanized)
        self.assertTrue(visible)
        self.assertEqual(value, caption)
        self.assertGreaterEqual(score, .78)
        score, _, visible = match_caption(spoken, 9, 9, frames, romanized)
        self.assertFalse(visible)
        self.assertEqual(score, 0)

    def test_speech_needs_ten_seconds_and_three_percent(self):
        clauses = [{'speech_type': '旁白讲解', 'caption_similarity': 0,
                    'text': '这段剧情值得仔细分析', 'recording_start': t,
                    'recording_end': t + 4} for t in (0, 4, 8)]
        selected, _, seconds, candidate = speech_selection(clauses, 300)
        self.assertEqual(len(selected), 3)
        self.assertEqual(seconds, 12)
        self.assertEqual(candidate, 12)
        selected, _, seconds, candidate = speech_selection(clauses, 500)
        self.assertEqual(selected, [])
        self.assertEqual(seconds, 0)
        self.assertEqual(candidate, 12)
        selected, _, seconds, _ = speech_selection(clauses[:2], 100)
        self.assertEqual(selected, [])
        self.assertEqual(seconds, 0)

    def test_completion_retries_output_with_old_ledger_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video, ledger, output = (root / 'video.mp4', root / 'ledger.json',
                                     root / 'audio.json')
            video.write_bytes(b'local evidence')
            ledger.write_text(json.dumps({'play_start': 26, 'play_end': 53}))
            result = {'status': 'ok', 'analysis_version': VERSION,
                      'fingerprint': 'older-page-end'}
            output.write_text(json.dumps(result))
            with patch('pilot.audio.finish_r2.recording',
                       return_value=(video, ledger, output)):
                self.assertIn(7844, incomplete())
                result['fingerprint'] = _fingerprint(video, json.loads(ledger.read_text()))
                output.write_text(json.dumps(result))
                self.assertEqual(incomplete(), [])

    def test_digital_silence_skips_native_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write_silence(_video, _start, _end, wav):
                with wave.open(str(wav), 'wb') as stream:
                    stream.setnchannels(1)
                    stream.setsampwidth(2)
                    stream.setframerate(16000)
                    stream.writeframes(b'\0' * 4 * 16000 * 2)
            with patch('pilot.audio.tsxk_r1.TRANSCRIPTS', root), \
                 patch('pilot.audio.tsxk_r1._extract_wav', side_effect=write_silence), \
                 patch('pilot.audio.tsxk_r1._model', side_effect=AssertionError('model called')):
                chunks = transcribe(root / 'video.mp4', 0, 4, None, 'silent', [None])
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0]['text'], '')


if __name__ == '__main__':
    unittest.main()
