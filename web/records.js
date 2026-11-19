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
    return { time, status: row.staff_present === '1' ? 'present' : row.status === 'uncertain' ? 'uncertain' : 'empty', points: [] };
  });
  if (!frames.length) throw new Error('The frames CSV is empty.');
  for (const row of pointsFile.rows) {
    const index = number(row.frame_index), x = number(row.x_center), y = number(row.y_center);
    if (!Number.isInteger(index) || frames[index]?.status !== 'present' || !Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0) {
      throw new Error('A coordinate row does not match the frame flags or contains invalid XY values.');
    }
    frames[index].points.push({ id: row.staff_id, x, y, evidence: row.evidence || row.method || 'Imported coordinate' });
  }
  return frames;
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
  const flag = ['frame_index,time_seconds,staff_present,status,staff_ids'];
  const xy = ['frame_index,time_seconds,staff_id,x_center,y_center,evidence'];
  frames.forEach(({ time, status, points }, i) => {
    flag.push([i, time.toFixed(6), Number(status === 'present'), status === 'empty' ? 'not_detected' : status, points[0]?.id || ''].join(','));
    for (const point of points) xy.push([i, time.toFixed(6), point.id, point.x.toFixed(1), point.y.toFixed(1), point.evidence].join(','));
  });
  return { frames, frameCsv: flag.join('\n') + '\n', xyCsv: xy.join('\n') + '\n' };
}
