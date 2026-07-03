# WorldCovers

WorldCovers is a Django and React application for cataloging stampless covers
and postal markings.

The staging/beta host runs at [woco.dev](https://woco.dev/). Production
deployment is currently `hellowoco.app`.

> "Another success is the post-office, with its educating energy augmented by cheapness and guarded by a certain religious sentiment in mankind; so that the power of a wafer or a drop of wax or gluten to guard a letter, as it flies over sea over land and comes to its address as if a battalion of artillery brought it, I look upon as a fine meter of civilization."

&nbsp;&nbsp;&nbsp;&nbsp;-- _Ralph Waldo Emerson_

## Project Overview

WorldCovers has three main code areas:

- [Common model](./backend/common): shared models, admin resources,
  API, and management commands.
- [WoCo server](./backend/woco): Django settings, URL routing, and server
  entry points.
- [Web UI](./frontend): React SPA served by Django in production and by Vite
  during frontend development.

Public Help content is served from Markdown files in [docs](./docs) by
`backend/common/api/help.py`. Files under `docs/devel/` are internal
developer and operator docs and are not exposed in the live Help page.

For design and scope details, see [docs/devel/design.md](./docs/devel/design.md)

## Quickstart

Prerequisites:

- Local development has been tested on macOS. Linux should work with the same
  commands when the prerequisites below are installed.
- Python 3.13, pinned by [.python-version](./.python-version)
- `uv`, installed with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Node.js and npm for the frontend build
- MySQL 8, running locally. `./woco setup dev` needs either `sudo mysql`
  access or the MySQL root password to create the local database and app user.

From the repo root, run the one-command setup:

```sh
./woco setup dev
```

On a fresh clone this installs dependencies, creates `.env` with a generated
secret key, prompts for local database settings, creates the MySQL database
and app user, writes `mysql.cnf`, builds the frontend, runs migrations, and
collects static files. Press Enter at the app-password prompt to generate a
random password. The command is idempotent -- safe to re-run any time.

For unattended setup, pass the values as environment variables:

```sh
WOCO_DB_PASSWORD=<app-db-password> \
WOCO_MYSQL_ROOT_PASSWORD=<mysql-root-password> \
./woco setup dev
```

`WOCO_DB_NAME` and `WOCO_DB_USER` are optional; defaults are `worldcovers`
and `wocod`. Omit `WOCO_MYSQL_ROOT_PASSWORD` when `sudo mysql` works locally.

Then start the dev server:

```sh
./woco dev
```

The equivalent manual steps, if you prefer to run them yourself:

```sh
uv sync
# Edit the password in tools/setup_worldcovers_db.sql first, then:
sudo mysql < tools/setup_worldcovers_db.sql
cp mysql.cnf.example mysql.cnf   # then fill in the same user and password
cp .env.example .env             # then set DJANGO_SECRET_KEY (see below)
./woco secretkey                 # prints a fresh secret key
cd frontend && npm ci && npm run build && cd ..
./woco migrate
./woco collectstatic --noinput
./woco dev
```

Paste the generated secret key into the `DJANGO_SECRET_KEY=` line of `.env`
before running `./woco migrate` -- Django refuses to start without it.

`./woco` is the repo-local CLI shim for Django management commands on macOS
and Linux. It wraps `uv run woco`, which calls `woco_cli.py` and then
Django's `execute_from_command_line`. `woco.bat` is included as a convenience
wrapper for Windows cmd.exe and PowerShell; use `.\woco.bat` in place of
`./woco` on Windows systems.

For full setup details and troubleshooting, see
[docs/devel/BUILD.md](./docs/devel/BUILD.md). For a task-oriented index of
all developer and operator docs, see
[docs/devel/README.md](./docs/devel/README.md).

## Development

For day-to-day development, run:

```sh
./woco dev
```

With `DEBUG=True`, this starts Vite on `http://localhost:8080` and Django on
`http://127.0.0.1:8000`. Open the Vite URL for frontend hot reload. API,
admin, media, and static requests are proxied to Django.

With `DEBUG=False`, `./woco dev` builds `frontend/dist/` and serves the built
SPA through Django at `http://127.0.0.1:8000`.

## Deployment And Operations

Hosted deploys target Ubuntu 24.04 LTS servers with systemd, nginx, MySQL 8,
Node 22, uv, and Python 3.13. The checked-in provisioning script is written
for that host profile.

The deployment source of truth is:

- [.github/workflows/build-and-deploy.yml](./.github/workflows/build-and-deploy.yml)
- [.github/workflows/deploy-prod.yml](./.github/workflows/deploy-prod.yml)
- [deploy/provision.sh](./deploy/provision.sh)
- [deploy/deploy.sh](./deploy/deploy.sh)
- [deploy/worldcovers.service](./deploy/worldcovers.service)
- [deploy/worldcovers-apply-unit.sh](./deploy/worldcovers-apply-unit.sh)

GitHub Actions stops and starts the `worldcovers` service around each deploy.
Staging can apply a changed systemd unit through the audited helper; production
fails closed until a root operator reviews and applies unit changes manually.
`deploy/deploy.sh` runs dependency sync, migrations, frontend build, and
Django static collection.

For deployment details, see [docs/devel/DEPLOY.md](./docs/devel/DEPLOY.md).
For operator tasks, see [docs/devel/RUNBOOK.md](./docs/devel/RUNBOOK.md).
For ETL and management commands, see [docs/devel/TOOLS.md](./docs/devel/TOOLS.md).
For the ASCC catalog pipeline, see [docs/devel/PIPELINE.md](./docs/devel/PIPELINE.md).

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

Contribution policy and public issue-tracker links are not yet formalized. Coordinate changes through the project team.

Parts of this codebase were generated with AI assistance. Changes still require
human review before acceptance.

---

_**We hope you enjoy WoCo!**_
