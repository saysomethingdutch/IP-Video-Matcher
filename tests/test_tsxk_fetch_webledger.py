import unittest
from unittest.mock import patch

from pilot.ezcun.fetch import check_entries, check30b_entries, expiry_from_signed_url
from pilot.webledger.ledger import resolve_row, sample_times


class TsxkFetchLedgerTests(unittest.TestCase):
    def test_signed_url_expiry_is_derived_without_storing_signature(self):
        url = ('https://obs.example/video.mp4?X-Obs-Date=20260927T000000Z'
               '&X-Obs-Expires=3600&X-Obs-Signature=secret')
        self.assertEqual(expiry_from_signed_url(url), '2026-09-27T01:00:00+00:00')

    def test_sample_times_include_end_of_recording(self):
        times = sample_times(112.64)
        self.assertEqual(times[0], 0)
        self.assertEqual(times[-1], 112.14)

    def test_second_check_list_uses_input_csv_and_is_disjoint(self):
        first, second = check_entries(), check30b_entries()
        self.assertEqual(len(second), 30)
        self.assertFalse({item['row'] for item in first} &
                         {item['row'] for item in second})
        with patch('pilot.webledger.ledger.entries', side_effect=AssertionError('workbook opened')):
            self.assertEqual(resolve_row(None, second[0]['row'])['source_set'], 'check30b')


if __name__ == '__main__':
    unittest.main()
