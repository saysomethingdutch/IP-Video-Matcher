import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pilot.audio.finish_check30b import incomplete
from pilot.audio.tsxk_r1 import VERSION, _fingerprint, recording, selected


class SecondCheckAudioTests(unittest.TestCase):
    def test_second_set_is_disjoint_and_uses_separate_evidence_files(self):
        first, second = set(selected('check30')), selected('check30b')
        self.assertEqual(len(second), 30)
        self.assertFalse(first & set(second))
        video, ledger, output = recording(second[0], 'check30b')
        self.assertEqual(video.parent.name, 'evidence_check30b')
        self.assertEqual(ledger.name, f'{second[0]}.json')
        self.assertEqual(output.name, f'{second[0]}.json')

    def test_audit_rejects_stale_ledger_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video, ledger, output = (root / 'video.mp4', root / 'ledger.json',
                                     root / 'audio.json')
            video.write_bytes(b'local')
            ledger.write_text(json.dumps({'play_start': 10, 'play_end': 20}))
            fingerprint = _fingerprint(video, json.loads(ledger.read_text()))
            output.write_text(json.dumps({'status': 'ok', 'analysis_version': VERSION,
                                          'fingerprint': fingerprint,
                                          'audio_uploaded': False, 'glm_text_only': True}))
            with patch('pilot.audio.finish_check30b.selected', return_value=[8215]), \
                 patch('pilot.audio.finish_check30b.recording',
                       return_value=(video, ledger, output)):
                self.assertEqual(incomplete(), [])
                ledger.write_text(json.dumps({'play_start': 10, 'play_end': 21}))
                self.assertEqual(incomplete(), [8215])


if __name__ == '__main__':
    unittest.main()
