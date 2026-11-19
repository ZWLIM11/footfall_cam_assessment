import { readResults, eventsFor, frameAt, makeDemo } from './records.js';
import { parseBadgePack } from './badge-pack.js';

const $ = id => document.getElementById(id);
const video = $('video'), preview = $('preview'), brush = preview.getContext('2d');
const timeline = $('timeline'), tracks = timeline.getContext('2d');
const colors = { present: '#258861', uncertain: '#d49748', empty: '#d8e3df' };
const statusText = { present: 'Staff flagged', uncertain: 'Uncertain', empty: 'No flag' };
const evidenceText = { auto_badge: 'Badge evidence', auto_bridge: 'Linked track', auto_bridge_gap: 'Short gap estimate', auto_track: 'Track extension' };
const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
let dataset, index = 0, objectUrl, playing = false, animation = 0, started, startedAt;
let analysis = { enabled: false, requires_key: false };
let privateBadges, cancelBrowserRun;
const browserMaxBytes = 60 * 1024 * 1024;

function stamp(seconds) {
  const centis = Math.round(seconds * 100);
  return `${String(Math.floor(centis / 6000)).padStart(2, '0')}:${String((centis % 6000) / 100 | 0).padStart(2, '0')}.${String(centis % 100).padStart(2, '0')}`;
}

function construct(tag, value, className) {
  const el = document.createElement(tag);
  el.textContent = value;
  if (className) el.className = className;
  return el;
}

function notice(message = '') { $('activity').hidden = !message; $('activity').textContent = message; }

function drawScene(w, h) {
  brush.fillStyle = '#15332a'; brush.fillRect(0, 0, w, h);
  brush.strokeStyle = '#284b3e'; brush.lineWidth = Math.max(1, w / 900);
  const grid = Math.max(24, w / 20);
  brush.beginPath();
  for (let x = 0; x <= w; x += grid) { brush.moveTo(x, 0); brush.lineTo(x, h); }
  for (let y = 0; y <= h; y += grid) { brush.moveTo(0, y); brush.lineTo(w, y); }
  brush.stroke();
  if (!dataset.demo) return;
  brush.fillStyle = '#214236'; brush.fillRect(355, 0, 210, h);
  brush.setLineDash([10, 9]); brush.strokeStyle = '#4b6d5e';
  brush.beginPath(); brush.moveTo(460, 18); brush.lineTo(460, h - 18); brush.stroke(); brush.setLineDash([]);
  for (const left of [66, 650]) {
    for (const top of [69, 230, 391]) {
      brush.fillStyle = '#36554a'; brush.beginPath(); brush.roundRect(left, top, 235, 90, 8); brush.fill();
      brush.fillStyle = '#658477'; brush.fillRect(left + 36, top + 20, 55, 36); brush.fillRect(left + 142, top + 20, 55, 36);
      brush.fillStyle = '#213d33'; brush.fillRect(left + 43, top + 25, 41, 26); brush.fillRect(left + 149, top + 25, 41, 26);
      brush.fillStyle = '#4d6e61'; brush.fillRect(left + 162, top + 95, 38, 20);
    }
  }
  for (const [x, y] of [[173,205],[226,346],[711,178],[842,372]]) {
    brush.fillStyle = '#8eaa9c'; brush.beginPath(); brush.ellipse(x, y, 14, 20, .2, 0, Math.PI * 2); brush.fill();
  }
  const t = dataset.frames[index].points[0];
  if (t) {
    brush.fillStyle = '#4a8a71'; brush.beginPath(); brush.ellipse(t.x, t.y, 17, 25, .3, 0, Math.PI * 2); brush.fill();
  }
}

