# Metadatarr

Metadatarr is a self-hosted web app for finding TBA episodes from Sonarr and preparing metadata suggestions and TVDB tickets. Sonarr is accessed read-only.

## Repository setup

Keep the directory structure intact. GitHub Actions runs from `.github/workflows/ci.yml`; it builds the frontend, checks Python syntax, runs both backend test suites, and builds a Docker image without publishing it. The workflow does not need production API keys or a `METADATARR_SECRET_KEY`.

Before the first commit:

1. Copy `.env.example` to `.env` only on the server where the existing Compose stack runs.
2. Keep `.env`, API keys, passwords, and the real `METADATARR_SECRET_KEY` out of GitHub.
3. Review `docker-compose.snippet.yml` before making the repository public. It is a snippet for an existing self-hosted stack, not a standalone Compose file.

## Checks

The backend tests can be run from the repository root after preparing a Python environment with the dependency listed in `requirements.txt`:

```bash
python -m unittest discover -s tests -v
python test_tba.py
```

The GitHub workflow installs the frontend dependencies without generating a lockfile, builds the frontend, runs the backend checks, and executes `docker build --tag metadatarr:ci .`. It does not push an image to a registry.

## Existing Docker stack

The supplied Compose snippet expects the app build context in `./tba-tickets`, exposes host port `8083` to container port `8080`, and persists the SQLite database under `/data`. Adapt it only as needed for the surrounding stack. Sonarr remains read-only.

Set `METADATARR_SECRET_KEY` on the server before using encrypted secret storage. Generate a strong value locally, keep it stable across restarts and upgrades, and store it only in the server-side `.env`. Do not commit or share the value.

After deployment, check:

- `http://<server-address>:8083/health`
- `http://<server-address>:8083/api/v1/health`
- `http://<server-address>:8083/app/`

Do not use a TVDB ticket submission as a health check.

## Current Phase 1 status

The Phase 1 backend suite contains 25 tests and the legacy regression suite contains 69 tests. Final Phase 1 acceptance is still pending a successful GitHub Actions run, Docker build, and runtime health checks.