#!/usr/bin/env python3
"""Identify staff-tag evidence in fixed overhead video and export frame-level results."""

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
from time import perf_counter

import cv2 as cv

from continuity import MotionHistory, continuity_results
from review import load_review
from vision import BadgeMatcher, associate, background, classify, proposals, resize


def input_video(path):
    if not path.is_file():
        raise ValueError(f'Input video does not exist: {path}')
    cap = cv.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f'OpenCV could not open {path}')
    total, width, height = (int(cap.get(prop)) for prop in
                            (cv.CAP_PROP_FRAME_COUNT, cv.CAP_PROP_FRAME_WIDTH, cv.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv.CAP_PROP_FPS)
    if min(total, width, height, fps) <= 0:
        cap.release()
        raise ValueError('Video requires readable dimensions, FPS and frame count.')
    return cap, total, fps, width, height


def scan(args, cap, total, fps, width, height):
    small_width = min(args.analysis_width, width)
    bg = background(args.input, total, small_width)
    matcher = BadgeMatcher(args.templates)
    tracks, active = [], []
    next_id, previous = 1, None
    frame_boxes = []
    low_light = {}
    history = MotionHistory(width, height, args.tracking == 'continuity')
    progress_interval = getattr(args, 'progress_interval', 250)
    for frame_index in range(total):
        ok, frame = cap.read()
        if not ok:
            raise ValueError(f'Could not decode frame {frame_index} of {total}.')
        small = resize(frame, small_width)
        if small.mean() < 42 or bg is None:
            low_light[frame_index] = 'low_light'
            previous, active = None, []
            frame_boxes.append([])
            history.append(None, [])
            continue
        if args.tracking == 'continuity' and previous is not None and cv.absdiff(small, previous).mean() >= 24:
            low_light[frame_index] = 'scene_change'
            previous, active = None, []
            frame_boxes.append([])
            history.append(None, [])
            continue
        candidates = list(proposals(small, bg, previous, width, height, args.motion_threshold))
        active, new, next_id = associate(active, candidates, frame_index, next_id,
                                         max(1, round(.16 * fps)), (width, height))
        tracks.extend(new)
        current = [t.observations[-1][1] for t in active if t.last_index == frame_index]
        if frame_index % args.scan_stride == 0 and current:
            masks = {id(cand): mask for cand, _, mask in candidates}
            gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
            for candidate in current:
                matcher.score(gray, candidate, masks[id(candidate)])
        frame_boxes.append(current)
        history.append(small, [candidate for candidate, _, _ in candidates])
        previous = small
        if frame_index and frame_index % progress_interval == 0:
            print(f'Scanned {frame_index}/{total} frames', flush=True)
    return frame_boxes, tracks, low_light, history


def intervals(flags):
    start = None
    for frame_index, on in enumerate([*flags, False]):
        if on and start is None:
            start = frame_index
        elif not on and start is not None:
            yield start, frame_index-1
            start = None


def automatic_results(tracks, threshold, fps, window, method):
    labels = defaultdict(dict)
    score_field = 'badge_score' if method == 'overhead' else 'reference_score'
    for track in tracks:
        for frame, cand, evidence in classify(track.observations, threshold, fps, window, method):
            labels[frame][f'auto_{track.id}'] = (*cand.center, evidence, cand.box, getattr(cand, score_field) or None)
    return labels


def merge_results(frame_boxes, automatic, review, excluded, complete, threshold, requested_review):
    frames, locations, quality = [], [], []
    for fi, candidates in enumerate(frame_boxes):
        reviewed = review.get(fi)
        chosen = dict(automatic.get(fi, {})) if not complete and not reviewed else {}
        if reviewed:
            for staff_id, (x, y, evidence) in reviewed.items():
                # Review verifies the landmark. A foreground component can contain another person.
                chosen[staff_id] = (x, y, evidence, None, None)
        top = max((candidate.badge_score for candidate in candidates), default=0)
        # Reviewed evidence can resolve a dim frame, but explicit review exclusions take precedence.
        reason = excluded.get(fi) if not reviewed or fi in requested_review else None
        if reason:
            chosen = {}
        status = ('present' if chosen else 'uncertain' if reason else 'reviewed_no_staff' if complete else
                  'uncertain' if top >= threshold - .065 else 'not_detected')
        if status == 'uncertain':
            quality.append((fi, reason or 'unconfirmed_badge_candidate', round(top, 4)))
        for staff_id, (x, y, evidence, box, score) in sorted(chosen.items()):
            locations.append((fi, staff_id, x, y, evidence, box, score))
            if evidence.startswith('review_occluded'):
                quality.append((fi, 'reviewed_partial_visibility', None))
        frames.append((int(bool(chosen)), status, '|'.join(sorted(chosen))))
    return frames, locations, quality


def write_csv(path, columns, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def export(args, frames, locations, quality, tracks, auto, comparison, baseline, fps):
    directory = args.output

    def stamp(f):
        return f'{f/fps:.6f}'

    write_csv(directory / 'staff_frames.csv', ['frame_index', 'time_seconds', 'staff_present', 'status', 'staff_ids'],
              ((fi, stamp(fi), *result) for fi, result in enumerate(frames)))
    write_csv(directory / 'staff_coordinates.csv',
              ['frame_index', 'time_seconds', 'staff_id', 'x_center', 'y_center', 'evidence',
               'x1', 'y1', 'x2', 'y2', 'badge_score'],
              ((fi, stamp(fi), staff_id, x, y, evidence, *(box or ('',)*4), score)
               for fi, staff_id, x, y, evidence, box, score in locations))
    write_csv(directory / 'staff_intervals.csv', ['start_frame', 'end_frame', 'start_seconds', 'end_seconds_inclusive'],
              ((a, b, stamp(a), stamp(b)) for a, b in intervals([value[0] for value in frames])))
    write_csv(directory / 'quality_review.csv', ['frame_index', 'time_seconds', 'reason', 'best_overhead_score'],
              ((fi, stamp(fi), reason, score) for fi, reason, score in quality))
    write_csv(directory / 'badge_candidates.csv',
              ['frame_index', 'time_seconds', 'track_id', 'x1', 'y1', 'x2', 'y2', 'overhead_score',
               'reference_score', 'badge_x', 'badge_y', 'reference_x', 'reference_y'],
              ((fi, stamp(fi), f'auto_{track.id}', *cand.box, round(cand.badge_score, 5), round(cand.reference_score, 5),
                *(cand.badge_xy or ('', '')), *(cand.reference_xy or ('', '')))
               for track in tracks for fi, cand in track.observations if cand.badge_xy or cand.reference_xy))
    write_csv(directory / 'automatic_comparison.csv',
              ['frame_index', 'time_seconds', 'overhead_template_present', 'front_reference_present', 'local_tracking_present'],
              ((fi, stamp(fi), int(fi in auto), int(fi in comparison), int(fi in baseline)) for fi in range(len(frames))))


def annotate(video, output, frames, locations, width, height, fps):
    points = defaultdict(list)
    for loc in locations:
        points[loc[0]].append(loc)
    cap = cv.VideoCapture(str(video))
    writer = cv.VideoWriter(str(output), cv.VideoWriter_fourcc(*'mp4v'), fps, (width, height))
    try:
        if not writer.isOpened():
            raise ValueError(f'Could not open MP4 writer for {output}.')
        for frame_index, (_, status, _) in enumerate(frames):
            ok, frame = cap.read()
            if not ok:
                raise ValueError(f'Could not render frame {frame_index}.')
            color = (46, 235, 107) if status == 'present' else (20, 205, 255) if status == 'uncertain' else (230, 230, 230)
            caption = f'{frame_index:05d}  {frame_index / fps:6.2f}s  {status.replace("_", " ").upper()}'
            cv.rectangle(frame, (0, 0), (min(width, 425), 28), (18, 18, 18), -1)
            cv.putText(frame, caption, (9, 20), cv.FONT_HERSHEY_SIMPLEX, .54, color, 1, cv.LINE_AA)
            for _, staff_id, x, y, evidence, box, _ in points[frame_index]:
                pt = round(x), round(y)
                if box:
                    x1, y1, x2, y2 = box
                    cv.rectangle(frame, (x1, y1), (x2-1, y2-1), color, 1)
                cv.drawMarker(frame, pt, (0,0,0), cv.MARKER_CROSS, 20, 4)
                cv.drawMarker(frame, pt, color, cv.MARKER_CROSS, 18, 2)
                text = f'{staff_id} ({x:.0f}, {y:.0f}) {evidence.replace("_", " ")}'
                label_at = min(max(1, pt[0]+9), max(1, width-290)), min(height-4, max(41, pt[1]-8))
                cv.putText(frame, text, label_at, cv.FONT_HERSHEY_SIMPLEX, .43, (0,0,0), 4, cv.LINE_AA)
                cv.putText(frame, text, label_at, cv.FONT_HERSHEY_SIMPLEX, .43, color, 1, cv.LINE_AA)
            writer.write(frame)
    finally:
        writer.release()
        cap.release()


def run(args):
    started = perf_counter()
    cv.setNumThreads(2)
    cap, total, fps, width, height = input_video(args.input)
    try:
        review, review_exclusions, complete = load_review(args.review, args.input, total, fps, width, height)
        args.output.mkdir(parents=True, exist_ok=True)
        frame_boxes, tracks, low_light, history = scan(args, cap, total, fps, width, height)
    finally:
        cap.release()
    baseline = automatic_results(tracks, args.badge_threshold, fps, args.window, 'overhead')
    overhead, links = baseline, 0
    if args.tracking == 'continuity':
        print('Linking badge sightings through motion', flush=True)
        overhead, links = continuity_results(tracks, history, fps, args.badge_threshold, args.window,
                                             args.bridge_seconds, args.tail_seconds)
    reference = automatic_results(tracks, args.badge_threshold, fps, args.window, 'reference')
    excluded = {**low_light, **review_exclusions}
    frames, locations, quality = merge_results(frame_boxes, overhead, review, excluded,
                                               complete, args.badge_threshold, review_exclusions)
    export(args, frames, locations, quality, tracks, overhead, reference, baseline, fps)
    if args.visualize:
        print('Writing annotated video', flush=True)
        annotate(args.input, args.output / 'annotated.mp4', frames, locations, width, height, fps)
    summary = {'input': str(args.input), 'output': str(args.output), 'fps': fps, 'frame_count': total,
               'width': width, 'height': height, 'duration_seconds': total/fps,
               'status_counts': dict(Counter(result[1] for result in frames)),
               'staff_instances': len(locations), 'evidence_counts': dict(Counter(row[4] for row in locations)),
               'overhead_automatic_positive_frames': len(overhead), 'reference_automatic_positive_frames': len(reference),
               'local_tracking_positive_frames': len(baseline), 'motion_supported_links': links,
               'review': str(args.review) if args.review else None, 'complete_review': complete,
               'settings': {'motion_threshold': args.motion_threshold, 'badge_threshold': args.badge_threshold,
                            'window_seconds': args.window, 'scan_stride': args.scan_stride,
                            'tracking': args.tracking, 'bridge_seconds': args.bridge_seconds, 'tail_seconds': args.tail_seconds,
                            'analysis_width': min(args.analysis_width, width), 'device': args.device,
                            'templates': str(args.templates), 'opencv': cv.__version__},
               'runtime_seconds': round(perf_counter()-started, 2)}
    (args.output / 'run_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)


def main(argv=None, *, progress_interval=250):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', default=Path('output'), type=Path)
    parser.add_argument('--templates', default=Path(__file__).resolve().parent / 'assets', type=Path)
    parser.add_argument('--review', type=Path, help='Optional human review JSON for this exact video')
    parser.add_argument('--visualize', action='store_true', help='Write annotated.mp4')
    parser.add_argument('--device', choices=['cpu'], default='cpu')
    parser.add_argument('--analysis-width', type=int, default=480)
    parser.add_argument('--motion-threshold', type=int, default=33)
    parser.add_argument('--badge-threshold', type=float, default=.92)
    parser.add_argument('--window', type=float, default=.32, help='Seconds for repeated evidence and propagation')
    parser.add_argument('--scan-stride', type=int, default=2, help='Score badges every N frames. Track every frame.')
    parser.add_argument('--tracking', choices=['continuity', 'local'], default='continuity',
                        help='Link badge sightings with optical flow, or use the original short window.')
    parser.add_argument('--bridge-seconds', type=float, default=14,
                        help='Maximum duration for a continuous link supported by badges at both ends.')
    parser.add_argument('--tail-seconds', type=float, default=1.2,
                        help='Maximum seconds to follow the same foreground track from a single sighting.')
    args = parser.parse_args(argv)
    args.progress_interval = progress_interval
    if not (args.analysis_width >= 120 and args.scan_stride >= 1 and 0 < args.badge_threshold <= 1 and
            0 < args.motion_threshold < 255 and 0 < args.window <= 1 and
            0 < args.bridge_seconds <= 30 and 0 <= args.tail_seconds <= 3):
        parser.error('Thresholds, window, analysis width or stride out of range.')
    try:
        run(args)
    except (OSError, ValueError, cv.error) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