function drawFrame(frame) {
  const w = preview.width, h = preview.height;
  brush.clearRect(0, 0, w, h);
  if (video.hidden) drawScene(w, h);
  const unit = Math.max(1, w / 700);
  for (const point of frame.points) {
    if (point.x >= w || point.y >= h) continue;
    const radius = 11 * unit;
    brush.strokeStyle = '#101f18'; brush.lineWidth = 5 * unit;
    const marker = () => { brush.beginPath(); brush.moveTo(point.x - radius, point.y); brush.lineTo(point.x + radius, point.y); brush.moveTo(point.x, point.y - radius); brush.lineTo(point.x, point.y + radius); brush.stroke(); };
    marker(); brush.strokeStyle = '#8af5bd'; brush.lineWidth = 2.5 * unit; marker();
    brush.font = `600 ${12 * unit}px system-ui`;
    const txt = point.id; const tw = brush.measureText(txt).width + 12 * unit;
    const x = Math.max(0, Math.min(w - tw, point.x + 12 * unit)), y = Math.max(0, point.y - 26 * unit);
    brush.fillStyle = '#102d24'; brush.fillRect(x, y, tw, 19 * unit);
    brush.fillStyle = '#cbffe0'; brush.fillText(txt, x + 6 * unit, y + 13 * unit);
  }
}

function drawTimeline() {
  const w = timeline.width, h = timeline.height;
  tracks.fillStyle = colors.empty; tracks.fillRect(0, 0, w, h);
  dataset.events.forEach(event => {
    const start = w * event.start / dataset.frames.length, end = w * (event.end + 1) / dataset.frames.length;
    tracks.fillStyle = colors[event.status]; tracks.fillRect(start, 0, Math.max(1, end - start), h);
  });
}

function renderEvents() {
  const list = $('event-list'), filter = $('event-filter').value;
  list.replaceChildren();
  for (const event of dataset.events) {
    if (filter !== 'all' && filter !== event.status) continue;
    const button = construct('button', '', 'event'); button.type = 'button';
    button.dataset.start = event.start; button.dataset.end = event.end;
    const group = construct('span', '');
    group.append(construct('b', statusText[event.status]), document.createElement('br'),
      construct('small', `${event.start}–${event.end} · ${event.end - event.start + 1} frames`));
    button.append(group, construct('span', stamp(dataset.frames[event.start].time), 'mono'));
    list.append(button);
  }
  if (!list.children.length) list.append(construct('p', 'No matching events.'));
  highlightEvent();
}

function highlightEvent() {
  for (const button of $('event-list').querySelectorAll('button')) {
    if (+button.dataset.start <= index && index <= +button.dataset.end) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  }
}

function showFrame(next, seekVideo = false) {
  index = Math.max(0, Math.min(dataset.frames.length - 1, next));
  const frame = dataset.frames[index];
  if (seekVideo && !video.hidden && video.readyState) video.currentTime = Math.min(frame.time, video.duration);
  $('seek').value = index;
  $('seek').setAttribute('aria-valuetext', `Frame ${index}, ${stamp(frame.time)}`);
  $('time').textContent = stamp(frame.time);
  $('frame-label').textContent = `Frame ${index} of ${dataset.frames.length - 1} · ${stamp(frame.time)}`;
  $('frame-status').className = `badge ${frame.status}`;
  $('frame-status').textContent = statusText[frame.status];
  const people = $('people'); people.replaceChildren();
  for (const point of frame.points) {
    const row = construct('div', '', 'point-row');
    row.append(construct('strong', point.id || 'Staff'), construct('span', `(${fmt.format(point.x)}, ${fmt.format(point.y)}) px`, 'mono'),
      construct('span', evidenceText[point.evidence] || point.evidence.replaceAll('_', ' '), 'evidence'));
    people.append(row);
  }
  if (!frame.points.length) people.textContent = 'No staff coordinate for this frame.';
  drawFrame(frame); highlightEvent();
}

function configureDimensions(w, h) {
  preview.width = Math.ceil(w); preview.height = Math.ceil(h);
  document.querySelector('.view').style.aspectRatio = `${w} / ${h}`;
}

function pause() {
  playing = false; cancelAnimationFrame(animation); video.pause(); $('play').textContent = 'Play';
}

