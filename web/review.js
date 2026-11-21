import { frameCsvWithCounts, readCsv, readResults, writeCsv } from './records.js';

export const evidenceLabels = {
  auto_badge: 'Badge match',
  auto_bridge: 'Linked track',
  auto_track: 'Track extension',
  auto_bridge_gap: 'Gap estimate',
  manual_review: 'Manual review',
};

export function coverageFor(frames) {
  const points = { auto_badge: 0, auto_bridge: 0, auto_track: 0, auto_bridge_gap: 0, manual_review: 0, other: 0 };
  const counts = { pending: [], flagged: 0, reviewed: 0, located: 0, points };
  frames.forEach((frame, index) => {
    if (frame.status === 'uncertain') counts.pending.push(index);
    if (frame.status === 'present') counts.flagged++;
    if (frame.reviewed) counts.reviewed++;
    if (frame.points.length) counts.located++;
    for (const { evidence } of frame.points) {
      const key = Object.hasOwn(evidenceLabels, evidence) ? evidence : 'other';
      points[key]++;
    }
  });
  return counts;
}

export function pointAtPointer(x, y, rect, width, height) {
  const scale = Math.min(rect.width / width, rect.height / height);
  if (!(scale > 0)) return undefined;
  const frameX = (x - rect.left - (rect.width - width * scale) / 2) / scale;
  const frameY = (y - rect.top - (rect.height - height * scale) / 2) / scale;
  if (frameX < 0 || frameX >= width || frameY < 0 || frameY >= height) return undefined;
  return { x: Math.round(Math.min(width - 1, frameX) * 10) / 10, y: Math.round(Math.min(height - 1, frameY) * 10) / 10 };
}

export function setReviewPoint(frame, target, id, x, y, width, height) {
  const index = target ?? frame.points.length;
  const existing = frame.points[index];
  if (!existing?.id && !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$/.test(id)) {
    throw new Error('Enter a staff ID with up to 50 letters, numbers, dots, dashes or underscores.');
  }
  if (!Number.isInteger(index) || index < 0 || index > frame.points.length || ![x, y].every(Number.isFinite) || x < 0 || y < 0 || x >= width || y >= height) {
    throw new Error('Enter XY coordinates inside the frame.');
  }
  const staffId = existing?.id || id;
  if (frame.points.some((point, i) => i !== index && point.id === staffId)) {
    throw new Error('This staff ID is already on the frame. Select it to change its point.');
  }
  const points = [...frame.points];
  points[index] = { id: staffId, x, y, evidence: 'manual_review' };
  return { ...frame, status: 'present', reviewed: true, points, staffCount: points.length };
}

function addColumns(header, names) {
  return [...new Set([...header, ...names])];
}

export function reviewedExports(frameCsv, xyCsv, edits, originalFrames = readResults(frameCsv, xyCsv)) {
  const withCounts = frameCsvWithCounts(frameCsv, originalFrames);
  if (!edits.size) return { frameCsv: withCounts, xyCsv };
  const { header: frameHeader, rows: frameRows } = readCsv(withCounts);
  const { header: xyHeader, rows: xyRows } = readCsv(xyCsv);
  const untouched = [], originalByFrame = new Map();
  for (const row of xyRows) {
    const index = Number(row.frame_index);
    if (!edits.has(index)) untouched.push(row);
    else {
      if (!originalByFrame.has(index)) originalByFrame.set(index, []);
      originalByFrame.get(index).push(row);
    }
  }
  for (const [index, { points }] of edits) {
    const row = frameRows[index];
    row.detector_status ||= row.status;
    row.staff_present = points.length ? 1 : 0;
    row.status = points.length ? 'present' : 'reviewed_no_staff';
    row.staff_ids = points.map(point => point.id).join('|');
    row.staff_count = points.length;
    row.review_status = 'reviewed';
    const originals = originalByFrame.get(index) ?? [];
    for (const point of points) {
      const original = originals.find(candidate => candidate.staff_id === point.id);
      if (point.evidence !== 'manual_review' && original) {
        untouched.push(original);
        continue;
      }
      if (original && original.evidence === 'manual_review' &&
          point.x === Number(original.x_center) && point.y === Number(original.y_center)) {
        untouched.push(original);
        continue;
      }
      const previousEvidence = original?.evidence || original?.method;
      const corrected = {
        ...original, frame_index: index, time_seconds: row.time_seconds, staff_id: point.id,
        x_center: point.x, y_center: point.y, evidence: 'manual_review',
        detector_evidence: original?.detector_evidence || (previousEvidence === 'manual_review' ? '' : previousEvidence),
      };
      for (const field of ['x1', 'y1', 'x2', 'y2', 'badge_score']) delete corrected[field];
      if (xyHeader.includes('method')) corrected.method = 'manual_review';
      untouched.push(corrected);
    }
  }
  untouched.sort((a, b) => Number(a.frame_index) - Number(b.frame_index));
  return {
    frameCsv: writeCsv(addColumns(frameHeader, ['staff_ids', 'review_status', 'detector_status']), frameRows),
    xyCsv: writeCsv(addColumns(xyHeader, ['time_seconds', 'evidence', 'detector_evidence']), untouched),
  };
}
