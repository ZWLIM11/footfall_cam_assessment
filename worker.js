import { DurableObject } from 'cloudflare:workers';

const PORT = 8080;
const MAX_BYTES = 60 * 1024 * 1024;
const response = (body, status = 200) => Response.json(body, { status, headers: { 'Cache-Control': 'no-store' } });

export class Detector extends DurableObject {
  async fetch(request) {
    this.starting ??= this.waitForPort().finally(() => { this.starting = undefined; });
    await this.starting;
    const url = new URL(request.url);
    url.protocol = 'http:';
    url.host = 'container';
    const forwarded = new Request(url, request);
    forwarded.headers.delete('host');
    forwarded.headers.delete('Authorization');
    return this.ctx.container.getTcpPort(PORT).fetch(forwarded);
  }

  async waitForPort() {
    const container = this.ctx.container;
    if (!container?.running) {
      container.start({ image: container.images.base, enableInternet: false, instance: 'standard-2',
        env: { FOOTFALLCAM_TEMPLATES: this.env.FOOTFALLCAM_TEMPLATES } });
    }
    await container.setInactivityTimeout(15 * 60 * 1000);
    const port = container.getTcpPort(PORT);
    for (let attempt = 0; attempt < 60; attempt++) {
      try {
        const health = await port.fetch('http://container/health', { signal: AbortSignal.timeout(1000) });
        await health.body?.cancel();
        if (health.ok) return;
      } catch { /* Retry startup until the Python server is listening. */ }
      await scheduler.wait(300);
    }
    throw new Error('The detector container did not start.');
  }
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (!path.startsWith('/api/')) return env.ASSETS.fetch(request);
    const enabled = Boolean(env.FOOTFALLCAM_ACCESS_KEY && env.FOOTFALLCAM_TEMPLATES);
    if (path === '/api/availability' && request.method === 'GET') {
      return response({ enabled, requires_key: true, max_bytes: MAX_BYTES });
    }
    if (!enabled) return response({ error: 'The hosted detector has not been configured.' }, 503);
    if (request.headers.get('Authorization') !== `Bearer ${env.FOOTFALLCAM_ACCESS_KEY}`) {
      return response({ error: 'The analysis key was rejected.' }, 401);
    }
    if (!((request.method === 'POST' && path === '/api/jobs') ||
        (request.method === 'GET' && /^\/api\/jobs\/[a-f0-9]{32}(\/results)?$/.test(path)))) {
      return response({ error: 'Unknown analysis endpoint.' }, 404);
    }
    if (request.method === 'POST' && Number(request.headers.get('Content-Length')) > MAX_BYTES) {
      return response({ error: 'Choose an MP4 at most 60 MiB.' }, 413);
    }
    try {
      return await env.DETECTOR.getByName('workspace').fetch(request);
    } catch (error) {
      console.error('Analysis service request failed', error.message);
      return response({ error: 'The analysis service is starting or unavailable. Try again shortly.' }, 503);
    }
  },
};
