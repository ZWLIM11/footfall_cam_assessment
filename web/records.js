export function readCsv(source) {
  const text = source.replace(/^\uFEFF/, '');
  const rows = [];
  let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i++; }
      else quoted = !quoted;
    } else if (!quoted && (char === ',' || char === '\n' || char === '\r')) {
      row.push(cell); cell = '';
      if (char !== ',') {
        if (char === '\r' && text[i + 1] === '\n') i++;
        if (row.some(Boolean)) rows.push(row);
        row = [];
      }
    } else cell += char;
  }
  if (quoted) throw new Error('The CSV has an unclosed quoted field.');
  if (cell || row.length) rows.push([...row, cell]);
  const header = rows.shift();
  if (!header || new Set(header).size !== header.length || rows.some(values => values.length !== header.length)) {
    throw new Error('The CSV header and rows do not match.');
  }
  return { header, rows: rows.map(values => Object.fromEntries(header.map((key, i) => [key, values[i]]))) };
}

export function writeCsv(header, rows) {
  const cell = value => {
    const string = String(value ?? '');
    return /[",\r\n]/.test(string) ? `"${string.replaceAll('"', '""')}"` : string;
  };
  return [header.map(cell).join(','), ...rows.map(row => header.map(key => cell(row[key])).join(','))].join('\r\n') + '\r\n';
}

function number(value) {
  return value === undefined || value.trim() === '' ? NaN : Number(value);
}

export function readResults(frameCsv, xyCsv) {
  const framesFile = readCsv(frameCsv);
  const pointsFile = readCsv(xyCsv);
  if (!['frame_index', 'time_seconds', 'staff_present', 'status'].every(key => framesFile.header.includes(key)) ||
      !['frame_index', 'staff_id', 'x_center', 'y_center'].every(key => pointsFile.header.includes(key))) {
    throw new Error('Choose staff_frames.csv and staff_coordinates.csv from the same detector run.');
  }
  let lastTime = -1;
  const frames = framesFile.rows.map((row, i) => {
    const time = number(row.time_seconds);
    if (number(row.frame_index) !== i || !Number.isFinite(time) || time < 0 || time <= lastTime || !['0', '1'].includes(row.staff_present)) {
      throw new Error('Frame indexes must be consecutive and timestamps must increase.');
    }
    lastTime = time;
    return {
      time,
      status: row.staff_present === '1' ? 'present' : row.status === 'uncertain' ? 'uncertain' : 'empty',
      reviewed: row.review_status === 'reviewed' || row.status === 'reviewed_no_staff',
      points: [],
    };
  });
  if (!frames.length) throw new Error('The frames CSV is empty.');
  for (const row of pointsFile.rows) {
    const index = number(row.frame_index), x = number(row.x_center), y = number(row.y_center);
    if (!Number.isInteger(index) || frames[index]?.status !== 'present' || !Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0) {
      throw new Error('A coordinate row does not match the frame flags or contains invalid XY values.');
    }
    if (row.staff_id && frames[index].points.some(point => point.id === row.staff_id)) {
      throw new Error(`A staff ID appears twice on frame ${index}.`);
    }
    const badgeScore = number(row.badge_score);
    frames[index].points.push({
      id: row.staff_id, x, y, evidence: row.evidence || row.method || 'Imported coordinate',
      badgeScore: Number.isFinite(badgeScore) ? badgeScore : undefined,
    });
  }
  frames.forEach((frame, index) => {
    const row = framesFile.rows[index];
    const ids = new Set(row.staff_ids?.split('|').filter(Boolean));
    const minimum = Math.max(ids.size, frame.points.length);
    if (row.staff_count?.trim()) {
      const count = number(row.staff_count);
      if (!Number.isSafeInteger(count) || count < minimum || row.staff_present !== String(Number(count > 0))) {
        throw new Error(`Staff count disagrees with the flags or detections on frame ${index}.`);
      }
      frame.staffCount = count;
    } else {
      frame.staffCount = frame.status === 'present' ? minimum || null : 0;
    }
  });
  return frames;
}

export function staffCountFor(frame) {
  return frame.status === 'present' ? frame.staffCount ?? (frame.points.length || null) : 0;
}

export function frameCsvWithCounts(frameCsv, frames) {
  const { header, rows } = readCsv(frameCsv);
  const counts = frames.map(frame => String(staffCountFor(frame) ?? ''));
  if (header.includes('staff_count') && rows.every((row, index) => row.staff_count === counts[index])) return frameCsv;
  if (!header.includes('staff_count')) header.push('staff_count');
  rows.forEach((row, index) => { row.staff_count = counts[index]; });
  return writeCsv(header, rows);
}

export function eventsFor(frames) {
  const events = [];
  for (let i = 0; i < frames.length; i++) {
    const { status } = frames[i];
    if (status === 'empty') continue;
    const last = events.at(-1);
    if (last?.status === status && last.end === i - 1) last.end = i;
    else events.push({ status, start: i, end: i });
  }
  return events;
}

export function frameAt(frames, time) {
  let low = 0, high = frames.length;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (frames[mid].time <= time + 0.00001) low = mid + 1;
    else high = mid;
  }
  return Math.max(0, low - 1);
}

export function makeDemo() {
  const flagged = [[24, 70], [84, 127], [158, 204], [226, 258]], dim = [[73, 79], [149, 153], [269, 276]];
  const active = (segments, n) => segments.some(([start, end]) => n >= start && n <= end);
  const frames = Array.from({ length: 300 }, (_, i) => {
    const present = active(flagged, i);
    const x = 460 + 38 * Math.sin(i / 27), y = 274 + 157 * Math.sin(i / 37 - .6);
    return { time: i / 12.5, status: present ? 'present' : active(dim, i) ? 'uncertain' : 'empty',
      points: present ? [{ id: 'demo_1', x, y, evidence: i % 19 === 0 ? 'auto_badge' : 'auto_bridge' }] : [] };
  });
  const flag = ['frame_index,time_seconds,staff_present,status,staff_ids,staff_count'];
  const xy = ['frame_index,time_seconds,staff_id,x_center,y_center,evidence'];
  frames.forEach(({ time, status, points }, i) => {
    flag.push([i, time.toFixed(6), Number(status === 'present'), status === 'empty' ? 'not_detected' : status, points.map(point => point.id).join('|'), points.length].join(','));
    for (const point of points) xy.push([i, time.toFixed(6), point.id, point.x.toFixed(1), point.y.toFixed(1), point.evidence].join(','));
  });
  return { frames, frameCsv: flag.join('\n') + '\n', xyCsv: xy.join('\n') + '\n' };
}