function load(data, source, file, demo = false) {
  pause();
  dataset = { ...data, demo, events: eventsFor(data.frames) };
  video.removeAttribute('src'); video.load(); video.hidden = true;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = undefined;
  let width = 960, height = 540;
  if (!demo && !file) for (const frame of data.frames) for (const p of frame.points) {
    width = Math.min(8192, Math.max(width, p.x + 30)); height = Math.min(8192, Math.max(height, p.y + 30));
  }
  configureDimensions(width, height);
  $('preview-caption').textContent = demo ? 'Illustrative overhead scene' : 'Coordinates only';
  $('preview-caption').hidden = Boolean(file);
  if (file) { objectUrl = URL.createObjectURL(file); video.src = objectUrl; video.hidden = false; }
  $('source-name').textContent = source;
  $('total').textContent = fmt.format(data.frames.length);
  $('positives').textContent = fmt.format(data.frames.filter(f => f.status === 'present').length);
  $('uncertain').textContent = fmt.format(data.frames.filter(f => f.status === 'uncertain').length);
  $('intervals').textContent = fmt.format(dataset.events.filter(e => e.status === 'present').length);
  $('seek').max = data.frames.length - 1;
  $('event-filter').value = 'all';
  $('event-list').scrollTop = 0;
  drawTimeline(); renderEvents(); showFrame(demo ? 38 : 0);
}

function tick(now) {
  if (!playing) return;
  const t = video.hidden ? startedAt + (now - started) / 1000 : video.currentTime;
  const next = frameAt(dataset.frames, t);
  if (next !== index) showFrame(next);
  if (video.hidden && index === dataset.frames.length - 1) pause();
  else animation = requestAnimationFrame(tick);
}

function saveCsv(value, filename) {
  const href = URL.createObjectURL(new Blob([value], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a'); a.href = href; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

for (const close of document.querySelectorAll('.dismiss')) close.addEventListener('click', () => close.closest('dialog').close());
$('demo-button').addEventListener('click', () => load(makeDemo(), 'Synthetic demo', null, true));
$('import-button').addEventListener('click', () => { $('import-error').hidden = true; $('import-dialog').showModal(); });
$('event-filter').addEventListener('change', renderEvents);
$('event-list').addEventListener('click', e => {
  const button = e.target.closest('button'); if (button) { pause(); showFrame(+button.dataset.start, true); }
});
$('seek').addEventListener('input', e => { pause(); showFrame(+e.target.value, true); });
$('export-frames').addEventListener('click', () => saveCsv(dataset.frameCsv, 'staff_frames.csv'));
$('export-xy').addEventListener('click', () => saveCsv(dataset.xyCsv, 'staff_coordinates.csv'));
video.addEventListener('loadedmetadata', () => {
  configureDimensions(video.videoWidth, video.videoHeight); showFrame(index, true);
  const last = dataset.frames.at(-1).time;
  if (Math.abs(video.duration - last) > 1) notice('The video duration differs from the CSV timeline. Check that the files belong to the same recording.');
});
video.addEventListener('ended', pause);
video.addEventListener('error', () => {
  if (!objectUrl || video.hidden) return;
  pause(); video.hidden = true; $('preview-caption').hidden = false; $('preview-caption').textContent = 'Coordinates only';
  drawFrame(dataset.frames[index]); notice('This browser could not play the video. The imported coordinates and CSVs are still available.');
});
$('play').addEventListener('click', async () => {
  if (playing) { pause(); return; }
  if (index === dataset.frames.length - 1) showFrame(0, true);
  if (!video.hidden) {
    try { await video.play(); } catch { notice('The browser could not play this video. Try an H.264 MP4.'); return; }
  }
  playing = true; $('play').textContent = 'Pause';
  started = performance.now(); startedAt = dataset.frames[index].time; animation = requestAnimationFrame(tick);
});

$('import-form').addEventListener('submit', async e => {
  e.preventDefault();
  const error = $('import-error'); error.hidden = true;
  try {
    const framesFile = $('frames-file').files[0], xyFile = $('xy-file').files[0], file = $('video-file').files[0];
    const [frameCsv, xyCsv] = await Promise.all([framesFile.text(), xyFile.text()]);
    load({ frames: readResults(frameCsv, xyCsv), frameCsv, xyCsv }, file?.name || 'Imported detector results', file);
    $('import-dialog').close(); e.target.reset();
  } catch (problem) { error.textContent = problem.message; error.hidden = false; }
});

async function api(path, request) {
  const result = await fetch(path, request);
  if (!result.headers.get('content-type')?.includes('application/json')) throw new Error('The analysis service did not return data.');
  const data = await result.json();
  if (!result.ok) throw new Error(data.error || 'The analysis request failed.');
  return data;
}

function switchRunMode() {
  const hosted = $('run-mode').value === 'server';
  $('badge-label').hidden = hosted;
  $('key-label').hidden = !hosted || !analysis.requires_key;
  $('access-key').required = hosted && analysis.requires_key;
  $('analyze-note').textContent = hosted
    ? analysis.requires_key ? 'Your MP4 is sent to the configured analysis container.' : 'Run the Python detector on this machine.'
    : 'The detector runs in your browser. The MP4 and badge pack stay on this device.';
}

function runInBrowser(file, badges) {
  return new Promise((resolve, reject) => {
    const detector = new Worker(new URL('./browser-detector.js', import.meta.url), { type: 'module' });
    const finish = (result, error) => {
      detector.terminate(); cancelBrowserRun = undefined; $('cancel-analysis').hidden = true;
      if (error) reject(error); else resolve(result);
    };
    cancelBrowserRun = () => finish(null, new Error('Analysis canceled.'));
    $('cancel-analysis').hidden = false;
    detector.onmessage = ({ data }) => {
      if (data.type === 'progress') notice(data.text);
      else if (data.type === 'done') finish(data);
      else if (data.type === 'error') finish(null, new Error(data.text));
    };
    detector.onerror = () => finish(null, new Error('The browser detector could not start. Reload and try again in a recent desktop browser.'));
    try { detector.postMessage({ file, badges }); }
    catch (error) { finish(null, error); }
  });
}

async function runOnServer(file, headers) {
  const job = await api('/api/jobs', { method: 'POST', body: file, headers: { ...headers, 'Content-Type': 'video/mp4' } });
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 1600));
    const update = await api(`/api/jobs/${job.id}`, { headers });
    if (update.state === 'failed') throw new Error(update.error);
    if (update.state === 'done') {
      const { frames_csv: frameCsv, coordinates_csv: xyCsv } = await api(`/api/jobs/${job.id}/results`, { headers });
      return { frameCsv, xyCsv };
    }
    if (update.progress) notice(update.progress);
  }
}

