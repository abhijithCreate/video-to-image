"""HTTP layer. Validation and orchestration only - logic lives in app.services."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import jobs
from app.assets import asset_url
from app.config import (
    ALLOWED_VIDEO_EXTENSIONS,
    BASE_DIR,
    QUALITY_PRESETS,
    settings,
)
from app.services import download as download_service
from app.services import image as image_service
from app.services import media_site as media_site_service
from app.services import video as video_service
from app.services import zip as zip_service

router = APIRouter()
templates = Jinja2Templates(directory=BASE_DIR / "templates")

CHUNK_SIZE = 1024 * 1024

CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "gif": "image/gif",
}

VIDEO_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}

RESOLUTION_PRESETS = [
    {"label": "Original", "value": "original"},
    {"label": "1920 x 1080", "value": "1920x1080"},
    {"label": "1280 x 720", "value": "1280x720"},
    {"label": "1024 x 576", "value": "1024x576"},
    {"label": "800 x 450", "value": "800x450"},
    {"label": "640 x 360", "value": "640x360"},
    {"label": "Custom", "value": "custom"},
]


class UrlRequest(BaseModel):
    url: str


class ProcessRequest(BaseModel):
    job_id: str
    method: str = Field(default="fps")
    fps: float | None = None
    count: int | None = None
    interval: float | None = None
    image_format: str = Field(default="jpg")
    quality_preset: str = Field(default="high")
    quality: int | None = None
    width: int | None = None
    height: int | None = None
    maintain_aspect: bool = True
    # Names the generated files. Sanitised server-side; blank keeps "frame".
    title: str | None = None
    # When set, previews are skipped entirely and only the ZIP is produced.
    zip_only: bool = False


class OutputTooLarge(Exception):
    """The requested settings would write more data than a job is allowed."""


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail=message)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "limits": {
                "max_upload_size_mb": settings.max_upload_size_mb,
                "max_video_duration_seconds": settings.max_video_duration_seconds,
                "max_output_images": settings.max_output_images,
                "job_retention_minutes": settings.job_retention_minutes,
                "retention_label": settings.retention_label,
                "max_download_clients": settings.max_download_clients,
                # The browser gives up a little after the server would, so a
                # stalled fetch surfaces as an error, not an endless spinner.
                "url_fetch_budget_seconds": (
                    settings.media_site_timeout_seconds
                    if media_site_service.enabled()
                    else settings.url_fetch_timeout_seconds
                )
                + 15,
            },
            "accepted_extensions": sorted(ALLOWED_VIDEO_EXTENSIONS),
            "asset": asset_url,
            "allow_url_uploads": settings.allow_url_uploads,
            "allow_media_site_urls": media_site_service.enabled(),
            # No FFmpeg means no conversion is possible on this host. Say so up
            # front rather than letting someone upload and hit an error.
            "processing_available": video_service.ffmpeg_available(),
            "stateless_conversion": settings.stateless_conversion,
            "accepted_labels": sorted(
                ext.lstrip(".").upper() for ext in ALLOWED_VIDEO_EXTENSIONS
            ),
            "image_formats": [f for f in image_service.FORMATS if f != "jpeg"],
            "quality_presets": QUALITY_PRESETS,
            "resolution_presets": RESOLUTION_PRESETS,
        },
    )


@router.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "ffmpeg": video_service.ffmpeg_available(),
            "env": settings.app_env,
            # Handy for confirming a deployment picked up the current build.
            "features": {
                "url_uploads": settings.allow_url_uploads,
                "media_sites": media_site_service.enabled(),
                "stateless_conversion": settings.stateless_conversion,
                # Without credentials a datacenter IP is usually refused, so
                # this is the first thing to check when links stop working.
                "media_site_cookies": media_site_service.cookies_configured(),
                "zip_only": True,
                "steps": 3,
            },
            "limits": {
                "max_output_images": settings.max_output_images,
                "job_retention_minutes": settings.job_retention_minutes,
            },
        }
    )


def _finalise_upload(job_id: str, path: Path, display_name: str) -> dict:
    """Probe an uploaded video, enforce the duration limit and record the job."""
    info = video_service.probe(path, original_filename=display_name)
    if info.duration > settings.max_video_duration_seconds:
        raise _bad_request(
            "This video is longer than the "
            f"{settings.max_video_duration_seconds} second limit."
        )
    if info.width <= 0 or info.height <= 0:
        raise _bad_request("This video has no usable picture dimensions.")

    meta = {
        "job_id": job_id,
        "created_at": time.time(),
        "status": "uploaded",
        "input": path.name,
        "video": info.as_dict(),
        "images": [],
    }
    jobs.write_meta(job_id, meta)
    return {
        "job_id": job_id,
        "video": info.as_dict(),
        "expires_in_minutes": settings.job_retention_minutes,
    }


@router.post("/api/upload")
async def upload(
    background: BackgroundTasks, file: UploadFile = File(...)
) -> JSONResponse:
    original_name = Path(file.filename or "").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise _bad_request(
            "Unsupported file type. Use "
            + ", ".join(sorted(e.lstrip(".").upper() for e in ALLOWED_VIDEO_EXTENSIONS))
            + "."
        )

    job_id, directory = await run_in_threadpool(jobs.create)
    destination = directory / f"input{extension}"

    try:
        await _receive(file, destination)
        payload = await run_in_threadpool(
            _finalise_upload, job_id, destination, original_name
        )
    except video_service.VideoError as exc:
        await run_in_threadpool(jobs.delete, job_id)
        raise _bad_request(str(exc)) from exc
    except HTTPException:
        await run_in_threadpool(jobs.delete, job_id)
        raise
    finally:
        await file.close()

    background.add_task(jobs.purge_expired)
    return JSONResponse(payload)


async def _receive(file: UploadFile, destination: Path) -> int:
    """Stream an upload to disk, stopping the moment it exceeds the limit."""
    written = 0
    with destination.open("wb") as handle:
        while chunk := await file.read(CHUNK_SIZE):
            written += len(chunk)
            if written > settings.max_upload_size_bytes:
                raise _bad_request(
                    f"This video is larger than the {settings.max_upload_size_mb} MB limit."
                )
            handle.write(chunk)
    if written == 0:
        raise _bad_request("The uploaded file is empty.")
    return written


def _extension_of(filename: str | None) -> str:
    """The validated extension of an uploaded filename."""
    name = Path(filename or "").name
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise _bad_request(
            "Unsupported file type. Use "
            + ", ".join(sorted(e.lstrip(".") for e in ALLOWED_VIDEO_EXTENSIONS)).upper()
            + "."
        )
    return extension


@router.post("/api/upload-url")
async def upload_url(
    request: UrlRequest, background: BackgroundTasks
) -> JSONResponse:
    """Fetch a video the user linked to, instead of uploading one."""
    if not settings.allow_url_uploads:
        raise _bad_request("Fetching videos from a URL is disabled on this server.")

    job_id, directory = await run_in_threadpool(jobs.create)
    try:
        path, display_name = await run_in_threadpool(
            download_service.fetch, url=request.url, directory=directory
        )
        payload = await run_in_threadpool(
            _finalise_upload, job_id, path, display_name
        )
    except (download_service.DownloadError, video_service.VideoError) as exc:
        await run_in_threadpool(jobs.delete, job_id)
        raise _bad_request(str(exc)) from exc
    except HTTPException:
        await run_in_threadpool(jobs.delete, job_id)
        raise

    background.add_task(jobs.purge_expired)
    return JSONResponse(payload)


def _encode_frames(
    *,
    source: Path,
    info: video_service.VideoInfo,
    plan: video_service.ExtractionPlan,
    options: image_service.ImageOptions,
    output_dir: Path,
    thumbs_dir: Path | None,
) -> tuple[list[dict], tuple[int, int] | None]:
    """Encode every planned frame, enforcing the output byte budget.

    Shared by the job pipeline and the single-request converter, so the limits
    and the fail-fast projection behave identically in both.
    ``thumbs_dir`` of ``None`` skips previews.
    """
    source_size = (info.width, info.height)
    target = image_service.compute_target_size(source_size, options)
    size = video_service.frame_size(source_size, target)

    def clear() -> None:
        for directory in (output_dir, thumbs_dir):
            if directory is not None and directory.is_dir():
                for stale in directory.iterdir():
                    stale.unlink(missing_ok=True)

    images: list[dict] = []
    written = 0
    budget = settings.max_total_output_bytes

    for index, timestamp, data in video_service.stream_frames(
        source=source,
        plan=plan,
        source_size=source_size,
        target_size=target,
    ):
        frame = image_service.open_frame(data, size)
        entry = image_service.save(
            frame=frame,
            destination=output_dir / options.frame_name(index),
            options=options,
            target_size=target,
        )
        thumb_name = None
        if thumbs_dir is not None:
            thumb_name = f"frame_{index:05d}.jpg"
            image_service.save_thumbnail(
                frame=frame,
                destination=thumbs_dir / thumb_name,
                longest_edge=settings.thumbnail_size,
            )
        entry.update({"frame": index, "timestamp": timestamp, "thumbnail": thumb_name})
        images.append(entry)
        written += entry["size_bytes"]

        # Fail fast: if the first frame already projects far past the budget,
        # say so now rather than after minutes of encoding.
        if index == 1 and entry["size_bytes"] * plan.count > budget * 1.25:
            (output_dir / entry["filename"]).unlink(missing_ok=True)
            projected = entry["size_bytes"] * plan.count / (1024 * 1024)
            raise OutputTooLarge(
                f"{plan.count} images at these settings would produce roughly "
                f"{projected:.0f} MB, over the {settings.max_total_output_mb} MB limit. "
                "Reduce the number of images or the resolution, or choose JPG or WebP "
                "instead of a lossless format."
            )

        if written > budget:
            clear()
            raise OutputTooLarge(
                f"These settings would produce more than {settings.max_total_output_mb} MB "
                "of images. Reduce the number of images or the resolution, or choose "
                "JPG or WebP instead of a lossless format."
            )

    return images, target


def _process(payload: ProcessRequest) -> dict:
    """Blocking pipeline: extract with FFmpeg, encode with Pillow, index results."""
    meta = jobs.read_meta(payload.job_id)
    directory = jobs.directory(payload.job_id)
    source = directory / meta["input"]
    if not source.is_file():
        raise jobs.JobError("This job has expired or no longer exists.")

    info = video_service.VideoInfo(**meta["video"])
    plan = video_service.resolve_plan(
        method=payload.method,
        info=info,
        fps=payload.fps,
        count=payload.count,
        interval=payload.interval,
    )
    options = image_service.resolve_options(
        fmt=payload.image_format,
        quality_preset=payload.quality_preset,
        quality=payload.quality,
        width=payload.width,
        height=payload.height,
        maintain_aspect=payload.maintain_aspect,
        title=payload.title,
    )
    output_dir = directory / "output"
    thumbs_dir = directory / "thumbs"
    for stale in list(output_dir.iterdir()) + list(thumbs_dir.iterdir()):
        stale.unlink(missing_ok=True)
    (directory / jobs.ARCHIVE_FILENAME).unlink(missing_ok=True)

    images, target = _encode_frames(
        source=source,
        info=info,
        plan=plan,
        options=options,
        output_dir=output_dir,
        thumbs_dir=None if payload.zip_only else thumbs_dir,
    )

    meta.update(
        {
            "status": "complete",
            "processed_at": time.time(),
            "options": {
                "method": plan.method,
                "fps": plan.fps,
                "interval": plan.interval,
                "format": options.fmt,
                "quality": options.quality if options.supports_quality else None,
                "width": target[0] if target else info.width,
                "height": target[1] if target else info.height,
                "maintain_aspect": options.maintain_aspect,
                "title": options.title,
            },
            "truncated": plan.truncated,
            "limit": settings.max_output_images,
            "zip_only": payload.zip_only,
            "images": images,
            "total_size_bytes": sum(item["size_bytes"] for item in images),
        }
    )

    if payload.zip_only:
        # Build the archive now: it is the only thing the user asked for.
        archive = zip_service.build_archive(
            files=[output_dir / item["filename"] for item in images],
            destination=directory / jobs.ARCHIVE_FILENAME,
        )
        meta["archive_size_bytes"] = archive.stat().st_size

    jobs.write_meta(payload.job_id, meta)
    jobs.touch(payload.job_id)
    return meta


@router.post("/api/process")
async def process(payload: ProcessRequest, background: BackgroundTasks) -> JSONResponse:
    try:
        meta = await run_in_threadpool(_process, payload)
    except jobs.JobError as exc:
        raise _not_found(str(exc)) from exc
    except (
        video_service.VideoError,
        image_service.ImageError,
        zip_service.ZipError,
        OutputTooLarge,
    ) as exc:
        raise _bad_request(str(exc)) from exc

    background.add_task(jobs.purge_expired)
    return JSONResponse(_public(meta))


def _public(meta: dict) -> dict:
    """Strip server-side details before returning a job to the browser."""
    job_id = meta["job_id"]
    zip_only = bool(meta.get("zip_only"))
    images = meta.get("images", [])
    return {
        "job_id": job_id,
        "status": meta.get("status"),
        "video": meta.get("video"),
        "options": meta.get("options"),
        "truncated": meta.get("truncated", False),
        "limit": meta.get("limit", settings.max_output_images),
        "zip_only": zip_only,
        "count": len(images),
        "total_size_bytes": meta.get("total_size_bytes", 0),
        "archive_size_bytes": meta.get("archive_size_bytes"),
        "retention_label": settings.retention_label,
        "max_download_clients": settings.max_download_clients,
        # In ZIP-only mode there is nothing to preview, so no per-image URLs are
        # published at all - only the archive.
        "images": [] if zip_only else [
            {
                **item,
                "url": f"/api/download/{job_id}/{item['filename']}",
                "thumbnail_url": f"/api/thumbnail/{job_id}/{item['thumbnail']}",
            }
            for item in images
        ],
        "download_all_url": f"/api/download-all/{job_id}",
    }


@router.post("/api/convert")
async def convert(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    method: str = Form("fps"),
    fps: float | None = Form(None),
    count: int | None = Form(None),
    interval: float | None = Form(None),
    image_format: str = Form("jpg"),
    quality_preset: str = Form("high"),
    quality: int | None = Form(None),
    width: int | None = Form(None),
    height: int | None = Form(None),
    maintain_aspect: bool = Form(True),
    title: str | None = Form(None),
) -> FileResponse:
    """Upload, convert and return the archive in one request.

    The three-step flow keeps a job on disk between requests, which only works
    where every request reaches the same machine. On a platform that spreads
    requests across instances the job written during upload is often missing by
    the time a preview or download arrives. This endpoint holds no state: the
    work directory is deleted once the response has been sent.
    """
    extension = _extension_of(file.filename)
    job_id, directory = await run_in_threadpool(jobs.create)
    source = directory / f"input{extension}"

    try:
        await _receive(file, source)

        def run() -> tuple[Path, str]:
            info = video_service.probe(source, original_filename=file.filename)
            if info.duration > settings.max_video_duration_seconds:
                raise _bad_request(
                    "This video is longer than the "
                    f"{settings.max_video_duration_seconds} second limit."
                )
            if info.width <= 0 or info.height <= 0:
                raise _bad_request("This video has no usable picture dimensions.")

            plan = video_service.resolve_plan(
                method=method, info=info, fps=fps, count=count, interval=interval
            )
            options = image_service.resolve_options(
                fmt=image_format,
                quality_preset=quality_preset,
                quality=quality,
                width=width,
                height=height,
                maintain_aspect=maintain_aspect,
                title=title,
            )
            output_dir = directory / "output"
            images, _ = _encode_frames(
                source=source,
                info=info,
                plan=plan,
                options=options,
                output_dir=output_dir,
                thumbs_dir=None,  # nothing survives to be previewed
            )
            archive = zip_service.build_archive(
                files=[output_dir / item["filename"] for item in images],
                destination=directory / jobs.ARCHIVE_FILENAME,
            )
            name = f"{options.title}.zip" if options.title else f"images-{job_id[:8]}.zip"
            return archive, name

        archive, download_name = await run_in_threadpool(run)
    except (
        video_service.VideoError,
        image_service.ImageError,
        zip_service.ZipError,
        OutputTooLarge,
    ) as exc:
        await run_in_threadpool(jobs.delete, job_id)
        raise _bad_request(str(exc)) from exc
    except HTTPException:
        await run_in_threadpool(jobs.delete, job_id)
        raise
    finally:
        await file.close()

    # Runs after the archive has been streamed, so nothing lingers on disk.
    background.add_task(jobs.delete, job_id)
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=download_name,
        background=background,
    )


@router.get("/api/result/{job_id}")
async def result(job_id: str) -> JSONResponse:
    try:
        meta = await run_in_threadpool(jobs.read_meta, job_id)
    except jobs.JobError as exc:
        raise _not_found(str(exc)) from exc
    return JSONResponse(_public(meta))


def _client_address(request: Request) -> str | None:
    """Best-effort client address, honouring a proxy's forwarding header."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.get("/api/download/{job_id}/{filename}")
