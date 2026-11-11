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

1. Choose a **new, unused** release version. Update the `version` in `frontend/package.json` to the matching numeric value (for example, `0.1.2`), commit it to a branch, merge to `main`, and wait for the PR/branch CI checks to pass. The workflow requires the version tag to match this value exactly.
2. Confirm that the corresponding Git tag has not already been used. The earlier `v0.1.1` was used for a CI test; do not repurpose it as a release tag. Use a fresh version (for example, `v0.1.2` only if it is unused and `frontend/package.json` is `0.1.2`).
3. In GitHub, open **Releases** and choose **Draft a new release**. Create the matching `vX.Y.Z` tag on `main`, add a release title, then publish.
4. Wait for GitHub Actions to finish and confirm that the separate `publish` job succeeded. A green PR run alone does not publish an image.

For example, if `v0.1.2` is still unused and `frontend/package.json` is set to `0.1.2`, a successful release would publish:

- `ghcr.io/phil-bot/metadatarr:0.1.2`
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

For a later release, change only the version in this `image:` line (for example, to `0.1.2` if that is the new version you released). Keep the other service settings unchanged. From the existing Compose project directory, pull and recreate the service:

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

## Phase 2 status

Metadatarr now reads the complete Sonarr series, season, and episode inventory, including available series and episode overviews. `ORIGINAL_LANGUAGE` remains visible as a legacy setting but no longer filters imported series. Sonarr text language is never inferred from a series' original language: set `SONARR_METADATA_LANGUAGE` only when the language of text returned by Sonarr is known; otherwise language matching is reported as not checkable.

The API provides read-only inventory endpoints (`GET /api/v1/series` and `GET /api/v1/series/<id>`) and local configuration endpoints for target languages and placeholder rules. CSRF-protected, same-origin `PUT` endpoints update only Metadatarr's SQLite settings; they do not write to Sonarr. Environment-seeded rules remain read-only.

Placeholder rules support empty, exact, or restricted-regex matching for series and episode titles and overviews. Sonarr does not provide season titles or overviews, so those fields are represented as not supplied rather than inferred. Poster fetching supports Sonarr URL Base paths, reports causes through `X-Poster-Reason`, enforces a 6,000,000-byte image limit, and uses a cache bounded to 32 MiB and 400 entries. Only same-origin `MediaCover` paths are accepted; remote artwork URLs are ignored.

## Phase 3 status

The four built-in sources are registered separately from the TVDB ticket action. DeepL outputs are stored as machine-translated candidates with a link to the exact source candidate. SQLite schema version 2 adds candidate provenance, backfills existing suggestions without rewriting the legacy suggestion/ticket tables, and records action previews. Startup verifies both migration checksums; the migration creates and checks a backup before applying additive changes.

The local JSON API includes ticket list/detail/export and draft creation, source/action status, source search, and a guarded TVDB ticket action. External submission requires an enabled action, a current server-stored preview, explicit confirmation, and a unique idempotency key. The old HTML submit route is non-writing. The nightly search uses only enabled, nightly-approved Mediathek/TMDB sources; it does not submit tickets. Sonarr remains GET-only.

Phase 3 validation (user-reported, 11.11.2026): On the NAS, `python3 -m unittest discover -s tests -v` passed 61/61 tests and `python3 test_tba.py` passed 71/71 checks. The PR-triggered GitHub CI succeeded and the PR was merged. The user built a local image and started a container on the NAS; `/health` and `/api/v1/health` returned HTTP 200 on `127.0.0.1:8083`, and the JSON endpoint reported `status: ok`. The user also reported that the additional Phase 3 functional checks were all verified and working. Provider/action automated tests use fixtures or mocked HTTP; whether any live provider call or real TVDB submission was performed manually has not been specified. The frontend remains Phase 4 work and was not changed for Phase 3. No tag was created for the PR run, and no new registry release or GHCR publication is reported; the last documented published image remains `0.1.0`. `package-lock.json` remains absent.
