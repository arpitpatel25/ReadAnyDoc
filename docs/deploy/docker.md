# Docker

The repository ships a `Dockerfile` and a `docker-compose.yml` for the server with the full engine (layout model,
OCR and Tesseract). No key is baked into the image.

```bash
git clone https://github.com/arpitpatel25/ReadAnyDoc && cd ReadAnyDoc
export ANTHROPIC_API_KEY=sk-ant-...
export READANYDOC_API_KEY=choose-a-long-random-key
docker compose up --build -d
curl -s http://localhost:8000/version
```

The first parse downloads the layout weights into the `/data` volume (about 90 seconds once); later parses and
restarts reuse them.

## What the image contains

- Python 3.12 slim, `readanydoc[engine]`, Tesseract.
- Runs as a non-root user (uid 10001), port 8000, a health check on `/health`.
- `/data` volume: jobs database, uploads, results, caches and model weights.

## Settings

Pass any [configuration](configuration.md) variable through the environment. `docker-compose.yml` lists the common
ones with their defaults:

```yaml
environment:
  READANYDOC_API_KEY: ${READANYDOC_API_KEY:-}
  READANYDOC_PROFILE: ${READANYDOC_PROFILE:-engine}
  READANYDOC_READER: ${READANYDOC_READER:-}
  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
  OPENAI_API_KEY: ${OPENAI_API_KEY:-}
  READANYDOC_WORKERS: ${READANYDOC_WORKERS:-4}
```

## A model server on the same host

`host.docker.internal` reaches the host from the container:

```bash
READANYDOC_READER="openai_compatible:my-model@http://host.docker.internal:8001/v1" docker compose up
```

## Running in the cloud

The image runs anywhere containers run (AWS ECS or App Runner, Google Cloud Run, Azure Container Apps, Fly.io,
Kubernetes). Give it a persistent volume for `/data`, set `READANYDOC_API_KEY`, keep the provider key in your
platform's secret store, and put it behind HTTPS. See [Security](security.md) and
[Performance and scaling](performance.md).
