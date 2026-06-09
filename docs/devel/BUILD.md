# Building WorldCovers

## Prerequisites

- Python 3.13+
- Node.js + npm (for the frontend build)
- MySQL 8+
- `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`; auto-installs the pinned Python from `.python-version`)

## Steps

### 1. Install Python dependencies

```sh
uv sync
```

This creates `.venv/` at the repo root with both runtime and dev dependencies installed. On the server, use `uv sync --no-dev --frozen` to skip the dev group.

### 2. Configure the database

Copy the example credentials file and fill in your MySQL details:

```sh
cp mysql.cnf.example mysql.cnf
```

Edit `mysql.cnf` with your database user and password. Django reads this file at startup via `read_default_file` -- **`migrate` will fail without it**.

```ini
[client]
user = your_db_user
password = your_db_password
default-character-set = utf8mb4
```

`mysql.cnf` is gitignored. To create the database and user from scratch, see `tools/setup_worldcovers_db.sql`.

### 3. Build the frontend

The React SPA must be built before Django can serve it:

```sh
cd frontend && npm ci && npm run build
```

This creates `frontend/dist/`, which the Django dev server serves via the catch-all route at the site root. No separate frontend dev server is needed for normal use.

### 4. Run migrations

```sh
woco migrate
```

### 5. Collect static files

```sh
woco collectstatic --noinput
```

### 6. Start the dev server

For day-to-day development with frontend hot-reload, use `woco dev` from the repo root (see [the launcher section](#launcher) below). To run just Django manually:

```sh
woco runserver
```

The built SPA is served at `/`. The API lives under `/api/` and the admin at `/admin/`.

### Launcher

`woco dev` is the one-command dev launcher. It reads Django's `DEBUG` setting and picks the right mode:

- `DEBUG=True` (default): starts the Vite dev server on :8080 (with HMR) and Django on :8000 in the same terminal. Open `http://localhost:8080` -- API/admin/static requests are proxied to Django. Edit any frontend or backend file and the change shows up immediately. Ctrl+C kills both processes.
- `DEBUG=False`: runs `npm run build` then `woco runserver`. Open `http://127.0.0.1:8000`. Use this to sanity-check the production bundle before pushing.

## Seeding and ETL

For importing catalog data and running ETL pipelines, see [TOOLS.md](TOOLS.md).

## Deployment

For deploying to staging/production, see [DEPLOY.md](DEPLOY.md).
