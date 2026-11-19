"""Compare two automatic runs against previously reviewed sample locations."""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import median

from run import write_csv
from verify import rows


def locations(root):
    by_frame = defaultdict(list)
    for row in rows(root / 'staff_coordinates.csv'):
        by_frame[int(row['frame_index'])].append((float(row['x_center']), float(row['y_center'])))
    return by_frame


def compare(reference, baseline, current, output):
    sources = [reference, baseline, current]
    summaries = [json.loads((root / 'run_summary.json').read_text(encoding='utf-8')) for root in sources]
    dimensions = ('frame_count', 'fps', 'width', 'height')
    if any(tuple(info[key] for key in dimensions) != tuple(summaries[0][key] for key in dimensions)
           for info in summaries[1:]):
        raise ValueError('All runs must have the same source video dimensions, frame count and FPS.')
    if any(info['review'] is not None for info in summaries[1:]):
        raise ValueError('Baseline and improved runs must both be generated without a review file.')
    total = summaries[0]['frame_count']
    truth, before, after = map(locations, sources)
    if any(len(points) != 1 for points in truth.values()):
        raise ValueError('This sample comparison expects one visually reviewed person per positive frame.')

    def distances(frame, estimates):
        return sorted(math.dist(truth[frame][0], point) for point in estimates[frame]) if frame in truth else []

    def measure(estimates, runtime):
        gaps = {f: distances(f, estimates) for f in list(estimates)}
        measured = [errors[0] for errors in gaps.values() if errors]
        return {'flagged_frames': sum(map(bool, estimates.values())),
                'coordinate_rows': sum(map(len, estimates.values())),
                'frames_with_prediction_within_40_px': sum(x <= 40 for x in measured),
                'frames_with_prediction_within_60_px': sum(x <= 60 for x in measured),
                'predictions_farther_than_60_px': sum(sum(x > 60 for x in errors) for errors in gaps.values()),
                'extra_predictions_within_60_px': sum(max(0, sum(x <= 60 for x in errors)-1) for errors in gaps.values()),
                'predictions_on_unreviewed_frames': sum(len(estimates[f]) for f in gaps if f not in truth),
                'nearest_center_median_px_on_flagged_reviewed_frames': round(median(measured), 1) if measured else None,
                'runtime_seconds': runtime}

    result = {'reviewed_positive_frames': len(truth), 'source_frame_count': total,
              'baseline': measure(before, summaries[1]['runtime_seconds']),
              'improved': measure(after, summaries[2]['runtime_seconds'])}
    result['additional_frames_with_prediction_within_60_px'] = (
        result['improved']['frames_with_prediction_within_60_px'] - result['baseline']['frames_with_prediction_within_60_px'])
    result['interpretation'] = ('Distances are against the prior visual estimate of the same wearer on this recording. '
                                'This is an in-clip comparison with no independent negatives or unseen-video accuracy estimate.')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'comparison.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')

    def nearest(f, predictions):
        delta = distances(f, predictions)
        return round(delta[0], 1) if delta else None

    write_csv(output / 'comparison_by_frame.csv',
              ['frame_index', 'reviewed_location', 'baseline_instances', 'improved_instances',
               'baseline_nearest_error_px', 'improved_nearest_error_px'],
              ((f, int(f in truth), len(before[f]), len(after[f]), nearest(f, before), nearest(f, after))
               for f in range(total)))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--improved', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    compare(args.reference, args.baseline, args.improved, args.output)