async def download(job_id: str, filename: str, request: Request) -> FileResponse:
    try:
        path = await run_in_threadpool(jobs.resolve_file, job_id, "output", filename)
        await run_in_threadpool(
            jobs.register_download, job_id, _client_address(request)
        )
    except jobs.JobError as exc:
        raise _not_found(str(exc)) from exc

    suffix = path.suffix.lstrip(".").lower()
    return FileResponse(
        path,
        media_type=CONTENT_TYPES.get(suffix, "application/octet-stream"),
        filename=path.name,
        headers={"Cache-Control": "private, max-age=600"},
    )


@router.get("/api/preview/{job_id}")
async def preview(job_id: str) -> FileResponse:
    """Stream the uploaded video back for the on-page preview.

    Needed when the video was fetched from a URL, where the browser has no local
    copy to play. It is not a result download, so it does not count towards the
    distinct-downloader limit.
    """
    try:
        path = await run_in_threadpool(jobs.input_file, job_id)
    except jobs.JobError as exc:
        raise _not_found(str(exc)) from exc

    return FileResponse(
        path,
        media_type=VIDEO_CONTENT_TYPES.get(path.suffix.lower(), "video/mp4"),
        headers={"Cache-Control": "private, max-age=600"},
    )


@router.get("/api/thumbnail/{job_id}/{filename}")
async def thumbnail(job_id: str, filename: str) -> FileResponse:
    try:
        path = await run_in_threadpool(jobs.resolve_file, job_id, "thumbs", filename)
    except jobs.JobError as exc:
        raise _not_found(str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=600"},
    )


