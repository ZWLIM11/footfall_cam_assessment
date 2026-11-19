import { readCsv } from './records.js';

function indexFor(row, frames) {
  const index = Number(row.frame_index);
  if (!String(row.frame_index ?? '').trim() || !Number.isInteger(index) || !frames[index]) {
    throw new Error('A reference frame index is outside the current clip.');
  }
  const time = row.time_seconds?.trim();
  if (time && (!Number.isFinite(Number(time)) || Math.abs(Number(time) - frames[index].time) > .01)) {
    throw new Error(`The timestamp for reference frame ${index} differs from the current clip.`);
  }
  return index;
}

function centerFor(row) {
  const x = row.x_center?.trim(), y = row.y_center?.trim();
  if (!x && !y) return undefined;
  if (!x || !y || ![x, y].every(value => Number.isFinite(Number(value)) && Number(value) >= 0)) {
    throw new Error('Enter both XY values as non-negative pixels, or leave both blank.');
  }
  return { x: Number(x), y: Number(y) };
}

export function referenceLabels(frameCsv, xyCsv, frames) {
  const { header, rows } = readCsv(frameCsv);
  if (!['frame_index', 'staff_present'].every(key => header.includes(key))) {
    throw new Error('The reference CSV needs frame_index and staff_present columns.');
  }
  const reviewExport = header.includes('status');
  if (reviewExport && rows.length !== frames.length) {
    throw new Error('The reviewed export and current clip have different frame counts. Use labels from the same clip.');
  }
  const labels = new Map();
  for (const row of rows) {
    if (reviewExport && row.review_status !== 'reviewed' && row.status !== 'reviewed_no_staff') continue;
    const flag = row.staff_present.trim();
    const center = centerFor(row);
    if (!reviewExport && !flag && !center) continue;
    if (!['0', '1'].includes(flag) || (center && flag === '0')) {
      throw new Error('Use 1 for verified staff and 0 for verified no staff. Add XY only for staff frames.');
    }
    const index = indexFor(row, frames);
    if (labels.has(index)) throw new Error(`Reference frame ${index} appears more than once.`);
    labels.set(index, { present: flag === '1', center });
  }
  if (!labels.size) throw new Error('No verified frames found. Fill in the label template or export frames marked in the review queue.');
  if (xyCsv) {
    const { header: xyHeader, rows: xyRows } = readCsv(xyCsv);
    if (!['frame_index', 'evidence', 'x_center', 'y_center'].every(key => xyHeader.includes(key))) {
      throw new Error('The optional coordinates CSV must be the reviewed coordinates export.');
    }
    for (const row of xyRows) {
      if (row.evidence !== 'manual_review') continue;
      const index = indexFor(row, frames);
      const label = labels.get(index);
      if (!label?.present) throw new Error(`The reviewed coordinate on frame ${index} needs a matching staff label.`);
      if (label.center) throw new Error(`Use one verified XY center for frame ${index}.`);
      label.center = centerFor(row);
      if (!label.center) throw new Error(`Frame ${index} is missing its reviewed XY center.`);
    }
  }
  return labels;
}

export function measureBenchmark(frames, labels) {
  const counts = { tp: 0, tn: 0, fp: 0, fn: 0 };
  const distances = [];
  let uncertain = 0, labeledXY = 0, within60 = 0;
  const rows = Array.from(labels).sort(([a], [b]) => a - b).map(([index, label]) => {
    const frame = frames[index];
    const positive = frame.status === 'present';
    const outcome = label.present ? (positive ? 'tp' : 'fn') : (positive ? 'fp' : 'tn');
    counts[outcome]++;
    if (frame.status === 'uncertain') uncertain++;
    let distance = null;
    if (label.center) {
      labeledXY++;
      if (positive && frame.points.length) {
        distance = frame.points.reduce((nearest, point) =>
          Math.min(nearest, Math.hypot(point.x - label.center.x, point.y - label.center.y)), Infinity);
        distances.push(distance);
        if (distance <= 60) within60++;
      }
    }
    return { index, time: frame.time, present: label.present, prediction: frame.status, outcome, distance };
  });
  const bothClasses = counts.tp + counts.fn > 0 && counts.tn + counts.fp > 0;
  const ratio = (numerator, denominator) => denominator ? numerator / denominator : null;
  distances.sort((a, b) => a - b);
  const middle = Math.floor(distances.length / 2);
  return {
    total: frames.length, labeled: rows.length, counts, uncertain, rows, labeledXY, within60,
    pairedXY: distances.length,
    medianError: distances.length ? (distances[middle] + distances[Math.ceil(distances.length / 2) - 1]) / 2 : null,
    precision: bothClasses ? ratio(counts.tp, counts.tp + counts.fp) : null,
    recall: ratio(counts.tp, counts.tp + counts.fn),
    f1: bothClasses ? ratio(2 * counts.tp, 2 * counts.tp + counts.fp + counts.fn) : null,
    bothClasses,
  };
}

export function labelTemplate(frames) {
  return 'frame_index,time_seconds,staff_present,x_center,y_center\n' +
    frames.map((frame, index) => `${index},${frame.time.toFixed(6)},,,`).join('\n') + '\n';
}

export function benchmarkCsv(rows) {
  return 'frame_index,time_seconds,verified_staff_present,detector_status,outcome,xy_error_px\n' +
    rows.map(row => [row.index, row.time.toFixed(6), Number(row.present), row.prediction, row.outcome,
      row.distance === null ? '' : row.distance.toFixed(3)].join(',')).join('\n') + '\n';
}
