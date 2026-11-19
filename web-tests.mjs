import assert from 'node:assert/strict';
import test from 'node:test';
import { eventsFor, frameAt, makeDemo, readCsv, readResults } from './web/records.js';
import { parseBadgePack } from './web/badge-pack.js';

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
