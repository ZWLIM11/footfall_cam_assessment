"""Load video-bound human review data, or mark landmarks in OpenCV's desktop viewer."""

import argparse
import hashlib
import json
from pathlib import Path

import cv2 as cv


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_review(path, video, total, fps, width, height):
    if path is None:
        return {}, {}, False
    document = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if document.get('sha256') != checksum(video):
        raise ValueError('Review file belongs to a different video. Open the reviewer for this input.')
    labels = {}
    for segment in document['segments']:
        staff_id = segment['staff_id']
        keys = segment['points']
        for f, x, y, evidence in keys:
            if not (isinstance(f, int) and 0 <= f < total and 0 <= x < width and 0 <= y < height):
                raise ValueError(f'Review point outside source frame: {(f, x, y)}')
            labels.setdefault(f, {})[staff_id] = (x, y, evidence)
        for (a, xa, ya, ea), (b, xb, yb, eb) in zip(keys, keys[1:]):
            if not 1 <= b-a <= max(1, round(.72 * fps)):
                raise ValueError(f'Review anchors must be ordered and at most 0.72 s apart: {a}, {b}')
            for f in range(a+1, b):
                fraction = (f-a)/(b-a)
                evidence = 'review_occluded_interpolated' if 'review_occluded' in (ea, eb) else 'review_interpolated'
                labels.setdefault(f, {})[staff_id] = (round(xa + (xb-xa)*fraction, 1),
                                                      round(ya + (yb-ya)*fraction, 1), evidence)
    uncertain = {}
    for start, end, reason in document.get('uncertain', []):
        if not 0 <= start <= end < total:
            raise ValueError(f'Review interval outside source video: {start} to {end}')
        uncertain.update((f, reason) for f in range(start, end+1))
    return labels, uncertain, bool(document.get('complete_review'))


def annotate(video, output, staff_id):
    """Click visible body centers, and mark frames with a readable badge using B."""
    cap = cv.VideoCapture(str(video))
    output = Path(output)
    if not cap.isOpened():
        raise ValueError(f'Cannot open video: {video}')
    total = int(cap.get(cv.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv.CAP_PROP_FPS)
    if total < 1 or fps <= 0:
        raise ValueError('Video requires a readable frame count and FPS.')
    signature = checksum(video)
    document = {'sha256': signature, 'complete_review': False, 'segments': [], 'uncertain': []}
    if output.exists():
        document = json.loads(output.read_text(encoding='utf-8-sig'))
        if document.get('sha256') != signature:
            raise ValueError('Choose a new review path for this video.')
    current = {'staff_id': staff_id, 'points': []}
    document['segments'].append(current)
    name = 'FootfallCam review'
    cv.namedWindow(name, cv.WINDOW_NORMAL)
    index, point, evidence = 0, None, 'review_anchor'

    def click(event, x, y, flags, data):
        nonlocal point
        if event == cv.EVENT_LBUTTONDOWN:
            point = (x, y)

    cv.setMouseCallback(name, click)
    try:
        while True:
            cap.set(cv.CAP_PROP_POS_FRAMES, index)
            ok, frame = cap.read()
            if not ok:
                raise ValueError(f'Could not read frame {index}.')
            while True:
                overlay = frame.copy()
                if point is not None:
                    cv.drawMarker(overlay, point, (26, 255, 108), cv.MARKER_CROSS, 18, 2)
                captions = [f'Frame {index}/{total-1} | {index/fps:.2f}s | {evidence} | {len(current["points"])} anchors',
                            'Click center. S save point, B badge, N new segment, U undo, Q finish.',
                            'A/D +/-1 frame, J/L +/-5 frames, K/I +/-1 second.']
                for row, caption in enumerate(captions, 1):
                    cv.putText(overlay, caption, (8, row*20), cv.FONT_HERSHEY_SIMPLEX, .47, (0,0,0), 4)
                    cv.putText(overlay, caption, (8, row*20), cv.FONT_HERSHEY_SIMPLEX, .47, (255,255,255), 1)
                cv.imshow(name, overlay)
                key = cv.waitKey(35) & 255
                if key != 255 or cv.getWindowProperty(name, cv.WND_PROP_VISIBLE) < 1:
                    break
            if key in (27, ord('q')) or cv.getWindowProperty(name, cv.WND_PROP_VISIBLE) < 1:
                break
            steps = {'a': -1, 'd': 1, 'j': -5, 'l': 5, 'k': -round(fps), 'i': round(fps)}
            if chr(key) in steps:
                index = max(0, min(total-1, index + steps[chr(key)]))
                point, evidence = None, 'review_anchor'
            elif key == ord('b'):
                evidence = 'review_badge' if evidence == 'review_anchor' else 'review_anchor'
            elif key == ord('u') and current['points']:
                current['points'].pop()
            elif key == ord('n') and current['points']:
                current = {'staff_id': staff_id, 'points': []}
                document['segments'].append(current)
            elif key == ord('s') and point is not None:
                keys = current['points']
                if keys and not 1 <= index-keys[-1][0] <= max(1, round(.72*fps)):
                    print('Add an intervening point, move forward, or begin a new segment with N.', flush=True)
                else:
                    keys.append([index, *point, evidence])
    finally:
        cap.release()
        cv.destroyAllWindows()
    document['segments'] = [segment for segment in document['segments'] if segment['points']]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    print(f'Saved {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--staff-id', default='staff_1')
    args = parser.parse_args()
    annotate(args.input, args.output, args.staff_id)
