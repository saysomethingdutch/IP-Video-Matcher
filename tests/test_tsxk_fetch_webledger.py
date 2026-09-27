import unittest

from pilot.ezcun.fetch import expiry_from_signed_url
from pilot.webledger.ledger import playback_status, sample_times


class TsxkFetchLedgerTests(unittest.TestCase):
    def test_signed_url_expiry_is_derived_without_storing_signature(self):
        url = ('https://obs.example/video.mp4?X-Obs-Date=20260927T000000Z'
               '&X-Obs-Expires=3600&X-Obs-Signature=secret')
        self.assertEqual(expiry_from_signed_url(url), '2026-09-27T01:00:00+00:00')

    def test_playback_jump_is_a_definite_no(self):
        value, flags = playback_status([(10, 1, 60), (15, 35, 60)], 100, 65, 60)
        self.assertEqual(value, '否')
        self.assertIn('跳播', flags[0])

    def test_duration_only_is_flagged_for_review(self):
        value, flags = playback_status([], 112, 61, 60)
        self.assertEqual(value, '是')
        self.assertIn('需复核', flags[0])

    def test_sample_times_include_end_of_recording(self):
        times = sample_times(112.64)
        self.assertEqual(times[0], 0)
        self.assertEqual(times[-1], 112.14)


if __name__ == '__main__':
    unittest.main()