$('run-mode').addEventListener('change', switchRunMode);
$('cancel-analysis').addEventListener('click', () => cancelBrowserRun?.());
$('analyze-button').addEventListener('click', () => {
  $('analyze-error').hidden = true;
  switchRunMode();
  $('analyze-dialog').showModal();
});
$('analyze-form').addEventListener('submit', async e => {
  e.preventDefault();
  const error = $('analyze-error'), file = $('analyze-file').files[0];
  const hosted = $('run-mode').value === 'server';
  const key = $('access-key').value.trim();
  const headers = key ? { Authorization: `Bearer ${key}` } : {};
  const submit = e.target.querySelector('[type=submit]');
  submit.disabled = true; $('analyze-button').disabled = true; error.hidden = true;
  try {
    if (!file?.size || file.size > (hosted ? analysis.max_bytes : browserMaxBytes)) throw new Error('Choose a non-empty MP4 up to 60 MiB.');
    if (!hosted) {
      const packFile = $('badge-file').files[0];
      if (packFile) {
        if (packFile.size > 50_000) throw new Error('Choose the small badge-pack JSON file.');
        privateBadges = parseBadgePack(await packFile.text());
        $('badge-state').textContent = 'Loaded for this page';
      }
      if (!privateBadges) throw new Error('Add the badge-pack JSON for the staff badge you want to detect.');
    }
    $('analyze-dialog').close(); e.target.reset();
    notice(hosted ? 'Starting server analysis…' : 'Starting browser analysis…');
    const { frameCsv, xyCsv } = await (hosted ? runOnServer(file, headers) : runInBrowser(file, privateBadges));
    load({ frames: readResults(frameCsv, xyCsv), frameCsv, xyCsv }, file.name, file);
    notice('Analysis complete. Frame and coordinate CSVs are ready.');
  } catch (problem) {
    if ($('analyze-dialog').open) { error.textContent = problem.message; error.hidden = false; }
    else notice(problem.message);
  } finally { submit.disabled = false; $('analyze-button').disabled = false; }
});

load(makeDemo(), 'Synthetic demo', null, true);
try {
  analysis = await api('/api/availability');
  if (analysis.enabled) {
    $('mode-label').hidden = false;
    $('run-mode').options[1].textContent = analysis.requires_key ? 'Cloudflare analysis server' : 'Local Python server';
  }
} catch { /* Static hosting uses on-device analysis. */ }
