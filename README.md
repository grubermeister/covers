# WorldCovers

WorldCovers is a Django and React application for cataloging stampless covers
and postal markings.

The public beta runs at [hellowoco.app](https://hellowoco.app/).

## Project Overview

WorldCovers has three main code areas:

- [Common model](./backend/common): shared Django models, admin resources,
  API resources, and management commands.
- [WoCo server](./backend/woco): Django settings, URL routing, and server
  entry points.
- [Web UI](./frontend): React SPA served by Django in production and by Vite
  during frontend development.

Public Help content is served from Markdown files in [docs](./docs) by
`backend/common/api/help.py`. Files under `docs/devel/` are internal
developer and operator docs and are not exposed in the live Help page.

For design and scope details, see [docs/devel/design.md](./docs/devel/design.md)
and [docs/devel/scope.md](./docs/devel/scope.md).

## Quickstart

Prerequisites:

- Python 3.13, pinned by [.python-version](./.python-version)
- `uv`, installed with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Node.js and npm for the frontend build
- MySQL 8 with credentials in `mysql.cnf`

From the repo root:

```sh
uv sync
cp mysql.cnf.example mysql.cnf
cd frontend && npm ci && npm run build && cd ..
./woco migrate
./woco collectstatic --noinput
./woco dev
```

`./woco` is the repo-local CLI shim for Django management commands. It wraps
`uv run woco`, which calls `woco_cli.py` and then Django's
`execute_from_command_line`.

On Windows, use `.\woco.bat` in place of `./woco`.

For full setup details, see [docs/devel/BUILD.md](./docs/devel/BUILD.md).

## Development

For day-to-day development, run:

```sh
./woco dev
```

With `DEBUG=True`, this starts Vite on `http://localhost:8080` and Django on
`http://localhost:8000`. Open the Vite URL for frontend hot reload. API,
admin, media, and static requests are proxied to Django.

With `DEBUG=False`, `./woco dev` builds `frontend/dist/` and serves the built
SPA through Django at `http://127.0.0.1:8000`.

## Deployment And Operations

The staging deployment source of truth is:

- [.github/workflows/build-and-deploy.yml](./.github/workflows/build-and-deploy.yml)
- [deploy/deploy.sh](./deploy/deploy.sh)
- [deploy/worldcovers.service](./deploy/worldcovers.service)

The GitHub Actions workflow stops and starts the `worldcovers` service and
installs the systemd unit when it changes. `deploy/deploy.sh` runs dependency
sync, migrations, frontend build, and Django static collection.

For deployment details, see [docs/devel/DEPLOY.md](./docs/devel/DEPLOY.md).
For operator tasks, see [docs/devel/RUNBOOK.md](./docs/devel/RUNBOOK.md).
For ETL and management commands, see [docs/devel/TOOLS.md](./docs/devel/TOOLS.md).
For the ASCC catalog pipeline, see [docs/devel/PIPELINE.md](./docs/devel/PIPELINE.md).

## Public Documentation

The live Help page is backed by these repo-level Markdown files:

- [docs/faq.md](./docs/faq.md)
- [docs/glossary.md](./docs/glossary.md)
- [docs/vision.md](./docs/vision.md)
- [docs/acknowledgements.md](./docs/acknowledgements.md)

Developer and operator docs stay under `docs/devel/` and are intentionally
excluded from the Help API.

## Versioning

The app version is a single string in [VERSION](./VERSION) at the repo root.
`pyproject.toml` reads it dynamically via `[tool.hatch.version]`, and
`frontend/vite.config.ts` reads the same file to build the `__APP_VERSION__`
constant shown in the site footer. To release a new version, edit `VERSION`
only -- nothing else needs to change.

`frontend/package.json` also carries a `version` field because npm requires
one; it is not read by the app and can drift. Treat `VERSION` as canonical.

## License And Contributions

For licensing details, see [LICENSE](./LICENSE).

Contribution policy and public issue-tracker links are not defined in this
repository yet. Until they are, coordinate changes through the project team.

Parts of this codebase were generated with AI assistance. Changes still require
human review before acceptance.
