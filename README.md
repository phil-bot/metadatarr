# Metadatarr

Metadatarr is a self-hosted web app for finding TBA episodes from Sonarr and preparing metadata suggestions and TVDB tickets. Sonarr is accessed read-only.

## Repository setup

Keep the directory structure intact. GitHub Actions runs from `.github/workflows/ci.yml`; it builds the frontend, checks Python syntax, runs both backend test suites, and builds a Docker image. Pull requests, branch pushes, and manual runs never publish an image. Only a tag in the exact `vX.Y.Z` format can publish, and only after all required checks pass. CI does not need production API keys or a `METADATARR_SECRET_KEY`.

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

The GitHub workflow installs the frontend dependencies without generating a lockfile, builds the frontend, runs the backend checks, and executes `docker build --tag metadatarr:ci .`. This build does not push an image. A separate publish job depends on the successful validation job and runs only for valid version-tag pushes.

## Releasing a versioned container image

1. In the GitHub repository, open **Releases** and choose **Draft a new release**.
2. Choose **Select tag** and enter a new tag such as `v1.2.3`; choose **Create new tag: v1.2.3 on publish** and set the target to `main`.
3. Add a release title and publish the release. Creating the tag triggers the workflow.
4. Wait for the GitHub Actions run to finish successfully. The publish job is gated on the frontend build, Python checks, both backend test suites, the no-lockfile checks, and the Docker build.

A successful release of `v1.2.3` publishes these tags:

- `ghcr.io/phil-bot/metadatarr:1.2.3`
- `ghcr.io/phil-bot/metadatarr:latest`

The workflow authenticates with its job-scoped `GITHUB_TOKEN` (`packages: write` only on the publish job); no PAT or repository secret is needed. For unauthenticated pulls from a server, make the GHCR package **Public** in its package settings after the first publication. Public GHCR container images can be pulled anonymously. See [GitHub's package visibility documentation](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

To pull a specific release on the server:

```bash
docker pull ghcr.io/phil-bot/metadatarr:1.2.3
```

When switching the existing Compose service to the registry image later, replace only its current `build: ./tba-tickets` line with:

```yaml
image: ghcr.io/phil-bot/metadatarr:1.2.3
```

Keep the service's other ports, volumes, environment variables, dependencies, and labels unchanged. Then pull and recreate that service with the existing Compose project, for example `docker compose pull tba-tickets` followed by `docker compose up -d tba-tickets`.

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