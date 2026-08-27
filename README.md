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

Run from the project root: templates and static files are resolved relative to
the working directory.

## Tests

```bash
pytest
```

Tests that need a real video are skipped automatically when FFmpeg is not on
`PATH`; `GET /health` reports whether the server can see it.

## Configuration

All settings come from the environment (see `.env.example`). Never commit `.env`.

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
└── services/
    ├── video.py     ffprobe metadata, extraction plans, FFmpeg frame decoding
    ├── image.py     Pillow: format, quality, resize, compression
    ├── download.py  fetching a video from a URL, with SSRF protection
    └── zip.py       archive creation
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
- **Bot checks happen, and are usually temporary.** YouTube challenges clients
  by IP — after a burst of requests, and far more readily from a datacenter than
  from a home or office connection. See *When the bot check fires* below.
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
page and leaves direct file links working.

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

### Docker

Runs as a non-root user (uid 10001). Jobs live on the container's own ephemeral
layer rather than a `tmpfs`: a 500-image batch can run to hundreds of megabytes,
which is more than is sensible to hold in RAM. Nothing is persisted — the
directory dies with the container, and the retention sweep clears jobs long
before that. `docker compose up --build` serves port 8000.

### Vercel

`vercel.json` deploys `app/main.py` as a Python serverless function with
conservative limits (20 MB upload, 20 s duration, 100 images). Serverless caveats to know before
relying on it:

- **The Vercel Python runtime does not include FFmpeg.** Uploads will be rejected
  with a clear message until you make an `ffmpeg`/`ffprobe` binary available and
  point `FFMPEG_PATH` / `FFPROBE_PATH` at it.
- Instances are ephemeral: a job may be processed on one instance and downloaded
  from another, which loses the result. Long clips also risk the invocation
  timeout.
- Video-site links are a poor fit for serverless: extraction plus download eats
  the invocation budget, and the platform's IPs are the ones sites block hardest.
  Consider `ALLOW_MEDIA_SITE_URLS=false` there.

For anything beyond light use, deploy the Docker image to a host with a
persistent filesystem (Fly.io, Railway, Render, a VPS). The API and UI are
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

`style.css` and `app.js` are referenced through `asset()`, which stamps each URL
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
