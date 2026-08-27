---

name: video-to-image
description: Build and maintain a clean single-application Python video-to-image converter using FastAPI, Jinja2, Tailwind CSS, FFmpeg, Pillow, Docker, and Vercel-compatible deployment.
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# Video to Image Converter

Build a clean, modern, production-quality **single Python web application** for converting video frames into images.

## Core Requirements

Use only:

* Python 3.12+
* FastAPI
* Jinja2
* Tailwind CSS
* Minimal vanilla JavaScript
* FFmpeg / ffprobe
* Pillow
* Docker
* Git

Do **not** create a separate frontend application.

Do **not** use:

* React
* Next.js
* Vue
* Angular
* Node.js frontend
* Django
* Database unless explicitly required

FastAPI must serve the HTML pages directly using Jinja2.

---

## Application Goal

Create a modern alternative to:

https://ezgif.com/video-to-jpg

The application should allow users to:

1. Upload a video
2. Preview the video
3. Extract frames
4. Select extraction method
5. Select image format
6. Select image quality
7. Select image resolution
8. Preview generated images
9. Download individual images
10. Download all images as ZIP
11. Start a new conversion

---

## Supported Video Formats

Support common formats such as:

* MP4
* WebM
* MOV
* AVI
* MKV
* MPEG
* M4V

Validate files on the backend using both extension and actual media information.

---

## Frame Extraction

Provide these options:

### Every Frame

Extract every video frame, subject to configured safety limits.

### FPS

Allow:

* 0.1
* 0.5
* 1
* 2
* 5
* 10
* Custom

### Number of Images

Allow the user to specify how many images to generate.

### Time Interval

Allow extraction every:

* 1 second
* 2 seconds
* 5 seconds
* 10 seconds
* Custom

---

## Image Formats

Support:

* JPG / JPEG
* PNG
* WebP
* BMP
* TIFF
* GIF

Use **Pillow** for image conversion.

Format-specific settings should be displayed only when relevant.

---

## Image Quality

Provide:

* Low
* Medium
* High
* Very High
* Custom

Recommended values:

```text
Low       = 50
Medium    = 70
High      = 85
Very High = 95
```

For JPG/WebP, provide a quality slider.

For PNG, explain that PNG is lossless and uses compression rather than JPEG-style quality.

---

## Resolution

Provide:

* Original
* 1920 × 1080
* 1280 × 720
* 1024 × 576
* 800 × 450
* 640 × 360
* Custom

Maintain aspect ratio by default.

---

## Video Information

After upload, show:

* Filename
* File size
* Duration
* Resolution
* FPS
* Format

Use FFmpeg/ffprobe to determine metadata.

---

## UI Design

Create a **clean, modern SaaS-style utility interface**.

Use Tailwind CSS.

The UI should be:

* Minimal
* Professional
* Responsive
* Accessible
* Fast
* Easy to understand

Use:

* Cards
* Clear sections
* Good spacing
* Strong typography
* Subtle borders
* Soft shadows
* Clear primary CTA
* Drag-and-drop upload area
* Progress indicators
* Empty/loading/error/success states

Avoid excessive gradients, animations, or unnecessary UI.

Support:

* Light mode
* Dark mode
* Mobile
* Tablet
* Desktop

---

## Main Page

Use a single main page.

Suggested flow:

```text
┌─────────────────────────────────────────────┐
│              Video to Image                 │
│                                             │
│   Extract high-quality images from video    │
│                                             │
│        ┌─────────────────────────┐          │
│        │   Drag & Drop Video     │          │
│        │                         │          │
│        │    [ Choose Video ]     │          │
│        └─────────────────────────┘          │
│                                             │
├─────────────────────────────────────────────┤
│ Video Preview          Conversion Options   │
│                                             │
│ ▶ Video               Frame Extraction      │
│                        Image Format         │
│                        Quality              │
│                        Resolution           │
│                        Advanced Options     │
│                                             │
│                 [ Generate Images ]         │
└─────────────────────────────────────────────┘
```

After processing, replace/show the results section:

```text
Generated Images

100 images generated

[ Download All ]

┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│      │ │      │ │      │ │      │
│ IMG  │ │ IMG  │ │ IMG  │ │ IMG  │
│      │ │      │ │      │ │      │
└──────┘ └──────┘ └──────┘ └──────┘
```

---

## Image Preview

Clicking an image should open a simple accessible lightbox.

