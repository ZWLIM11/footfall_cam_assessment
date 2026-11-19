import assert from 'node:assert/strict';
import test from 'node:test';
import { eventsFor, frameAt, makeDemo, readCsv, readResults } from './web/records.js';
import { forgetSavedBadgePack, loadSavedBadgePack, parseBadgePack, rememberBadgePack } from './web/badge-pack.js';
import { coverageFor, pointAtPointer, reviewedExports, setReviewPoint } from './web/review.js';

test('imports demo exports and builds separate staff and uncertain intervals', () => {
  const { frameCsv, xyCsv } = makeDemo();
  const frames = readResults(frameCsv, xyCsv);
  assert.equal(frames.length, 300);
  assert.equal(eventsFor(frames).filter(row => row.status === 'present').length, 4);
  assert.equal(frameAt(frames, 3.04), 38);
  assert.equal(frames[38].points[0].evidence, 'auto_badge');
});

test('accepts BOM and quoted CSV data with CRLF', () => {
  const csv = '\uFEFFname,note\r\n"staff,1","said ""hi"""\r\n';
  assert.deepEqual(readCsv(csv).rows, [{ name: 'staff,1', note: 'said "hi"' }]);
});

test('rejects coordinates for frames not flagged as staff', () => {
  const { frameCsv, xyCsv } = makeDemo();
  assert.throws(() => readResults(frameCsv, xyCsv + '0,0,staff,21,10,auto_badge\n'), /does not match/);
});

test('loads all required badge crops and rejects oversized image dimensions', () => {
  const pngHeader = Buffer.alloc(40);
  pngHeader.set([137, 80, 78, 71, 13, 10, 26, 10]);
  pngHeader.write('IHDR', 12);
  pngHeader.writeUInt32BE(12, 16);
  pngHeader.writeUInt32BE(12, 20);
  const encoded = pngHeader.toString('base64');
  const pack = { 'front.png': encoded, 'dark.png': encoded, 'light.png': encoded };
  assert.deepEqual(parseBadgePack(JSON.stringify(pack))['dark.png'], Uint8Array.from(pngHeader));
  pngHeader.writeUInt32BE(257, 16);
  pack['dark.png'] = pngHeader.toString('base64');
  assert.throws(() => parseBadgePack(JSON.stringify(pack)), /valid small PNG named dark\.png/);
});

test('restores saved crops on a new visit and clears them on request', () => {
  const png = new Uint8Array(40);
  png.set([137, 80, 78, 71, 13, 10, 26, 10]);
  png.set([73, 72, 68, 82], 12);
  const dimensions = new DataView(png.buffer);
  dimensions.setUint32(16, 12);
  dimensions.setUint32(20, 12);
  const badges = { 'front.png': png, 'dark.png': png, 'light.png': png };
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  assert.equal(loadSavedBadgePack(storage), undefined);
  rememberBadgePack(badges, storage);
  assert.deepEqual(loadSavedBadgePack(storage), badges);
  forgetSavedBadgePack(storage);
  assert.equal(loadSavedBadgePack(storage), undefined);
});

test('treats an unreadable or corrupt saved pack as missing', () => {
  assert.equal(loadSavedBadgePack({ getItem: () => '{broken' }), undefined);
  assert.equal(loadSavedBadgePack({ getItem: () => { throw new Error('Storage disabled'); } }), undefined);
});

test('review exports keep frame flags, provenance and unaffected coordinate rows in sync', () => {
  const frameCsv = 'frame_index,time_seconds,staff_present,status,staff_ids\n0,0.0,1,present,auto_1|auto_2\n1,0.04,0,uncertain,\n2,0.08,1,present,auto_1\n';
  const header = 'frame_index,time_seconds,staff_id,x_center,y_center,evidence,x1,y1,x2,y2,badge_score,note\n';
  const xyCsv = header + '0,0.0,auto_1,30,20,auto_badge,25,15,35,25,0.95,"a,b"\n' +
    '0,0.0,auto_2,65,20,auto_bridge,60,15,70,25,,unchanged\n2,0.08,auto_1,32,23,auto_track,25,15,38,28,,\n';
  const frames = readResults(frameCsv, xyCsv);
  assert.equal(frames[0].points[0].badgeScore, .95);
  const edits = new Map([
    [0, setReviewPoint(frames[0], 0, 'auto_1', 35.5, 22, 100, 80)],
    [1, setReviewPoint(frames[1], undefined, 'auto_1', 42, 24, 100, 80)],
    [2, { ...frames[2], status: 'empty', reviewed: true, points: [] }],
  ]);
  const exported = reviewedExports(frameCsv, xyCsv, edits);
  const flags = readCsv(exported.frameCsv).rows;
  const points = readCsv(exported.xyCsv).rows;
  assert.equal(flags[1].detector_status, 'uncertain');
  assert.equal(flags[1].staff_ids, 'auto_1');
  assert.equal(flags[2].status, 'reviewed_no_staff');
  assert.equal(flags[2].staff_present, '0');
  assert.equal(points.length, 3);
  assert.equal(points[0].detector_evidence, 'auto_badge');
  assert.equal(points[0].evidence, 'manual_review');
  assert.equal(points[0].x1, '');
  assert.equal(points[0].badge_score, '');
  assert.equal(points[0].note, 'a,b');
  assert.equal(points[1].x1, '60');
  assert.equal(points[1].evidence, 'auto_bridge');
  assert.equal(points[2].time_seconds, '0.04');
  const reopened = readResults(exported.frameCsv, exported.xyCsv);
  assert.ok(reopened.every(frame => frame.reviewed));
  const stats = coverageFor(reopened);
  assert.equal(stats.located, 2);
  assert.equal(stats.points.manual_review, 2);
  assert.equal(stats.points.auto_bridge, 1);
  assert.deepEqual(stats.pending, []);
  assert.deepEqual(reviewedExports(frameCsv, xyCsv, new Map()), { frameCsv, xyCsv });
});

test('maps preview clicks to original pixels and ignores letterboxed space', () => {
  const rect = { left: 40, top: 100, width: 500, height: 500 };
  assert.deepEqual(pointAtPointer(290, 350, rect, 1000, 500), { x: 500, y: 250 });
  assert.equal(pointAtPointer(290, 200, rect, 1000, 500), undefined);
  assert.deepEqual(pointAtPointer(539.99, 474.99, rect, 1000, 500), { x: 999, y: 499 });
});

test('rejects duplicate IDs and out-of-frame point edits', () => {
  const frame = { points: [{ id: 'auto_1' }] };
  assert.throws(() => setReviewPoint(frame, undefined, 'auto_1', 7, 8, 100, 80), /already on the frame/);
  assert.throws(() => setReviewPoint(frame, undefined, '=A1', 7, 8, 100, 80), /staff ID/);
  assert.throws(() => setReviewPoint(frame, 0, 'auto_1', 100, 17, 100, 80), /inside the frame/);
});
