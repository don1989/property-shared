FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# `curl` is needed by Coolify's container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY property_core ./property_core
COPY property_app ./property_app
RUN uv sync --frozen --no-dev --extra apps

# Run as a non-root user so a compromise doesn't run as uid 0 (audit M7).
RUN useradd -u 1001 -m appuser && chown -R appuser /app /opt/venv
USER appuser

EXPOSE 8080

CMD ["property-app"]
