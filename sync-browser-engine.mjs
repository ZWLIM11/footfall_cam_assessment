import { copyFileSync, mkdirSync, readFileSync } from 'node:fs';

const names = ['continuity.py', 'review.py', 'run.py', 'vision.py'];
const destination = new URL('./web/_engine/', import.meta.url);
const source = new URL('./code/', import.meta.url);
const checking = process.argv.includes('--check');

if (!checking) mkdirSync(destination, { recursive: true });
for (const name of names) {
  const original = new URL(name, source);
  const browserCopy = new URL(name, destination);
  if (checking) {
    if (!readFileSync(original).equals(readFileSync(browserCopy))) {
      throw new Error(`Browser engine copies are out of date. Run npm run sync-engine (${name}).`);
    }
  } else copyFileSync(original, browserCopy);
}
