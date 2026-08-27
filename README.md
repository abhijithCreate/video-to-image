# Video to Image

Extract high-quality images from video frames. A single Python web application —
FastAPI serves both the HTML page (Jinja2 + Tailwind) and the JSON API. FFmpeg
decodes frames, Pillow encodes them.

A modern, self-hostable alternative to online tools such as ezgif's video-to-jpg.

## Features

- **Three-step flow** — Upload → Options → Images. Only the current step is on
  screen; a back arrow sits to the left of each step's title. No page reloads
- Drag-and-drop upload with progress, **or paste a video URL** — a direct file
  link, or a **YouTube** (and similar) page link, resolved with yt-dlp
- MP4 / WebM / MOV / AVI / MKV / MPEG / M4V support
- Video inspection: filename, size, duration, resolution, frame rate, format and codec
- Four extraction methods: **frame rate**, **number of images**, **time interval**, **every frame**
- Output formats: JPG, PNG, WebP, BMP, TIFF, GIF
- Quality presets (Low 50 / Medium 70 / High 85 / Very High 95) plus a custom slider for lossy formats
- Resolution presets up to 1920×1080, custom sizes, aspect ratio preserved by default
- **Image title** — an optional name for the output: `beach-sunset_00001.jpg` and
  `beach-sunset.zip` instead of the default `frame_00001.jpg`
- Up to **500 images** per conversion
- **ZIP-only mode** — "Don't display the images, I just want a ZIP file" skips previews entirely
- **Remember settings** — keeps your options in the browser for the next conversion
- Lazy-loaded results grid, **Lightbox3** image viewer (swipe, pinch-zoom,
  keyboard navigation), single or ZIP download
- Light/dark mode, responsive from mobile to desktop, reduced-motion support
- Server-side validation everywhere, and every job deleted automatically

## Requirements

- Python 3.12+
- FFmpeg (provides `ffmpeg` and `ffprobe`)

## Run with Docker (recommended)

FFmpeg is baked into the image, so this needs nothing else installed.

```bash
docker compose up --build
```

Open <http://localhost:8000>.

## Run locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # optional — defaults work as-is
brew install ffmpeg           # or: apt-get install ffmpeg

uvicorn app.main:app --reload
```

Templates and static files are located relative to the `app` package, not the
working directory, so it does not matter where you start the server from.

## Tests

```bash
pytest
```

Tests that need a real video are skipped automatically when FFmpeg is not on
`PATH`; `GET /health` reports whether the server can see it. To run *everything*
without installing FFmpeg locally, use the image, which bundles it:

```bash
docker run --rm -v "$PWD":/work -w /work -e HOME=/tmp \
  video-to-image:latest python -m pytest -q -p no:cacheprovider
