# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy ALL sources first (so pip install -e . can read setuptools metadata).
# Layer caching is traded off against simplicity: every src change re-runs
# pip install, which is ~30-60s. For production builds this is acceptable;
# for local dev use docker-compose with volume mount instead.
COPY pyproject.toml ./
COPY src/ ./src/
COPY sql/ ./sql/
COPY README.md ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[dev]"

# Non-root user
RUN useradd -m -u 1000 v4g && chown -R v4g:v4g /app
USER v4g

# Default: start Flask. Render overrides via render.yaml for web vs worker.
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "src.web.app:app"]