Show:

* Large image
* Frame number
* Timestamp
* Resolution
* File format
* Download button

Support:

* Previous
* Next
* ESC to close
* Keyboard navigation

---

## Backend Architecture

Keep the application simple.

Recommended:

```text
app/
├── main.py
├── config.py
├── routes.py
└── services/
    ├── video.py
    ├── image.py
    └── zip.py
```

Responsibilities:

### `video.py`

Handle:

* FFmpeg
* ffprobe
* Video metadata
* Frame extraction

### `image.py`

Handle:

* Pillow
* Format conversion
* Quality
* Resize
* Compression

### `zip.py`

Handle:

* ZIP creation
* Download preparation

Keep business logic out of route handlers.

---

## API

Use simple FastAPI endpoints:

```text
GET  /
GET  /health
POST /api/upload
POST /api/process
GET  /api/result/{job_id}
GET  /api/download/{job_id}/{filename}
GET  /api/download-all/{job_id}
DELETE /api/job/{job_id}
```

Use JSON responses for API requests.

Use Jinja2 for the main HTML page.

---

## Temporary Files

Do not permanently store uploaded videos.

Use temporary job directories:

```text
/tmp/video-to-image/
└── <job-id>/
    ├── input.mp4
    ├── frames/
    └── output/
```

Use UUIDs for job IDs.

Automatically clean temporary files.

---

## Security

Implement:

* File size limits
* Video duration limits
* Maximum output image limits
* File type validation
* Safe filenames
* Path traversal protection
* Processing timeouts
* Temporary file cleanup
* Safe FFmpeg subprocess execution

Never use:

```python
shell=True
```

with user-controlled input.

Never expose server paths or Python stack traces to users.

---

## Configuration

Use environment variables.

Create `.env.example`:

```env
APP_ENV=development

MAX_UPLOAD_SIZE_MB=50
MAX_VIDEO_DURATION_SECONDS=60
MAX_OUTPUT_IMAGES=100

JOB_RETENTION_MINUTES=30
TEMP_DIR=/tmp/video-to-image
```

Never commit `.env`.

---

## Docker

Provide:

* `Dockerfile`
* `docker-compose.yml`
* `.dockerignore`

The application must run with:

```bash
docker compose up --build
```

and be available at:

```text
http://localhost:8000
```

The Docker image must contain FFmpeg.

Use a non-root user where practical.

---

## Vercel

Prepare the application for Vercel deployment where technically supported.

Keep processing limits conservative because Vercel uses serverless execution.

Do not design the application around:

* Persistent processes
* Unlimited video processing
* GPU processing
* Long-running workers
* Ollama
* Large local AI models

Keep video/image processing logic isolated so it can later be moved to another hosting provider without changing the UI/API architecture.

---

## Accessibility

Implement:

* Semantic HTML
* Proper form labels
* Keyboard navigation
* Visible focus states
* Accessible dialogs
* ARIA only where necessary
* Screen-reader-friendly status messages
* Good color contrast
* Reduced-motion support

---

## Performance

Prioritize:

* Fast initial page load
* Lazy-loaded result images
* Efficient FFmpeg processing
* Efficient image conversion
* Temporary file cleanup
* Streaming downloads where practical

Do not unnecessarily load all full-resolution images into browser memory.

---

## Project Structure

Use a simple structure:

```text
video-to-image/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── routes.py
│   └── services/
│       ├── video.py
│       ├── image.py
│       └── zip.py
│
├── templates/
│   └── index.html
│
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js
│
├── tests/
│
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .gitignore
├── .env.example
├── vercel.json
└── README.md
```

---

## Development Rules

Before modifying the project:

1. Inspect the existing code.
2. Reuse existing functionality.
3. Avoid unnecessary dependencies.
4. Keep Python code modular.
5. Keep UI simple.
6. Do not over-engineer.
7. Validate everything server-side.
8. Test every major feature.

After implementation:

```bash
pytest
```

Then test:

```bash
docker compose up --build
```

Verify:

```text
http://localhost:8000
```

---

## Quality Standard

The final application should feel like a **real, polished online utility**, not a Python demo.

Prioritize:

1. Clean UI
2. Simple UX
3. Correct video processing
4. Reliable image conversion
5. Security
6. Accessibility
7. Mobile responsiveness
8. Docker support
9. Vercel compatibility
10. Maintainable code

Keep the implementation **simple, clean, and production-ready**.