```

One test actually reaches YouTube and is opt-in, so the suite stays deterministic
and does not fail when a video is taken down:

```bash
V2I_NETWORK_TESTS=1 pytest tests/test_media_site.py
```

`tests/conftest.py` chdirs to the project root, which is convenient but hides a
whole class of path bug — see [Vercel](#vercel). `tests/test_deployment.py`
deliberately runs from elsewhere to cover it.

## Configuration

All settings come from the environment. `.env` is **optional** — every value
below has a working default, so the app runs without one:

```bash
cp .env.example .env
```

`.env` is read from the project root regardless of where you start the server
(see [Paths must not depend on the working directory](#paths-must-not-depend-on-the-working-directory));
a cwd-relative env file is silently ignored from another directory, which leaves
the app running on defaults with nothing to say the config was skipped. `.env` is
gitignored and must stay that way — `.env.example` is the file that gets
committed.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | `production` disables `/docs` |
| `MAX_UPLOAD_SIZE_MB` | `50` | Upload ceiling, enforced while streaming to disk |
| `MAX_VIDEO_DURATION_SECONDS` | `60` | Rejected after probing, before any processing |
| `MAX_OUTPUT_IMAGES` | `500` | Hard cap on frames per job |
| `MAX_TOTAL_OUTPUT_MB` | `2048` | Ceiling on the bytes one job may produce |
| `JOB_RETENTION_MINUTES` | `60` | Age at which a job directory is swept |
| `MAX_DOWNLOAD_CLIENTS` | `3` | Distinct addresses allowed before a result is destroyed |
| `TEMP_DIR` | `/tmp/video-to-image` | Where job directories live |
| `ALLOW_URL_UPLOADS` | `true` | Enables fetching a video from a pasted URL |
| `URL_FETCH_TIMEOUT_SECONDS` | `60` | Total budget for one URL fetch |
| `URL_MAX_REDIRECTS` | `3` | Redirect hops followed, each re-validated |
| `ALLOW_PRIVATE_URL_HOSTS` | `false` | Permit URL fetches to private addresses |
| `ALLOW_MEDIA_SITE_URLS` | `true` | Resolve video-site pages (YouTube etc.) with yt-dlp |
| `MEDIA_SITE_MAX_HEIGHT` | `1080` | Tallest stream the extractor will pick |
| `MEDIA_SITE_TIMEOUT_SECONDS` | `120` | Total budget for one page link, all client attempts |
| `YOUTUBE_COOKIES_FILE` | *(unset)* | `cookies.txt` to send when a bot check will not let up |
| `YOUTUBE_COOKIES_FROM_BROWSER` | *(unset)* | Read cookies from a local browser instead |
| `FFMPEG_PATH` / `FFPROBE_PATH` | `ffmpeg` / `ffprobe` | Binary locations |
| `PROCESS_TIMEOUT_SECONDS` | `600` | Wall-clock limit for one extraction run |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | The application page |
| `GET` | `/health` | Liveness plus FFmpeg availability |
| `POST` | `/api/upload` | Multipart upload; validates and probes, returns `job_id` + metadata |
| `POST` | `/api/process` | JSON options; extracts and encodes, returns the image index |
| `GET` | `/api/result/{job_id}` | Re-read a completed job |
| `GET` | `/api/download/{job_id}/{filename}` | One image |
| `POST` | `/api/upload-url` | Fetch a video from a URL; same response as `/api/upload` |
| `GET` | `/api/preview/{job_id}` | Streams the uploaded video for the on-page player |
| `GET` | `/api/thumbnail/{job_id}/{filename}` | Grid preview (keeps the page light) |
| `GET` | `/api/download-all/{job_id}` | All images as a ZIP |
| `DELETE` | `/api/job/{job_id}` | Delete a job and its files immediately |

`POST /api/process` accepts an optional `title`, which names the generated files
(`<title>_00001.<ext>`) and the ZIP (`<title>.zip`). It is sanitised server-side:
anything outside `A-Za-z0-9_-` is folded to a hyphen, runs of separators are
collapsed, and the result is trimmed to 60 characters — so the names always
satisfy the strict pattern the download endpoints enforce. A blank or unusable
title keeps the default `frame_00001.<ext>` and `images-<job>.zip`.

`POST /api/process` accepts `zip_only: true`, which skips thumbnails, builds the
archive up front and publishes no per-image URLs at all — the response carries
`count`, `archive_size_bytes` and `download_all_url` only.

Errors are always `{"error": "<message>"}` — never a stack trace or a server path.

`GET /health` reports which features the running build has, which is the quickest
way to tell whether a deployment picked up the latest code:

```json
{"status":"ok","ffmpeg":true,"features":{"url_uploads":true,"media_sites":true,"zip_only":true,"steps":3},
 "limits":{"max_output_images":500,"job_retention_minutes":60}}
```

### Result lifetime

A result is deleted when **either** limit is reached:

- `JOB_RETENTION_MINUTES` (default 60) has passed, or
- `MAX_DOWNLOAD_CLIENTS` (default 3) distinct addresses have downloaded it.

Repeat downloads from one address never count twice, and thumbnail requests are
not counted at all. Only a salted hash of each address is stored, so counting
distinct downloaders never means keeping a record of who they were. This is what
makes the UI's promise — *"removed in 1 hour, or sooner if accessed by multiple
IP addresses"* — literally true. `X-Forwarded-For` is honoured, so run behind a
trusted proxy (`uvicorn --proxy-headers --forwarded-allow-ips=...`) if the app is
not directly exposed.

## Architecture

```text
app/
├── main.py          app factory, error handlers, retention sweep
├── config.py        environment-backed settings
├── routes.py        HTTP layer: validation and orchestration only
├── jobs.py          temp job directories, metadata, cleanup, path safety
├── assets.py        content-hashed URLs for static files
└── services/
    ├── video.py      ffprobe metadata, extraction plans, FFmpeg frame decoding
    ├── image.py      Pillow: format, quality, resize, compression, output naming
    ├── download.py   fetching a video from a URL, with SSRF protection
    ├── media_site.py resolving a video-site page (YouTube etc.) with yt-dlp
    └── zip.py        archive creation
