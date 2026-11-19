/* Runs the same OpenCV tracker as the CLI in a separate browser thread. */
const runtimeUrl = 'https://cdn.jsdelivr.net/pyodide/v0.28.2/full/pyodide.mjs';
const sources = ['continuity.py', 'review.py', 'run.py', 'vision.py'];
let latestError = '';

function progress(text) { self.postMessage({ type: 'progress', text }); }

async function engine() {
  progress('Loading the browser detector and OpenCV…');
  const [{ loadPyodide }, files] = await Promise.all([
    import(runtimeUrl),
    Promise.all(sources.map(async name => {
      const response = await fetch(new URL(`./_engine/${name}`, import.meta.url));
      if (!response.ok) throw new Error('The detector files are temporarily unavailable. Refresh and try again.');
      return response.text();
    })),
  ]);
  const py = await loadPyodide({
    stdout: line => {
      if (/^Scanned (\d+)\/(\d+) frames$/.test(line) || line.startsWith('Linking badge')) progress(line);
    },
    stderr: line => { if (line.startsWith('Error:')) latestError = line.slice(6).trim(); },
  });
  await py.loadPackage('opencv-python', { messageCallback: () => {} });
  py.FS.mkdir('/engine');
  sources.forEach((name, i) => py.FS.writeFile(`/engine/${name}`, files[i]));
  return py;
}

self.onmessage = async ({ data }) => {
  try {
    const { file, badges } = data;
    if (!file || file.size === 0 || file.size > 60 * 1024 * 1024) throw new Error('Choose a non-empty MP4 up to 60 MiB.');
    const py = await engine();
    py.FS.mkdir('/badges');
    for (const name of ['front.png', 'dark.png', 'light.png']) py.FS.writeFile(`/badges/${name}`, badges[name]);
    py.FS.writeFile('/clip.mp4', new Uint8Array(await file.arrayBuffer()));
    progress('Reading the video and estimating its background…');
    await py.runPythonAsync(`
import sys
from pathlib import Path
sys.path.insert(0, '/engine')
from run import input_video, main
cap, count, fps, width, height = input_video(Path('/clip.mp4'))
cap.release()
if count > 4500 or max(width, height) > 1920 or width * height > 1920 * 1080:
    raise ValueError('Choose a clip with at most 4,500 frames and up to 1080p resolution.')
main(['--input', '/clip.mp4', '--templates', '/badges', '--output', '/results'], progress_interval=25)
`);
    const decode = path => py.FS.readFile(`/results/${path}`, { encoding: 'utf8' });
    self.postMessage({ type: 'done', frameCsv: decode('staff_frames.csv'), xyCsv: decode('staff_coordinates.csv') });
  } catch (error) {
    const expected = String(error.message || error).match(/(?:ValueError|RuntimeError): ([^\r\n]+)\s*$/);
    self.postMessage({ type: 'error', text: latestError || expected?.[1] ||
      'The browser detector could not run. Check the badge pack, try an H.264 MP4, and reload if the runtime failed to download.' });
  }
};
