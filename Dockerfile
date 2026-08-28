FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TEMP_DIR=/tmp/video-to-image \
    PORT=8000

# The application does NOT need this: decoding runs in-process through PyAV.
# It is here only so the test suite can synthesise sample videos with the CLI.
# Drop it for a slimmer production image; the app is unaffected.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

# The full set: this image both runs the app and runs its test suite.
COPY requirements.txt requirements-extra.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY app ./app
COPY templates ./templates
COPY static ./static

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /tmp/video-to-image \
    && chown -R appuser:appuser /srv/app /tmp/video-to-image
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; url='http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health'; sys.exit(0 if urllib.request.urlopen(url, timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
