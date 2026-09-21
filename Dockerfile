# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=0 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Minimal system setup: non-root user; all dependencies are pure Python
# wheels, so no extra OS packages are needed.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

# Install dependencies first so the layer is cached across source changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application and tests
COPY app/ ./app/
COPY tests/ ./tests/

# Startup initialization performed at image build time: byte-compile all
# sources so first request never pays import/compile cost.
RUN python -m compileall -q app tests

USER appuser

EXPOSE 8000

# Container-level healthcheck (Compose also reports this state).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json,urllib.request,sys; \
sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8000/health'))['status']=='ok' else 1)"

# Single-process uvicorn is sufficient for deterministic results; scale
# horizontally via Compose --scale if needed.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
