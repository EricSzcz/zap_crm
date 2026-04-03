# ── Build stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies into a virtual env inside the image
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim

# System dependencies for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the virtual env from the builder
COPY --from=builder /app/.venv /app/.venv

# Copy the application source
COPY . .

# Make the venv the active Python environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create a non-root user
RUN useradd --no-create-home --shell /bin/false appuser \
    && chown -R appuser:appuser /app
USER appuser

# Collect static files (requires SECRET_KEY at build time; use a dummy)
RUN SECRET_KEY=dummy-build-key python manage.py collectstatic --noinput

EXPOSE 8000

# Entrypoint: migrate then start Daphne (ASGI, needed for Django Channels)
CMD ["sh", "-c", "python manage.py migrate --noinput && daphne -b 0.0.0.0 -p 8000 zap_crm.asgi:application"]
