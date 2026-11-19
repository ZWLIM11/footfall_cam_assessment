"""Validate the exported frame, coordinate and interval CSVs against the run metadata."""

import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path

import cv2 as cv

from run import intervals


def rows(path):
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def check(condition, message):
    if not condition:
        raise ValueError(message)


def verify(directory):
    root = Path(directory)
    summary = json.loads((root / 'run_summary.json').read_text(encoding='utf-8'))
    total, fps, width, height = (summary[key] for key in ('frame_count', 'fps', 'width', 'height'))
    frames = rows(root / 'staff_frames.csv')
    coords = rows(root / 'staff_coordinates.csv')
    check([int(row['frame_index']) for row in frames] == list(range(total)), 'Missing or unordered frames')
    by_frame = defaultdict(set)
    for row in coords:
        f = int(row['frame_index'])
        x, y = float(row['x_center']), float(row['y_center'])
        check(0 <= f < total and 0 <= x < width and 0 <= y < height, f'Coordinate out of bounds at frame {f}')
        check(row['staff_id'] not in by_frame[f], f'Duplicate staff instance at frame {f}')
        check(math.isclose(float(row['time_seconds']), f/fps, abs_tol=1e-6), f'Bad coordinate timestamp {f}')
        if row['x1']:
            x1, y1, x2, y2 = (int(row[key]) for key in ('x1', 'y1', 'x2', 'y2'))
            check(0 <= x1 <= x < x2 <= width and 0 <= y1 <= y < y2 <= height, f'Bad box at frame {f}')
        by_frame[f].add(row['staff_id'])
    flags = []
    for f, row in enumerate(frames):
        actual = set(row['staff_ids'].split('|')) if row['staff_ids'] else set()
        check(actual == by_frame[f], f'Staff IDs disagree with coordinates for frame {f}')
        check(int(row['staff_present']) == bool(actual), f'Staff flag disagrees with coordinates for frame {f}')
        check((row['status'] == 'present') == bool(actual), f'Status disagrees with labels for frame {f}')
        check(math.isclose(float(row['time_seconds']), f/fps, abs_tol=1e-6), f'Bad frame timestamp {f}')
        flags.append(bool(actual))
    saved_intervals = [(int(r['start_frame']), int(r['end_frame'])) for r in rows(root / 'staff_intervals.csv')]
    check(list(intervals(flags)) == saved_intervals, 'Interval output does not match frame output')
    check(summary['staff_instances'] == len(coords), 'Summary instance count disagrees with CSV')
    overlay = root / 'annotated.mp4'
    if overlay.is_file():
        cap = cv.VideoCapture(str(overlay))
        try:
            check(cap.isOpened(), 'Cannot reopen annotated video')
            check(int(cap.get(cv.CAP_PROP_FRAME_COUNT)) == total and
                  int(cap.get(cv.CAP_PROP_FRAME_WIDTH)) == width and
                  int(cap.get(cv.CAP_PROP_FRAME_HEIGHT)) == height and
                  math.isclose(cap.get(cv.CAP_PROP_FPS), fps, rel_tol=.002), 'Annotated video metadata differs')
            decoded = 0
            while cap.grab():
                decoded += 1
            check(decoded == total, 'Annotated video did not decode to the expected frame count')
        finally:
            cap.release()
    result = {'frame_rows': total, 'staff_positive_frames': sum(flags),
              'staff_coordinate_rows': len(coords), 'positive_intervals': saved_intervals,
              'original_resolution': [width, height], 'fps': fps,
              'annotated_video_checked': overlay.is_file()}
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    try:
        verify(parser.parse_args().directory)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'Verification failed: {exc}\n')
