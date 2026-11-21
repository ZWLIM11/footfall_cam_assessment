from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import cv2 as cv
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from continuity import MotionHistory, Sighting, solve_bridge
from review import checksum, load_review
from run import export, input_video, intervals, merge_results, run
from verify import rows, verify
from vision import Candidate, Track, associate, classify, proposals


class PipelineTests(unittest.TestCase):
    def test_empty_foreground(self):
        image = np.full((120, 160, 3), 120, dtype=np.uint8)
        self.assertEqual(list(proposals(image, image, image, 160, 120, 33)), [])

    def test_ambiguous_overlap_starts_new_track(self):
        candidate = Candidate((10, 10, 40, 40), (25, 25))
        active = [Track(i, 0, [(0, candidate)]) for i in (1, 2)]
        _, born, _ = associate(active, [(candidate, True, None)], 1, 3, 3, (200, 200))
        self.assertEqual([track.last_index for track in active], [0, 0])
        self.assertEqual([track.id for track in born], [3])

    def test_badge_evidence_has_bounded_propagation(self):
        hits = []
        for frame in range(31):
            cand = Candidate((frame*4, 10, frame*4+30, 70), (frame*4+15, 40))
            if frame in (10, 13):
                cand.badge_score = .94 if frame == 10 else .90
                cand.badge_xy = (frame*4+15, 34)
            hits.append((frame, cand))
        accepted = list(classify(hits, .92, 25, .32))
        self.assertEqual((accepted[0][0], accepted[-1][0]), (2, 21))
        self.assertEqual([f for f, _, why in accepted if why == 'auto_badge'], [10, 13])
        self.assertFalse(list(classify(hits[:13], .92, 25, .32)))

    def test_intervals_do_not_bridge_one_frame(self):
        self.assertEqual(list(intervals([1, 1, 0, 1])), [(0, 1), (3, 3)])

    def test_export_counts_each_staff_detection_only_in_its_frame(self):
        person = (12, 34, 'auto_badge', None, .96)
        second = (56, 78, 'auto_bridge', None, None)
        automatic = {0: {'staff_1': person, 'staff_2': second}, 1: {'staff_1': person}}
        frames, locations, quality = merge_results([[], [], []], automatic, {}, {}, False, .92, False)
        with TemporaryDirectory() as name:
            args = SimpleNamespace(output=Path(name))
            export(args, frames, locations, quality, [], automatic, {}, {}, 25)
            values = rows(args.output / 'staff_frames.csv')
            self.assertEqual([row['staff_count'] for row in values], ['2', '1', '0'])
            self.assertEqual([row['staff_present'] for row in values], ['1', '1', '0'])

    def test_motion_bridge_handles_short_missing_detection_but_rejects_cut(self):
        history = MotionHistory(384, 216, True)
        image = np.full((216, 384, 3), 120, dtype=np.uint8)
        for frame in range(6):
            x = 40 + 3*frame
            person = Candidate((x, 40, x+40, 100), (x+20, 70))
            history.append(image, [person] if frame != 2 else [])
        start, end = Sighting(0, 0, 1, .94), Sighting(5, 0, 2, .95)
        path = solve_bridge(history.frames, start, end, {}, 25, .8)
        self.assertEqual(path, [(0, 0), (1, 0), (3, 0), (4, 0), (5, 0)])
        history.frames[2] = None
        self.assertEqual(solve_bridge(history.frames, start, end, {}, 25, .8), [])

    def test_missing_input(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            input_video(ROOT / 'definitely_missing.mp4')

    def test_corrupt_video(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'broken.mp4'
            path.write_text('garbage', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'could not open'):
                input_video(path)

    def test_review_path_and_interpolation(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            sample = root / 'video.mp4'
            sample.write_bytes(b'test signature')
            review = root / 'review.json'
            review.write_text(json.dumps({'sha256': checksum(sample), 'segments': [
                {'staff_id': 'staff_1', 'points': [[0, 10, 40, 'review_badge'], [4, 20, 30, 'review_occluded']]}]}))
            labels, _, _ = load_review(review, sample, 5, 25, 200, 100)
            self.assertEqual(labels[2]['staff_1'], (15.0, 35.0, 'review_occluded_interpolated'))
            sample.write_bytes(b'different video')
            with self.assertRaisesRegex(ValueError, 'different video'):
                load_review(review, sample, 5, 25, 200, 100)

    def test_different_mp4_empty_scene_at_12_fps(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            path = root / 'empty_384x216.mp4'
            writer = cv.VideoWriter(str(path), cv.VideoWriter_fourcc(*'mp4v'), 12, (384, 216))
            self.assertTrue(writer.isOpened())
            for _ in range(19):
                writer.write(np.full((216, 384, 3), 120, dtype=np.uint8))
            writer.release()
            templates = root/'templates'
            templates.mkdir()
            fixture = np.zeros((16, 24), dtype=np.uint8)
            fixture[3:13, 8:17] = 255
            for badge in ('front.png', 'dark.png', 'light.png'):
                self.assertTrue(cv.imwrite(str(templates/badge), fixture))
            args = SimpleNamespace(input=path, output=root/'results', templates=templates, review=None,
                                   visualize=True, device='cpu', analysis_width=480, motion_threshold=33,
                                   scan_stride=2, badge_threshold=.92, window=.32,
                                   tracking='continuity', bridge_seconds=14, tail_seconds=1.2)
            with redirect_stdout(io.StringIO()):
                run(args)
                result = verify(args.output)
            self.assertEqual((result['frame_rows'], result['staff_coordinate_rows'], result['fps']), (19, 0, 12))


if __name__ == '__main__':
    unittest.main()
