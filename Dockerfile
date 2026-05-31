FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# `curl` is needed by Coolify's container healthcheck. The previous block
# of libnss3/libxkbcommon0/etc here was for Playwright/Chromium back when
# Zoopla scraping needed a browser; we're on curl_cffi now (libcurl
# impersonation, no browser), so those are dropped — saves ~50MB.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Copy dependency manifests first for better layer caching
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra api

# Copy application code
COPY app ./app
COPY property_core ./property_core

# Run as a non-root user so a compromise (scraping stack / dep RCE) doesn't run
# as uid 0 in-container (audit M7).
RUN useradd -u 1001 -m appuser && chown -R appuser /app /opt/venv
USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

