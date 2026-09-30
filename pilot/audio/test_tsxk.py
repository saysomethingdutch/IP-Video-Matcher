"""WO-103 decisions that prevent mixed original dialogue and revoicing from merging."""
import unittest

from pilot.audio.tsxk import classify_chunks, music_judgment, video_time


def frame(t, subtitle):
    return {'t': t, 'items': [{'text': subtitle, 'x': .3, 'y': .76}]}


def chunk(t, text, tags=None):
    return {'recording_start': t, 'recording_end': t + 4,
            'text': text, 'tags': tags or ['BGM']}


class TsxkAudioTests(unittest.TestCase):
    def test_mixed_source_and_sustained_revoicing_are_separate(self):
        frames = [frame(2, '布洛琳留下卸甲'), frame(6, '剧情画面的其他对白'),
                  frame(10, '剧情画面的另一句对白'), frame(14, '画面上的第四句对白')]
        chunks = [chunk(0, '留下蟹甲，其他人全部出去。'),
                  chunk(4, '这句是重新配音的人声'),
                  chunk(8, '这一句也不是画面字幕'),
                  chunk(12, '连续第三段重新配音')]
        classify_chunks(chunks, frames)
        self.assertEqual(chunks[0]['clauses'][0]['kind'], 'source')
        self.assertEqual(chunks[0]['clauses'][1]['kind'], 'uploader')
        self.assertTrue(all(c['clauses'][0]['kind'] == 'uploader' for c in chunks[1:]))

    def test_missing_subtitle_does_not_prove_uploader_speech(self):
        chunks = [chunk(t, '这段有人说话但画面没有字幕') for t in (0, 4, 8)]
        classify_chunks(chunks, [])
        self.assertTrue(all(c['clauses'][0]['kind'] == 'uncertain' for c in chunks))

    def test_lyrics_in_sustained_music_are_not_uploader_speech(self):
        frames = [frame(t + 2, '与歌曲不同的画面字幕') for t in (0, 4, 8)]
        chunks = [chunk(t, '海阔天空歌词持续出现') for t in (0, 4, 8)]
        chunks += [chunk(t, '') for t in (12, 16, 20, 24, 28)]
        classify_chunks(chunks, frames)
        self.assertTrue(all(c['clauses'][0]['kind'] == 'uncertain' for c in chunks[:3]))

    def test_music_requires_corroboration_and_anchor_maps_time(self):
        chunks = [chunk(t, '', ['BGM']) for t in range(0, 24, 4)]
        self.assertTrue(music_judgment(chunks, [])['suspected'])
        self.assertEqual(video_time(62, {'play_start': 45, 'anchors': [[60, 21]]})[0], 23)


if __name__ == '__main__':
    unittest.main()
