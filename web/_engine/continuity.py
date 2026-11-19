"""Connect badge sightings through motion-supported paths in fixed-camera video."""

from collections import defaultdict
from dataclasses import dataclass

import cv2 as cv
import numpy as np

from vision import classify


@dataclass
class FrameMotion:
    candidates: list
    centers: np.ndarray
    diagonal: np.ndarray
    log_area: np.ndarray
    following: np.ndarray


class MotionHistory:
    """Store flow summaries for foreground regions, keeping only two images in memory."""

    def __init__(self, width, height, enabled):
        self.width = width
        self.height = height
        self.flow = cv.DISOpticalFlow_create(cv.DISOPTICAL_FLOW_PRESET_ULTRAFAST) if enabled else None
        self.frames = []
        self.previous_gray = None
        self.scale = None

    def append(self, image, candidates):
        if self.flow is None:
            return
        if image is None:
            self.frames.append(None)
            self.previous_gray = None
            return
        gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
        if self.scale is None:
            self.scale = np.array([gray.shape[1] / self.width, gray.shape[0] / self.height])
        bounds = np.asarray([c.box for c in candidates], dtype=np.float32).reshape(-1, 4)
        bounds *= np.tile(self.scale, 2)
        sizes = bounds[:, 2:] - bounds[:, :2]
        centers = (bounds[:, :2] + bounds[:, 2:]) * .5
        vectors = np.zeros_like(centers)
        current = FrameMotion(candidates, centers, np.linalg.norm(sizes, axis=1),
                              np.log(np.maximum(1, np.prod(sizes, axis=1))), vectors)
        if self.previous_gray is not None:
            previous = self.frames[-1]
            previous.following = self.measure(self.previous_gray, gray, previous)
        self.frames.append(current)
        self.previous_gray = gray

    def measure(self, a, b, frame):
        if not frame.candidates:
            return np.zeros_like(frame.centers)
        field = self.flow.calc(a, b, None)
        result = []
        for x1, y1, x2, y2 in (np.array([c.box for c in frame.candidates]) * np.tile(self.scale, 2)).astype(int):
            dx, dy = (x2 - x1) // 5, (y2 - y1) // 5
            core = field[y1+dy:y2-dy:2, x1+dx:x2-dx:2].reshape(-1, 2)
            result.append(np.median(core, axis=0) if core.size else (0, 0))
        return np.array(result, dtype=np.float32)


@dataclass(frozen=True)
class Sighting:
    frame: int
    node: int
    track: int
    score: float


def badge_sightings(track, threshold, fps, window):
    accepted = {frame: cand for frame, cand, evidence in classify(track.observations, threshold, fps, window)
                if evidence == 'auto_badge'}
    # Two confident patches can qualify over a wider time window when both the badge and the body move together.
    candidates = [(frame, cand) for frame, cand in track.observations
                  if cand.badge_xy is not None and cand.badge_score >= threshold - .02]
    for index, (a, ca) in enumerate(candidates):
        pa, xa = np.array(ca.badge_xy), np.array(ca.center)
        for b, cb in candidates[index+1:]:
            if b-a > max(1, round(.48*fps)):
                break
            pb, xb = np.array(cb.badge_xy), np.array(cb.center)
            size = max(np.hypot(c.box[2]-c.box[0], c.box[3]-c.box[1]) for c in (ca, cb))
            if (np.linalg.norm(pb-pa) >= .16*size and np.linalg.norm(xb-xa) >= .09*size
                    and np.linalg.norm((pb-xb)-(pa-xa)) <= .30*size):
                accepted[a], accepted[b] = ca, cb
    return sorted(accepted.items())


def solve_bridge(frames, a, b, lineage, fps, scale):
    """Find the lowest-cost path between two badge-bearing foreground regions."""
    section = frames[a.frame:b.frame+1]
    if len(section) < 2 or any(frame is None for frame in section):
        return []
    initial = np.full(len(section[0].candidates), np.inf)
    initial[a.node] = 0
    costs = [(initial, None, None)]
    max_gap = max(1, round(.16*fps))
    preferred = (a.track, b.track)
    for i, current in enumerate(section[1:], 1):
        n = len(current.candidates)
        best = np.full(n, np.inf)
        link_frame = np.full(n, -1, dtype=int)
        link_node = np.full(n, -1, dtype=int)
        indexes = [lineage.get((a.frame+i, track)) for track in preferred]
        const = max(scale, .2)
        penalties = []
        for node in set(indexes) - {None}:
            delta = np.linalg.norm(current.centers-current.centers[node], axis=1)
            penalties.append(.3*np.minimum(1.6, delta/max(18*const, current.diagonal[node])))
        bias = np.min(penalties, axis=0) if penalties else np.zeros(n)
        for gap in range(1, min(max_gap, i)+1):
            prev = section[i-gap]
            if not n or not prev.candidates:
                continue
            dist = np.linalg.norm(current.centers[None, :, :]-prev.centers[:, None, :], axis=2)
            prediction = prev.centers + gap*prev.following
            residual = np.linalg.norm(current.centers[None, :, :]-prediction[:, None, :], axis=2)
            size = np.sqrt(prev.diagonal[:, None]*current.diagonal[None, :])
            change = np.abs(current.log_area[None, :]-prev.log_area[:, None])
            edge = (.35*dist + .9*residual)/np.maximum(22*const, size) + .13*change + .34*(gap-1) + bias[None, :]
            invalid = (dist > np.minimum(44*const, 10*const+.45*size)*gap) | (change > 1.22+.17*gap)
            if gap == 1:
                invalid |= residual > np.maximum(32*const, .60*size)
            edge[invalid] = np.inf
            cost = costs[i-gap][0][:, None] + edge
            parent = cost.argmin(axis=0)
            value = cost[parent, np.arange(n)]
            improve = value < best
            best[improve], link_frame[improve], link_node[improve] = value[improve], i-gap, parent[improve]
        costs.append((best, link_frame, link_node))
    steps = len(section)-1
    if costs[-1][0][b.node] / steps > .19:
        return []
    pos, node = steps, b.node
    traced = [(a.frame+pos, node)]
    while pos:
        _, prev, nodes = costs[pos]
        pos, node = prev[node], nodes[node]
        traced.append((a.frame+pos, node))
    if steps > 2.2*fps:
        # Long links need consecutive foreground support from the two original motion tracklets.
        preferred_count = sum(any(lineage.get((f, track)) == node for track in preferred) for f, node in traced)
        if preferred_count < .70 * (steps+1):
            return []
    return traced[::-1]


