"""Foreground proposals, badge appearance, and short-term motion association."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2 as cv
import numpy as np


@dataclass
class Candidate:
    box: tuple[int, int, int, int]
    center: tuple[float, float]
    badge_score: float = 0.0
    reference_score: float = 0.0
    badge_xy: tuple[int, int] | None = None
    reference_xy: tuple[int, int] | None = None


def background(video, nframes, width):
    """Use a bounded sample across the clip so a paused walker stays visible."""
    cap = cv.VideoCapture(str(video))
    frames = []
    try:
        for index in np.unique(np.linspace(0, nframes - 1, min(nframes, 45), dtype=int)):
            cap.set(cv.CAP_PROP_POS_FRAMES, int(index))
            ok, frame = cap.read()
            if ok:
                small = resize(frame, width)
                if small.mean() >= 38:
                    frames.append(small)
    finally:
        cap.release()
    return np.median(np.stack(frames), axis=0).astype(np.uint8) if frames else None


def resize(frame, width):
    height, source_width = frame.shape[:2]
    return cv.resize(frame, (width, max(1, round(height * width / source_width))),
                     interpolation=cv.INTER_AREA)


def proposals(small, bg, previous, width, height, threshold):
    """Propose compact foreground regions. Bystanders and objects can also qualify."""
    diff = cv.absdiff(small, bg).max(axis=2)
    mask = np.uint8(diff >= threshold) * 255
    scale = max(1, round(small.shape[1] / 320))
    kernel = cv.getStructuringElement(cv.MORPH_ELLIPSE, (2 * scale + 1,) * 2)
    mask = cv.morphologyEx(mask, cv.MORPH_OPEN, kernel)
    mask = cv.morphologyEx(mask, cv.MORPH_CLOSE, kernel, iterations=2)
    moving = cv.absdiff(small, previous).max(axis=2) >= 21 if previous is not None else None
    _, labels, stats, _ = cv.connectedComponentsWithStats(mask)
    image_area = small.shape[0] * small.shape[1]
    sx, sy = width / small.shape[1], height / small.shape[0]
    for component, (x, y, w, h, area) in enumerate(stats[1:], 1):
        if not (image_area * .00058 <= area <= image_area * .075 and
                w >= 8 and h >= 8 and .2 <= w / h <= 5 and area / (w * h) >= .25):
            continue
        own_mask = labels[y:y+h, x:x+w] == component
        activity = moving is not None and np.count_nonzero(moving[y:y+h, x:x+w] & own_mask) >= max(8, area * .025)
        box = (round(x*sx), round(y*sy), round((x+w)*sx), round((y+h)*sy))
        center = ((box[0]+box[2])/2, (box[1]+box[3])/2)
        yield Candidate(box, center), bool(activity), own_mask


class BadgeMatcher:
    def __init__(self, directory, min_contrast=9):
        self.min_contrast = min_contrast
        directory = Path(directory)
        names = ('front.png', 'dark.png', 'light.png')
        assets = [directory / name for name in names]
        missing = next((file for file in assets if not file.is_file()), None)
        if missing is not None:
            raise ValueError(f'Missing badge template: {missing}')
        self.references = [cv.imread(str(file), cv.IMREAD_GRAYSCALE) for file in assets]
        if any(ref is None or ref.std() < 3 for ref in self.references):
            raise ValueError(f'Unreadable or flat badge template in {directory}')

    @lru_cache(maxsize=256)
    def templates(self, index, width):
        original = self.references[index]
        height = max(5, round(width * original.shape[0] / original.shape[1]))
        patch = cv.resize(original, (width, height), interpolation=cv.INTER_AREA)
        bank = []
        for angle in range(0, 360, 30):
            matrix = cv.getRotationMatrix2D(((width - 1)/2, (height - 1)/2), angle, 1)
            out_w = max(4, round(width*abs(matrix[0,0]) + height*abs(matrix[0,1])))
            out_h = max(4, round(width*abs(matrix[0,1]) + height*abs(matrix[0,0])))
            matrix[:, 2] += ((out_w-width)/2, (out_h-height)/2)
            bank.append(cv.warpAffine(patch, matrix, (out_w, out_h), borderMode=cv.BORDER_REPLICATE))
        return tuple(bank)

    def score(self, gray, candidate, own_mask):
        x1, y1, x2, y2 = candidate.box
        w, h = x2-x1, y2-y1
        # The central area of the moving body is a broad torso proxy for overhead views.
        padx, pady = w//12, h//12
        region = gray[y1+pady:y2-pady, x1+padx:x2-padx]
        if min(region.shape) < 10:
            return
        foreground = cv.resize(np.uint8(own_mask), (w, h), interpolation=cv.INTER_NEAREST)
        foreground = cv.dilate(foreground, np.ones((5, 5), np.uint8))[pady:h-pady, padx:w-padx]
        sizes = sorted({max(7, min(42, round(np.hypot(w, h) * ratio / 2) * 2))
                        for ratio in (.075, .108, .146)})
        for index in range(len(self.references)):
            top_score = 0.0
            top_xy = None
            for size in sizes:
                for template in self.templates(index, size):
                    th, tw = template.shape
                    if th > region.shape[0] or tw > region.shape[1]:
                        continue
                    scores = np.abs(cv.matchTemplate(region, template, cv.TM_CCOEFF_NORMED))
                    support = foreground[th//2:th//2+scores.shape[0], tw//2:tw//2+scores.shape[1]]
                    scores[support == 0] = 0
                    # Ignore high correlation on effectively flat patches.
                    for _ in range(3):
                        _, value, _, (x, y) = cv.minMaxLoc(scores)
                        if value <= top_score:
                            break
                        if region[y:y+th, x:x+tw].std() >= self.min_contrast:
                            top_score = value
                            top_xy = (x+x1+padx+tw//2, y+y1+pady+th//2)
                            break
                        scores[max(0,y-2):y+3, max(0,x-2):x+3] = 0
            if index == 0:
                candidate.reference_score = top_score
                candidate.reference_xy = top_xy
            elif top_score > candidate.badge_score:
                candidate.badge_score = top_score
                candidate.badge_xy = top_xy


@dataclass
class Track:
    id: int
    last_index: int
    observations: list
    velocity: tuple[float, float] = (0, 0)


def associate(active, detections, frame_index, next_id, max_gap, image_size):
    """One-to-one association. Break at large gaps or ambiguous close crossings."""
    active = [track for track in active if frame_index - track.last_index <= max_gap]
    pairs = []
    for ti, track in enumerate(active):
        last = track.observations[-1][1]
        gap = frame_index - track.last_index
        px, py = (p + v * min(gap, 3) for p, v in zip(last.center, track.velocity))
        diag = np.hypot(last.box[2]-last.box[0], last.box[3]-last.box[1])
        radius = min(np.hypot(*image_size) * .11, diag * (.48 + .19 * gap))
        for di, (cand, _, _) in enumerate(detections):
            x, y = cand.center
            dist = np.hypot(x-px, y-py)
            if dist <= radius:
                pairs.append((dist / max(1, radius), ti, di))
    pairs.sort()
    used_tracks, used_dets = set(), set()
    for cost, ti, di in pairs:
        if ti in used_tracks or di in used_dets:
            continue
        if any(other_ti != ti and other_ti not in used_tracks and other_di == di and other_cost - cost < .12
               for other_cost, other_ti, other_di in pairs):
            # Do not use a contested detection as identity evidence for either older track.
            continue
        track = active[ti]
        cand, _, _ = detections[di]
        dt = frame_index - track.last_index
        track.velocity = tuple((a-b)/dt for a, b in zip(cand.center, track.observations[-1][1].center))
        track.observations.append((frame_index, cand))
        track.last_index = frame_index
        used_tracks.add(ti)
        used_dets.add(di)
    new = []
    for di, (cand, moving, _) in enumerate(detections):
        if di not in used_dets and moving:
            new.append(Track(next_id, frame_index, [(frame_index, cand)]))
            next_id += 1
    return active + new, new, next_id


def classify(observations, threshold, fps, window_seconds, method='overhead'):
    """Require spatially consistent, repeated badge evidence and bound propagation."""
    window = max(1, round(fps * window_seconds))
    pair_gap = min(window, max(1, round(fps * .24)))
    score_field, point_field = ('badge_score', 'badge_xy') if method == 'overhead' else ('reference_score', 'reference_xy')
    positives = [(frame, cand, getattr(cand, point_field)) for frame, cand in observations
                 if getattr(cand, score_field) >= threshold - .03 and getattr(cand, point_field) is not None]
    reliable = set()
    for i, (fa, a, pa) in enumerate(positives):
        box_diagonal = np.hypot(a.box[2]-a.box[0], a.box[3]-a.box[1])
        for fb, b, pb in positives[i+1:]:
            if fb-fa > pair_gap:
                break
            if max(getattr(a, score_field), getattr(b, score_field)) < threshold:
                continue
            da = (pa[0]-a.center[0], pa[1]-a.center[1])
            db = (pb[0]-b.center[0], pb[1]-b.center[1])
            absolute = np.hypot(pa[0]-pb[0], pa[1]-pb[1])
            if absolute >= max(2, box_diagonal*.035) and np.hypot(da[0]-db[0], da[1]-db[1]) <= max(5, box_diagonal*.23):
                reliable.update((fa, fb))
    for frame, cand in observations:
        nearest = min((abs(frame-anchor) for anchor in reliable), default=window+1)
        if nearest <= window:
            yield frame, cand, 'auto_badge' if frame in reliable else 'auto_track'
