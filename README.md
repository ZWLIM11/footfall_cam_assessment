# FootfallCam detection workspace

A browser workspace for reviewing frame-by-frame staff badge detection and XY estimates from an overhead video. The Python detector uses badge matching, foreground candidates and motion-supported track links.

## Use on-device analysis

Visit the [live Cloudflare workspace](https://footfall-cam-assessment.limziwei2004.workers.dev/) and select **Analyze video**. Pick an H.264 MP4 and the private badge-pack JSON. Leave **Remember this badge pack on this browser** checked to reuse it after a refresh or on your next visit. Use **Change** or **Forget** in the analysis dialog to replace or remove the saved pack. The detector runs in a browser thread using a pinned [Pyodide WebAssembly runtime](https://pyodide.org/en/0.28.2/usage/webworker.html). There is no Python installation or Cloudflare Container required. Keep the tab open during analysis. The first run downloads the detector runtime and OpenCV. Processing time depends on your device and the clip.

The browser keeps the MP4, badge pack and results on your device. It accepts up to 60 MiB, 4,500 frames and 1080p. A badge pack can be prepared with `pack_badges.py` from the three PNG files in the assessment submission. Keep that file private.

After analysis, use **Show uncertain** to filter the event list or **Next uncertain** to step through individual frames. Use **Place XY** or **Edit XY**, then click the video preview or enter original-frame pixel coordinates. **Mark no staff** resolves a frame with no visible staff. **Undo frame edits** restores the imported or automatic result for the current frame. Download both CSVs to keep the corrected pair. The reviewed CSVs label manually placed coordinates with `manual_review` and record the original detector status for edited frames. They can be opened again through **Open results** with the matching video.

**Tracking coverage** reports the percentage of frames with XY points and the counts by point source. A direct match uses the badge center. Optical flow and foreground continuity connect strong sightings, and linked points use the foreground region center. Short gaps are interpolated, and a short extension can follow the same foreground region from a single sighting.

As you scrub, **Frame details** shows the staff count, time, and XY points for that frame. The frames CSV includes `staff_count` for each timestamp, and the coordinates CSV lists each detection with its staff ID and XY. A frame flagged uncertain can have zero detections while it awaits review. When reopening older exports, the workspace uses their staff IDs and coordinates to reconstruct the count where available.

## Benchmark a detector run

Load the original automatic results and choose **Benchmark**. Download the label template and fill in `staff_present` with 1 for verified staff and 0 for verified no staff. Leave unchecked frames blank. You can add one verified `x_center,y_center` pair in original pixels per staff frame, or use the reviewed CSV pair as reference. In a reviewed pair only manually reviewed frames and coordinates count. Compare labels from the same clip using zero-based frame indexes.

The benchmark reports precision, recall, F1, false flags, misses, and median XY error on paired points. It counts uncertain detector frames as unflagged. Precision and F1 require both verified staff and no-staff frames. The within-60-px denominator includes every reference center, including missed frames. **Download benchmark CSV** saves the result for each verified frame. Use separately labeled clips to evaluate how well the detector works on new footage.

## Deploy the browser workspace

The default `wrangler.jsonc` serves `web/` as static assets on Workers Free. For the Git-connected Worker `footfall-cam-assessment`, choose `main` as the production branch, keep the root directory at `/`, leave the build command empty, and use `npx wrangler deploy` as the deploy command. [Cloudflare Workers static assets](https://developers.cloudflare.com/workers/static-assets/get-started/)

If you use **Cloudflare Pages**, connect this repository to a Pages project. Use the `main` production branch, no framework, an empty build command and `web` as the build output directory. [Cloudflare Pages Git integration](https://developers.cloudflare.com/pages/get-started/git-integration/) · [Static HTML setup](https://developers.cloudflare.com/pages/framework-guides/deploy-anything/)

The workspace includes an interactive synthetic scene. Choose **Open results** to load `staff_frames.csv`, `staff_coordinates.csv` and the corresponding MP4 if available. Playback, the frame timeline, XY overlays and on-device analysis all operate inside the browser.

To preview the same static UI locally, run `python -m http.server 8080 --bind 127.0.0.1 --directory web` and open `http://127.0.0.1:8080`.

## Run the detector locally

Install the packages from the repo root using Python 3.13. Keep the assessment's three supplied badge crops (`front.png`, `dark.png`, `light.png`) together in a private folder. They are in `code/assets` in the private assessment submission archive.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r code\requirements.txt Flask==3.1.2
$env:FOOTFALLCAM_TEMPLATE_DIR = 'C:\private\footfallcam-badges'
.\.venv\Scripts\python.exe server.py
```

Open `http://127.0.0.1:8080` and choose **Analyze video** and **Local Python server**. Select an MP4 up to 60 MiB, 4,500 frames and 1080p. When detection finishes, inspect the overlay and download both CSV exports. The server handles one job at a time, removes uploaded video after processing and keeps recent results temporarily.

For a direct Python run with an annotated video export, use the same badge folder.

```powershell
.\.venv\Scripts\python.exe code\run.py --input 'C:\private\video.mp4' --templates $env:FOOTFALLCAM_TEMPLATE_DIR --output output --visualize
.\.venv\Scripts\python.exe code\verify.py output
```

## Run the full analyzer on Cloudflare Workers

The Worker serves the UI from `web/` and forwards authenticated video analysis jobs to the Linux Python container in `Dockerfile`. This route uses **Workers Paid** and a running Docker-compatible engine for local deployment. [Cloudflare Containers](https://developers.cloudflare.com/containers/get-started/) · [Container pricing](https://developers.cloudflare.com/containers/platform/pricing/)

From the repo root, choose `wrangler.container.jsonc` for the paid deployment. If you deploy from Git with Workers Builds, set its deploy command to `npx wrangler deploy --config wrangler.container.jsonc` so future builds use that configuration. Set a strong private analysis key and send the badge crops directly to the Worker secret over stdin.

```powershell
npx wrangler login
npx wrangler deploy --config wrangler.container.jsonc
npx wrangler secret put FOOTFALLCAM_ACCESS_KEY --config wrangler.container.jsonc
.\.venv\Scripts\python.exe pack_badges.py $env:FOOTFALLCAM_TEMPLATE_DIR | npx wrangler secret put FOOTFALLCAM_TEMPLATES --config wrangler.container.jsonc
```

Use the `workers.dev` URL printed by Wrangler. Once the container is ready and both secrets are configured, **Analyze video** offers **Cloudflare analysis server**. Enter the analysis key to run the Python tracker on the container. Container results are temporary, so download the CSV exports after each run. [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/)

## Assessment retest

| Metric | Original detector | Improved detector |
| --- | ---: | ---: |
| Automatically flagged frames | 203 | 772 |
| Within 40 px of the reviewed center | 85 | 501 |
| Within 60 px of the reviewed center | 151 | 698 |
| Median XY difference on flagged frames | 43.4 px | 30.0 px |

Both modes processed the same 1,341-frame overhead clip without a review file. The earlier visual review was used afterward to score XY. The improved tracker links two supported badge sightings using optical flow and foreground continuity. Its longest permitted bridge is 14 seconds, with tighter limits for an extension supported on one side. It still misses some stationary or obscured wearers and can drift at crossings. Accuracy on unseen real footage remains unmeasured.

The public repository contains source code, aggregate scores and the synthetic viewer demo. The original footage, annotated recordings, assessment imagery, badge crops and per-frame assessment results are distributed separately in the submission archive.

```powershell
npm test
.\.venv\Scripts\python.exe -m unittest discover -s code\tests -v
```

After editing the shared detector, run `npm run sync-engine` to refresh the Python files served under `web/_engine/`. `npm test` checks that the browser and CLI source stay in sync.