def link_sightings(sightings, frames, lineage, fps, scale, limit):
    """Link separate sightings once each, in chronological order."""
    incoming = set()
    group = {a: i+1 for i, a in enumerate(sightings)}
    paths = []
    for index, a in enumerate(sightings):
        for b in sightings[index+1:index+5]:
            delta = b.frame-a.frame
            if delta < max(1, round(.16*fps)) or b in incoming:
                continue
            if delta > limit*fps:
                break
            path = solve_bridge(frames, a, b, lineage, fps, scale)
            if path:
                incoming.add(b)
                group[b] = group[a]
                paths.append((a, b, path))
                break
    return paths, group


def continuity_results(tracks, history, fps, threshold, window, bridge_seconds, tail_seconds):
    frames = history.frames
    nodes = [{id(c): i for i, c in enumerate(frame.candidates)} if frame else {} for frame in frames]
    lineage = {(f, track.id): nodes[f][id(c)] for track in tracks for f, c in track.observations}
    representatives = []
    track_sightings = {}
    for track in tracks:
        hits = badge_sightings(track, threshold, fps, window)
        if not hits:
            continue
        track_sightings[track.id] = hits
        cluster = []
        for frame, cand in hits:
            if not cluster or frame-cluster[0].frame > .24*fps:
                cluster = []
                representatives.append(cluster)
            cluster.append(Sighting(frame, nodes[frame][id(cand)], track.id, cand.badge_score))
    if not representatives:
        return {}, 0
    seeds = sorted((max(cluster, key=lambda a: a.score) for cluster in representatives), key=lambda a: a.frame)
    scale = history.scale[0]*history.width/480
    paths, groups = link_sightings(seeds, frames, lineage, fps, scale, bridge_seconds)
    labels = defaultdict(dict)

    def put(frame, group, point, evidence, box, score=None):
        x, y = point
        x = round(float(np.clip(x, 0, history.width-1)), 1)
        y = round(float(np.clip(y, 0, history.height-1)), 1)
        if box and not (box[0] <= x < box[2] and box[1] <= y < box[3]):
            box = None
        labels[frame][f'auto_{group}'] = (x, y, evidence, box, score)

    # One-sided support is limited to the existing foreground track. Longer paths require a badge at both ends.
    for track in tracks:
        if track.id not in track_sightings:
            continue
        support = [s for s in seeds if s.track == track.id]
        direct = dict(track_sightings[track.id])
        for frame, candidate in track.observations:
            seed = min(support, key=lambda s: abs(s.frame-frame))
            gap = abs(seed.frame-frame)
            if gap <= tail_seconds*fps or frame in direct:
                put(frame, groups[seed], candidate.center, 'auto_track', candidate.box)

    for a, b, path in paths:
        for (f, j), (next_f, next_j) in zip(path, path[1:]):
            body, neighbor = frames[f].candidates[j], frames[next_f].candidates[next_j]
            put(f, groups[a], body.center, 'auto_bridge', body.box)
            for fi in range(f+1, next_f):
                t = (fi-f)/(next_f-f)
                point = tuple(x+t*(y-x) for x, y in zip(body.center, neighbor.center))
                put(fi, groups[a], point, 'auto_bridge_gap', None)
        last = frames[b.frame].candidates[b.node]
        put(b.frame, groups[b], last.center, 'auto_bridge', last.box)

    for track, hits in track_sightings.items():
        support = [s for s in seeds if s.track == track]
        for frame, cand in hits:
            seed = min(support, key=lambda s: abs(s.frame-frame))
            # The matched badge is a closer torso landmark than the whole foreground box center.
            put(frame, groups[seed], cand.badge_xy, 'auto_badge', cand.box, cand.badge_score)
    # Two paths can track the same body through adjacent sightings. Keep the strongest evidence for overlapping locations.
    priorities = {'auto_badge': 3, 'auto_bridge': 2, 'auto_track': 1, 'auto_bridge_gap': 0}
    radius = .045*np.hypot(history.width, history.height)
    for frame_labels in labels.values():
        retained = []
        for staff_id, loc in sorted(frame_labels.items(), key=lambda item: priorities[item[1][2]], reverse=True):
            if any(np.hypot(loc[0]-x, loc[1]-y) < radius for x, y in retained):
                del frame_labels[staff_id]
            else:
                retained.append(loc[:2])
    return labels, len(paths)