```

Processing is confined to `app/services`. Routes never shell out and never touch
Pillow, so the processing layer can move to a worker or another host without
changing the UI or the API.

Each job is a UUID directory under `TEMP_DIR` holding the input video, `output/`,
`thumbs/` and `job.json`. Metadata lives on disk beside the files, so any worker
can serve a job and nothing outlives the retention sweep. Uploaded videos are
never stored permanently.

### Naming the container

FFmpeg reports one demuxer name per container *family*, so an MP4 probes as
`mov,mp4,m4a,3gp,3g2,mj2` and a WebM as `matroska,webm`. Taking the first token
would label every MP4 "MOV" and every WebM "MATROSKA", so `describe_format()`
uses the file's own extension as the tie-breaker within a family.

### Frames are streamed, not staged

FFmpeg writes raw RGBA frames to a pipe and Pillow encodes them one at a time, so
no intermediate images ever touch the disk. That is what makes a 500-image limit
safe: staging 500 frames at 1080p as PNG first would cost up to ~2.4 GB of
temporary space, where streaming costs zero and holds one frame (~8 MB) in memory.

Measured on a 40 s 1080p clip, 500 images:

| Job | Time | Peak disk |
| --- | --- | --- |
| 500 × JPG 1920×1080, ZIP only | 20 s | 295 MB (input + output + ZIP) |
| 500 × JPG 1280×720, with previews | 11 s | 100 MB |
| 500 × PNG 1920×1080, ZIP only | 226 s | 780 MB |

Two guards keep a large batch from filling the disk: the first encoded frame is
used to project the total, and a batch that clearly cannot fit `MAX_TOTAL_OUTPUT_MB`
is refused in well under a second (500 × BMP at 1080p → *"would produce roughly
2966 MB"*). The running total is then enforced frame by frame as a backstop.

### Fetching from a URL

A pasted URL asks this server to make an outbound request, so `app/services/download.py`
validates every hop rather than trusting the string:

- only `http`/`https`, and the hostname must resolve to a **public** address —
  loopback, private ranges, link-local and the `169.254.169.254` cloud metadata
  endpoint are all refused (`ALLOW_PRIVATE_URL_HOSTS` opts in for trusted
  internal deployments)
- redirects are followed one at a time, up to `URL_MAX_REDIRECTS`, re-validating
  the target at each hop
- the body is capped at `MAX_UPLOAD_SIZE_MB` **while it streams** and the whole
  fetch is bounded by `URL_FETCH_TIMEOUT_SECONDS`, so an oversized or endless
  response cannot fill the disk
- the file still has to pass ffprobe afterwards, like any upload

The address check happens before the request, which leaves a small DNS-rebinding
window; that is the accepted trade-off for not pinning the connection to a
resolved IP.

### Video-site links (YouTube and similar)

A YouTube watch URL is not a video file — it is an HTML player page with the
media behind separate adaptive streams — so the direct path above cannot use it.
`app/services/media_site.py` resolves those links with **yt-dlp**.

How a link is routed:

1. A known page-only host (`youtube.com`, `youtu.be`, `vimeo.com`, …) goes
   straight to the extractor, so no pointless request is made first.
2. Anything else is tried as a direct file. If it comes back as a page rather
   than a video, the extractor gets a turn — which is what makes sites outside
   the shortcut list work.
3. If no extractor recognises it either, the plainer *"that link does not look
   like a video file"* message is what the user sees.

What the extractor does differently:

- **Metadata is read before anything downloads**, so a video over
  `MAX_VIDEO_DURATION_SECONDS` is refused in a couple of seconds rather than
  after a long transfer. Live streams are refused outright.
- **A video-only stream is preferred** (`bv*`, H.264 first). Frames are all that
  is wanted, so muxing an audio track that is about to be discarded is waste —
  and skipping the merge keeps it to one stream.
- Downloads are capped by `max_filesize`, height by `MEDIA_SITE_MAX_HEIGHT`, and
  only `input.<ext>` is written into the job directory; part files and rejected
  streams are cleared.
- yt-dlp's **generic** extractor is disabled. It accepts *any* URL and scrapes it
  for media, which would make every link look supported and would re-fetch URLs
  this module has no business touching.
- yt-dlp's own error text is mapped onto short messages ("That video is
  private.", "That video is unavailable.") so no internals reach the user.
- Because resolving a page link takes seconds to a minute rather than the
  near-instant direct fetch, the page publishes the budget as
  `data-timeout-seconds` on the URL form. The browser aborts 15 s after the
  server would give up, and posts progress lines while it waits — a silent
  spinner for a minute reads as a broken page.

Three caveats worth knowing:

- **Terms of service.** Downloading videos from YouTube is against its Terms of
  Service. That is a decision for whoever runs this server, not something the
  code can settle.
- **This does not work from a server.** YouTube challenges clients by IP, and
  every cloud host — Vercel, Fly, Railway, Render, any VPS — has a datacenter
  IP. Expect it to work from a home or office connection and to fail on a
  deployment. The deploy configs therefore ship
  `ALLOW_MEDIA_SITE_URLS=false`, which removes the field rather than offering
  something that cannot work. See *When the bot check fires* below.
- **yt-dlp needs bumping.** Extractors break whenever a site changes its player.
  The pin in `requirements.txt` will go stale; update it when links start
  failing.

#### When the bot check fires

YouTube's *"Sign in to confirm you're not a bot"* is rate-limiting by IP, not a
permanent block — it typically clears on its own within a minute or two. Three
things handle it, in order of how little they cost you:

1. **An automatic client retry.** The default (web) client is the one that gets
   challenged. `CLIENT_FALLBACKS` retries once with YouTube's `android` client,
   which talks to a different endpoint and still serves a complete progressive
   stream. Quality drops (typically 360p) — a working 360p beats nothing. Only
   clients verified to finish a *download* belong in that list: `android_vr`,
   for instance, extracts happily and then 403s on the media URLs, which is
   worse than not trying. The whole chain is bounded by
   `MEDIA_SITE_TIMEOUT_SECONDS`, since a challenged attempt burns wall clock on
   its own retries.
2. **Wait and retry.** Only the bot check is retried; a private video or an
   over-long one fails on the first attempt, so the server does not hammer the
   site for no gain.
3. **Cookies, if it will not let up.** Set `YOUTUBE_COOKIES_FILE` to a
   `cookies.txt` export (Netscape format), or `YOUTUBE_COOKIES_FROM_BROWSER` to
   a browser name. Both are unset by default: sending a logged-in session to a
   video site is the operator's decision, and those cookies are account
   credentials — mount them as a secret, never bake them into the image.

If none of that suits, `ALLOW_MEDIA_SITE_URLS=false` takes the field off the
page and leaves direct file links working. A video-site link then reports
*"Links from video sites are not supported on this server"* rather than falling
through to the direct path and claiming the link is not a video file.

#### Why moving hosts does not fix it

The challenge is about the **IP**, not the platform. Moving from Vercel to Fly,
Railway or Render changes nothing here — they are all datacenters. That is why
`fly.toml`, `render.yaml` and `vercel.json` all set
`ALLOW_MEDIA_SITE_URLS=false`, and a test asserts they still do.

Three ways to actually have it work on a server, none of them free:

1. **Cookies from a logged-in account** (`YOUTUBE_COOKIES_FILE`). Effective, but
   those cookies are account credentials, they expire, and using a residential
   session from a datacenter IP is a good way to get the account flagged. Use a
   throwaway account, mount the file as a secret, never bake it into an image.
2. **Route the extractor through a residential proxy.** Reliable, costs money,
   and adds a dependency to every fetch.
3. **Leave it off in production** and keep it for local use, where it works
   without any of the above. This is what the shipped configs do.

### Security

- Extension allow-list *and* ffprobe verification of a real video stream
- Streaming upload cap, duration cap, output-count cap, per-process timeout
- Job IDs must be UUID4; filenames must match a strict pattern and resolve inside
  their own job directory (no traversal)
- The user-supplied image title is folded to `A-Za-z0-9_-` before it ever reaches
  the filesystem, so a title like `../../etc/passwd` becomes `etc-passwd`
- FFmpeg is invoked with an argument list — never `shell=True`
- Generic error messages; details are logged server-side only

## Deployment notes

FFmpeg is the deciding factor: this app cannot extract a frame without it, and
a platform that does not provide it cannot run this app. Anything that takes a
Dockerfile works, because the image bundles FFmpeg itself.

### Container hosts (recommended)

`fly.toml` and `render.yaml` are ready to use; Railway needs no config file at
all — it detects the Dockerfile.

```bash
fly launch --copy-config --no-deploy && fly deploy && fly scale count 1
# or: push the repo and point Render at it (render.yaml is a blueprint)
# or: railway up
```

**Run exactly one instance.** A job is written to one instance's own `/tmp`, and
the later `/api/process` and `/api/download` requests must reach that same
instance. Scaling out makes conversions fail intermittently with *"This job has
expired or no longer exists."* Both configs pin a single instance for this
reason; a test asserts they still do.

The container reads `$PORT` (default 8000), which Render and Railway assign at
run time. `uvicorn` runs as PID 1 via `exec`, so a platform SIGTERM shuts it
down cleanly instead of being swallowed by a shell.

### Docker

Runs as a non-root user (uid 10001). Jobs live on the container's own ephemeral
layer rather than a `tmpfs`: a 500-image batch can run to hundreds of megabytes,
which is more than is sensible to hold in RAM. Nothing is persisted — the
directory dies with the container, and the retention sweep clears jobs long
before that. `docker compose up --build` serves port 8000.

### Vercel (not viable for processing)

`vercel.json` deploys `app/main.py` as a Python serverless function with
conservative limits (20 MB upload, 20 s duration, 100 images). It is kept for
reference; **use a container host instead**. Two problems are not fixable in
configuration:

- **No FFmpeg**, so nothing can be converted (below).
- **Ephemeral, per-instance disk.** The three-step flow spans three requests,
  and each may land on a different instance, so a job written during upload is
  often gone by `/api/process`. Bundling FFmpeg does not help with this.

For the record, bundling FFmpeg *would* just fit: static `ffmpeg` + `ffprobe`
are 160 MB uncompressed against a 250 MB cap, on top of ~79 MB of dependencies —
239 MB, leaving 11 MB of headroom, and requiring the binaries be copied to
`/tmp` and `chmod +x`'d at run time because the executable bit is not preserved.
That is a lot of fragility for a deployment that still loses jobs between
requests.

**It will serve the page, but it cannot convert video.** The Vercel Python
runtime ships no FFmpeg, so every upload is refused with *"Video processing is
unavailable on this server (FFmpeg not found)."* — a clean 400, and `/health`
reports `"ffmpeg": false`. Treat a Vercel deploy as a demo of the UI unless you
supply an `ffmpeg`/`ffprobe` binary in the bundle and point `FFMPEG_PATH` /
`FFPROBE_PATH` at it. A static ffmpeg build is ~80 MB on its own, which runs
straight into the bundle limit below.

#### Paths must not depend on the working directory

A serverless host imports the module with a working directory of its own —
Vercel uses `/var/task`. `StaticFiles(directory="static")` resolves that against
the cwd and **raises at import time**, so `app = create_app()` never completes:

```text
RuntimeError: Directory 'static' does not exist
    → 500 FUNCTION_INVOCATION_FAILED
