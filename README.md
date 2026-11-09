# Metadatarr

Metadatarr is a self-hosted web app for finding TBA episodes from Sonarr and preparing metadata suggestions and TVDB tickets. Sonarr is accessed read-only.

## Repository setup

Keep the directory structure intact. GitHub Actions runs from `.github/workflows/ci.yml`; it builds the frontend, checks Python syntax, runs both backend test suites, and builds a Docker image. Pull requests, branch pushes, and manual runs never publish an image. Only a tag in the exact `vX.Y.Z` format can publish, and only after all required checks pass. CI does not need production API keys or a `METADATARR_SECRET_KEY`.

Before committing changes:

1. Copy `.env.example` to `.env` only on the server where the existing Compose stack runs.
2. Keep `.env`, API keys, passwords, and the real `METADATARR_SECRET_KEY` out of GitHub.
3. Review `docker-compose.snippet.yml` before making the repository public. It is a snippet for an existing self-hosted stack, not a standalone Compose file.

## Checks

The backend tests can be run from the repository root after preparing a Python environment with the dependency listed in `requirements.txt`:

```bash
python -m unittest discover -s tests -v
python test_tba.py
```

The GitHub workflow installs the frontend dependencies without generating a lockfile, builds the frontend, runs the backend checks, and executes `docker build --tag metadatarr:ci .`. This build does not push an image. A separate publish job depends on the successful validation job and runs only for valid version-tag pushes.

## Releasing a versioned container image

**Current published version:** `v0.1.0`. The public GHCR package is available at `ghcr.io/phil-bot/metadatarr`; its version tag is `0.1.0` and `latest` currently points to that release. The image can be pulled without a registry login.

1. In the GitHub repository, open **Releases** and choose **Draft a new release**.
2. For the next release, choose **Select tag** and enter a new tag such as `v0.1.1`; choose **Create new tag: v0.1.1 on publish** and set the target to `main`.
3. Add a release title and publish the release. Creating the tag triggers the workflow.
4. Wait for the GitHub Actions run to finish successfully. The publish job is gated on the frontend build, Python checks, both backend test suites, the no-lockfile checks, and the Docker build.

A successful release of `v0.1.1` would publish these tags:

- `ghcr.io/phil-bot/metadatarr:0.1.1`
- `ghcr.io/phil-bot/metadatarr:latest`

The workflow authenticates with its job-scoped `GITHUB_TOKEN` (`packages: write` only on the publish job); no PAT or repository secret is needed. The GHCR package is now **Public**, so the server can pull images anonymously without a GHCR login. See [GitHub's package visibility documentation](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

To pull a specific release on the server:

```bash
docker pull ghcr.io/phil-bot/metadatarr:0.1.0
```

The supplied Compose example is already pinned to the released image:

```yaml
image: ghcr.io/phil-bot/metadatarr:0.1.0
```

For a later release, change only the version in this `image:` line (for example, to `0.1.1`). Keep the other service settings unchanged. From the existing Compose project directory, pull and recreate the service:

```bash
docker compose pull tba-tickets
docker compose up -d tba-tickets
```

## Existing Docker stack

The supplied Compose snippet uses the published image `ghcr.io/phil-bot/metadatarr:0.1.0`, exposes host port `8083` to container port `8080`, and persists the SQLite database under `/data`. For upgrades, change only the pinned image tag and pull the service again. Sonarr remains read-only.

Set `METADATARR_SECRET_KEY` on the server before using encrypted secret storage. Generate a strong value locally, keep it stable across restarts and upgrades, and store it only in the server-side `.env`. Do not commit or share the value.

After deployment, check:

- `http://<server-address>:8083/health`
- `http://<server-address>:8083/api/v1/health`
- `http://<server-address>:8083/app/`

Do not use a TVDB ticket submission as a health check.

## Current Phase 1 status

The Phase 1 backend suite contains 25 tests and the legacy regression suite contains 69 tests. Version `v0.1.0` is published in GHCR after the release workflow completed its checks and Docker build. Final Phase 1 acceptance is still pending runtime health checks.