def _archive_name(job_id: str, meta: dict) -> str:
    """Download name for a job's ZIP: the user's title, else the job id."""
    title = (meta.get("options") or {}).get("title")
    return f"{title}.zip" if title else f"images-{job_id[:8]}.zip"


@router.get("/api/download-all/{job_id}")
async def download_all(job_id: str, request: Request) -> FileResponse:
    try:
        await run_in_threadpool(
            jobs.register_download, job_id, _client_address(request)
        )
        meta = await run_in_threadpool(jobs.read_meta, job_id)
        files = await run_in_threadpool(jobs.output_files, job_id)
        archive = await run_in_threadpool(
            zip_service.build_archive,
            files=files,
            destination=jobs.directory(job_id) / jobs.ARCHIVE_FILENAME,
        )
    except jobs.JobError as exc:
        raise _not_found(str(exc)) from exc
    except zip_service.ZipError as exc:
        raise _bad_request(str(exc)) from exc

    return FileResponse(
        archive,
        media_type="application/zip",
        filename=_archive_name(job_id, meta),
    )


@router.delete("/api/job/{job_id}")
async def delete_job(job_id: str) -> JSONResponse:
    try:
        await run_in_threadpool(jobs.delete, job_id)
    except jobs.JobError as exc:
        raise _not_found(str(exc)) from exc
    return JSONResponse({"deleted": True, "job_id": job_id})
