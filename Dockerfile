# readanydoc HTTP API with the full engine (layout model, OCR, tesseract).
# Build: docker compose build   Run: docker compose up
# Page-reading models are reached over HTTP with your own key (ANTHROPIC_API_KEY / OPENAI_API_KEY); no key is
# baked in. The layout and OCR weights are downloaded into the data volume on first use.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# dependencies first, so code changes do not reinstall them
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --extra engine --no-install-project

COPY readanydoc ./readanydoc
RUN uv sync --frozen --no-dev --extra engine

ENV PATH="/app/.venv/bin:${PATH}" \
    READANYDOC_DATA_DIR=/data \
    READANYDOC_CACHE_DIR=/data/cache

RUN useradd --create-home --uid 10001 readanydoc \
    && mkdir -p /data \
    && chown -R readanydoc:readanydoc /data
USER readanydoc
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["readanydoc", "serve", "--host", "0.0.0.0", "--port", "8000"]
