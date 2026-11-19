import assert from 'node:assert/strict';
import test from 'node:test';
import { eventsFor, frameAt, makeDemo, readCsv, readResults } from './web/records.js';

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
