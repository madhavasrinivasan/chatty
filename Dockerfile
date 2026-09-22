# syntax=docker/dockerfile:1
# Alpine + uv. crawl4ai/patchright removed so musl wheels work.
# Never copy host .venv.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-alpine

RUN apk add --no-cache \
    ca-certificates \
    build-base \
    libffi-dev \
    openssl-dev \
    cargo \
    rust \
    zlib-dev \
    jpeg-dev \
    libpng-dev \
    freetype-dev \
    linux-headers \
    python3-dev \
    libffi \
    openssl \
    libstdc++ \
    libjpeg \
    zlib \
    libpng \
    freetype

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=3009

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py ./
COPY app ./app

RUN mkdir -p /app/Assets/PDF /app/rag /app/secrets

EXPOSE 3009

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3009"]
