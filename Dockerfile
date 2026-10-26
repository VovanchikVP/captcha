# ---- сборка зависимостей ----
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# слой с зависимостями кэшируется отдельно от кода
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# ---- рантайм ----
FROM python:3.13-slim-bookworm

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY app.py ./
COPY static ./static

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# host/port берутся из UVICORN_HOST/UVICORN_PORT — явные флаги здесь перебили бы env
CMD ["uvicorn", "app:app"]
