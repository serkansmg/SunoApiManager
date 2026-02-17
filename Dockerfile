# ─────────────────────────────────────────────────────────────
# Suno Manager — Docker Image
# Base: python:3.12-slim + ffmpeg (no conda overhead)
# ─────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies (ffmpeg for silence analysis)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install Python dependencies ─────────────────────────────
COPY requirements.txt .

# Install all deps except playwright (heavy + optional)
# Also install websockets for uvicorn WebSocket support
RUN pip install --no-cache-dir \
    $(grep -v '^playwright' requirements.txt | tr '\n' ' ') \
    websockets

# ── Copy application code ───────────────────────────────────
COPY app.py suno_api.py suno_models.py suno_router.py \
     database.py audio_analyzer.py captcha_solver.py ./
COPY templates/ ./templates/
COPY static/ ./static/

# ── Create data directories ─────────────────────────────────
RUN mkdir -p downloads uploads logs data

# ── Set DB path to data directory (mountable volume) ────────
ENV SUNO_DB_PATH=/app/data/suno_manager.db

# ── Expose port and health check ────────────────────────────
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/docs')" || exit 1

# ── Start the server ────────────────────────────────────────
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
