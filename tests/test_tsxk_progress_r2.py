import unittest

from pilot.webledger.progress_r2 import decide


class ProgressR2Tests(unittest.TestCase):
    def test_spikes_returning_to_the_old_line_are_not_seeks(self):
        points = [[t, round((t - 10) / 100, 4)] for t in range(12, 112, 2)]
        for t in (48, 50, 52):
            points[(t - 12) // 2][1] += .25
        self.assertTrue(decide(points, 10, 112).complete)

    def test_persistent_seek_is_a_no_with_two_evidence_times(self):
        points = [[t, round((t - 10) / 100 + (.25 if t >= 50 else 0), 4)]
                  for t in range(12, 86, 2)]
        result = decide(points, 10, 86)
        self.assertIs(result.complete, False)
        self.assertEqual(len(result.evidence_times), 2)

    def test_replay_is_decided_per_pass(self):
        first = [[t, round((t - 10) / 100, 4)] for t in range(12, 112, 2)]
        replay = [[t, round((t - 112) / 100, 4)] for t in range(114, 122, 2)]
        self.assertTrue(decide(first + replay, 10, 122).complete)

    def test_endpoints_can_be_extrapolated(self):
        points = [[t, round((t - 10) / 100, 4)] for t in range(16, 108, 2)]
        result = decide(points, 10, 112)
        self.assertIs(result.complete, True)
        self.assertIn('外推', result.basis)

    def test_early_end_has_screenshot_time(self):
        points = [[t, round((t - 10) / 100, 4)] for t in range(12, 62, 2)]
        result = decide(points, 10, 62)
        self.assertIs(result.complete, False)
        self.assertEqual(len(result.evidence_times), 1)


if __name__ == '__main__':
    unittest.main()
