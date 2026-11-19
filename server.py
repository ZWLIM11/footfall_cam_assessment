"""Serve the workspace and run the existing detector on temporary MP4 uploads."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from uuid import uuid4

import cv2
from flask import Flask, jsonify, request


ROOT = Path(__file__).resolve().parent
NAMES = ('front.png', 'dark.png', 'light.png')
MAX_BYTES = 60 * 1024 * 1024
MAX_FRAMES = 4500
JOB_TIMEOUT = 900
KEEP_SECONDS = 900
app = Flask(__name__, static_folder=str(ROOT / 'web'), static_url_path='')
app.config['MAX_CONTENT_LENGTH'] = MAX_BYTES
storage = TemporaryDirectory(prefix='footfallcam-')
work = Path(storage.name)
executor = ThreadPoolExecutor(max_workers=1)
lock = Lock()
jobs = {}


def configure_templates():
    bundle = os.getenv('FOOTFALLCAM_TEMPLATES')
    if bundle:
        folder = work / 'templates'
        folder.mkdir()
        try:
            images = json.loads(bundle)
            for name in NAMES:
                data = base64.b64decode(images[name], validate=True)
                if not 40 <= len(data) <= 12000 or not data.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Invalid badge crop')
                (folder / name).write_bytes(data)
        except (KeyError, TypeError, ValueError):
            app.logger.error('FOOTFALLCAM_TEMPLATES must contain the three badge crops as a JSON object.')
            return None
    else:
        folder = Path(os.getenv('FOOTFALLCAM_TEMPLATE_DIR', str(ROOT / 'code' / 'assets'))).resolve()
    return folder if all((folder / name).is_file() for name in NAMES) else None


templates = configure_templates()


def update(job_id, **fields):
    with lock:
        jobs[job_id].update(fields)


def run_job(job_id, folder):
    source = folder / 'input.mp4'
    log_path = folder / 'detector.log'
    output = folder / 'results'
    try:
        cap = cv2.VideoCapture(str(source))
        try:
            width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            if not cap.isOpened() or min(width, height, fps, frames) <= 0:
                raise ValueError('The file could not be decoded as an MP4.')
            if frames > MAX_FRAMES or width > 1920 or height > 1920 or width * height > 1920 * 1080:
                raise ValueError(f'Choose a video with at most {MAX_FRAMES:,} frames and up to 1080p resolution.')
        finally:
            cap.release()
        command = [sys.executable, str(ROOT / 'code' / 'run.py'), '--input', str(source), '--output', str(output),
                   '--templates', str(templates), '--tracking', 'continuity']
        update(job_id, progress='Scanning the clip with the Python detector…')
        with log_path.open('wb') as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=JOB_TIMEOUT)
        if completed.returncode:
            raise ValueError('The Python detector could not process this video. Try a shorter H.264 MP4.')
        update(job_id, state='done', finished=monotonic())
    except subprocess.TimeoutExpired:
        update(job_id, state='failed', error='Analysis reached its time limit. Try a shorter clip.', finished=monotonic())
    except ValueError as error:
        update(job_id, state='failed', error=str(error), finished=monotonic())
    except Exception:
        app.logger.exception('Video job failed')
        update(job_id, state='failed', error='The local detector failed to finish.', finished=monotonic())
    finally:
        source.unlink(missing_ok=True)


@app.after_request
def avoid_api_cache(response):
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.errorhandler(413)
def oversized(_error):
    return jsonify(error='Choose an MP4 at most 60 MiB.'), 413


@app.get('/')
def homepage():
    return app.send_static_file('index.html')


@app.get('/health')
def ready():
    return 'ready', 200


@app.get('/api/availability')
def availability():
    return jsonify(enabled=templates is not None, requires_key=False, max_bytes=MAX_BYTES)


@app.post('/api/jobs')
def submit():
    if templates is None:
        return jsonify(error='Badge templates are unavailable on the analysis server.'), 503
    if request.mimetype != 'video/mp4':
        return jsonify(error='Choose an MP4 video.'), 400
    if request.content_length and request.content_length > MAX_BYTES:
        return oversized(None)
    job_id = uuid4().hex
    with lock:
        expired = [key for key, job in jobs.items() if 'finished' in job and monotonic() - job['finished'] > KEEP_SECONDS]
        for key in expired:
            jobs.pop(key)
            shutil.rmtree(work / key, ignore_errors=True)
        if any(job['state'] == 'running' for job in jobs.values()) or len(jobs) >= 12:
            return jsonify(error='The detector is busy. Try again in a moment.'), 429
        jobs[job_id] = {'state': 'running', 'progress': 'Preparing video…'}
    folder = work / job_id
    try:
        folder.mkdir()
        source = folder / 'input.mp4'
        with source.open('wb') as stream:
            shutil.copyfileobj(request.stream, stream, length=1024 * 1024)
        if source.stat().st_size == 0:
            shutil.rmtree(folder)
            with lock:
                jobs.pop(job_id)
            return jsonify(error='The video upload was empty.'), 400
        executor.submit(run_job, job_id, folder)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        with lock:
            jobs.pop(job_id, None)
        raise
    return jsonify(id=job_id), 202


@app.get('/api/jobs/<job_id>')
def status(job_id):
    with lock:
        job = jobs.get(job_id)
        snapshot = {key: value for key, value in job.items() if key != 'finished'} if job else None
    return jsonify(snapshot) if snapshot else (jsonify(error='This job has expired or the container restarted.'), 404)


@app.get('/api/jobs/<job_id>/results')
def results(job_id):
    with lock:
        job = jobs.get(job_id)
        if not job or job['state'] != 'done':
            return jsonify(error='The results are unavailable.'), 404
        folder = work / job_id / 'results'
        return jsonify(frames_csv=(folder / 'staff_frames.csv').read_text(encoding='utf-8'),
                       coordinates_csv=(folder / 'staff_coordinates.csv').read_text(encoding='utf-8'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default=8080, type=int)
    args = parser.parse_args()
    app.run(host='127.0.0.1', port=args.port, threaded=True)
