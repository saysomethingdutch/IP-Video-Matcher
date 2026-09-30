import unittest
from unittest.mock import patch

from pilot.webledger.content_span import locate
from pilot.webledger.ledger import address_observation, page_segments
from pilot.webledger.progress_r2 import decide
from pilot.webledger.progress_r2 import Decision


def frame(t, address=None, desktop=False):
    items = [{'text': address, 'x': .2, 'y': .06}] if address else []
    if desktop:
        items.extend({'text': text, 'x': .1, 'y': .15}
                     for text in ('此电脑', '回收站'))
    return {'t': t, 'items': items}


class ContentSpanR3Tests(unittest.TestCase):
    def test_corrupt_bv_is_still_target_and_switch_is_autoplay(self):
        target = 'BV1nLz9BJEiX'
        records = [frame(29, target), frame(44, 'BV1nLz9BJE议X'),
                   frame(47, 'BV1nLz9BJE议X'),
                   frame(53, 'BV1Nf6FB5EP5?spm=player.switch')]
        self.assertEqual(address_observation(records[1])[0], 'BV1nLz9BJE议X')
        with patch('pilot.webledger.ledger.refine_switch', return_value=53):
            _, start, end, _, others, autoplay, flags = page_segments(
                records, target, 56, 'unused.mp4')
        self.assertEqual((start, end), (26, 53))
        self.assertTrue(autoplay)
        self.assertEqual(others[0]['bv'], 'BV1Nf6FB5EP5')
        self.assertFalse(any('浏览器关闭' in flag for flag in flags))

    def test_missing_address_only_closes_with_desktop_evidence(self):
        target = 'BV1nLz9BJEiX'
        records = [frame(10, target), frame(13), frame(16), frame(19)]
        self.assertEqual(page_segments(records, target, 22, 'unused.mp4')[2], 22)
        records[2] = frame(16, desktop=True)
        records[3] = frame(19, desktop=True)
        self.assertEqual(page_segments(records, target, 22, 'unused.mp4')[2], 16)

    def test_replay_uses_complete_pass_and_excludes_full_bar_plateau(self):
        points = [[52, .0282], [54, .0352], [56, .0352], [58, .0106]]
        points += [[t, round(.0106 + (t - 58) * .0053, 4)]
                   for t in range(60, 240, 2)]
        points += [[240, .9982], [242, .9982], [244, .9982]]
        result = decide(points, 42, 247)
        content = locate(points, 42, 247, result)
        self.assertEqual(len(content['passes']), 2)
        self.assertEqual(content['passes'][1]['complete'], True)
        self.assertEqual(content['content_end'], 240)
        self.assertGreater(content['content_start'], 54)

    def test_temporary_full_reading_does_not_cut_the_content(self):
        points = [[t, round((t - 10) / 100, 4)] for t in range(12, 110, 2)]
        points[(100 - 12) // 2][1] = .9965
        points[(102 - 12) // 2][1] = .9965
        points += [[110, 1.0], [112, 1.0]]
        content = locate(points, 10, 114, Decision(None, '进度条', '待核'))
        self.assertGreaterEqual(content['content_end'], 108)
        self.assertTrue(content['complete_needs_review'])


if __name__ == '__main__':
    unittest.main()