```

The failure arrives before any exception handler exists, which is why the host
shows its own generic crash page rather than this app's JSON error. Everything
bundled is therefore located from `BASE_DIR` in `app/config.py`
(`Path(__file__).resolve().parents[1]`), never from the cwd. `Jinja2Templates`
had the same bug with a nastier signature: it accepts a missing directory
happily and only fails later, at render.

Keep `BASE_DIR` in mind when adding anything that opens a bundled file — and
note that `tests/conftest.py` chdirs to the project root, so the ordinary suite
cannot catch a regression here. `tests/test_deployment.py` runs from a foreign
directory on purpose.

#### A blank environment variable is not the same as an unset one

A hosting dashboard will happily store a variable with **no value**, and it
reaches the process as `""`. Pydantic will not coerce `""` into an `int` or a
`bool`, so a handful of blank rows in a Vercel project produced this at import:

```text
ValidationError: 9 validation errors for Settings
  max_total_output_mb
    Input should be a valid integer ... [input_value='', input_type=str]
  allow_url_uploads
    Input should be a valid boolean ... [input_value='', input_type=str]
  ...
    → 500 FUNCTION_INVOCATION_FAILED
```

The nine were exactly the keys present in `.env.example` but absent from
`vercel.json`'s `env` block, minus the four whose type accepts a blank string —
the signature of copying the example's key list into a dashboard without
filling in values. `THUMBNAIL_SIZE`, an `int` that is *not* in `.env.example`,
was conspicuously fine.

`Settings` now drops blank values before validation (`_blank_means_unset`), so a
blank variable means "not configured" and the default applies. A real value
still overrides, and it makes the documented *"leave empty to resolve from
PATH"* true for `FFMPEG_PATH` / `FFPROBE_PATH`, where `""` would otherwise be an
unresolvable path rather than a fallback.

#### Other serverless caveats

- **The bundle is larger than `maxLambdaSize` claims.** The dependencies install
  to ~79 MB — `yt-dlp` alone is 24 MB, Pillow 11 MB — against the 35 MB in
  `vercel.json`. `requirements.txt` also still carries `pytest` and `httpx`,
  which no production deploy needs.
- **Instances are ephemeral.** A job may be processed on one instance and
  downloaded from another, which loses the result. Long clips also risk the
  invocation timeout.
- **Video-site links are a poor fit.** Extraction plus download eats the
  invocation budget, and a platform's IPs are the ones sites challenge hardest.
  Set `ALLOW_MEDIA_SITE_URLS=false` there.

For anything beyond a UI demo, deploy the Docker image to a host with a real
filesystem and FFmpeg (Fly.io, Railway, Render, a VPS). The API and UI are
unchanged either way.

## Image viewing

Frames are viewed with [Lightbox3](https://lokeshdhakar.com/projects/lightbox3/)
(v1.3.0, MIT), which replaced a hand-rolled `<dialog>`. It has no dependencies —
unlike Lightbox2, which needs jQuery.

It is **vendored** into `static/vendor/lightbox3/` rather than loaded from a CDN,
so image viewing survives an offline or firewalled deployment and the version
cannot drift underneath us. It goes through `asset_url`, so it is cache-busted
like everything else.

Three things about the integration are easy to get wrong:

- **The UMD bundle exports `window.Lightbox3.Lightbox`, not `window.Lightbox`.**
  Guarding on the latter leaves the lightbox silently dead — the click just
  follows the link.
- **Its self-initialisation cannot be used here.** It only runs if a
  `[data-lightbox]` element exists at `DOMContentLoaded`, and the results grid is
  empty until a conversion finishes, so `app.js` calls `init()` explicitly. That
  is enough because the library binds a single delegated click listener on
  `document` and resolves the gallery at open time — links rendered later still
  work, with no re-init.
- **There is no download button, and captions are injected as HTML.** So the
  frame number, timestamp, resolution, format, size and the download link all
  ride in `data-caption`. The library ships no anchor styles, so
  `.lightbox3-caption a` is styled locally or the link reads as plain text.

Grid items are real `<a href>` links to the full-size image, so the grid still
works with JavaScript disabled. Note that below 600px the library hides its
arrows in favour of swipe.

## Static assets

`style.css`, `app.js` and the vendored Lightbox3 files are referenced through
`asset()`, which stamps each URL
with a short hash of the file's contents (`/static/js/app.js?v=f3ba7abdff`). A
deploy therefore cannot be driven by a script the browser cached earlier — the
symptom of which is new HTML behaving like the old build (for example steps not
hiding). If you ever need to check what a running server actually has, `GET
/health` reports its feature set.

## Tailwind

Tailwind is loaded from its CDN build so the project stays a single Python
application with no Node toolchain. `static/css/style.css` holds the few
component classes (fields, option tiles) that are clearer as plain CSS.
To ship a compiled stylesheet instead, run the Tailwind CLI over `templates/` and
swap the CDN `<script>` for the generated file — no other change is needed.
