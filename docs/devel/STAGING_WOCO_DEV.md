# woco.dev staging / demo box

A disposable personal staging server for reviewing UI changes (e.g. with Ian) on
a live URL. It mirrors the prod host (`hellowoco.app`, see `DEPLOY.md`) but runs
its own fresh Ubuntu host so prod data and secrets never touch it. Data is meant
to be blasted away and reloaded one state at a time.

Differences from prod:

- Hostname is `woco.dev` (`DJANGO_APP_HOSTNAME` in `backend/.env`).
- Email backend is `console` -- the box never sends real mail.
- Its own freshly generated `DJANGO_SECRET_KEY` and MySQL password.
- No pipeline LLM keys: the ASCC pipeline runs on your local machine; only the
  finished bundle + media are pushed here.

`.dev` is on the HSTS preload list, so browsers force HTTPS. A valid Let's
Encrypt cert is mandatory and DNS must resolve before certbot can issue.

## Architecture

Same as prod: gunicorn (`127.0.0.1:8000`, systemd unit `worldcovers`) behind
nginx, MySQL 8 (`worldcovers` DB), uv/Python 3.13, Node 22, Django 5.2 + Vite
SPA. Repo at `/srv/woco`, owned by `wocod`.

## First-time provisioning

### 1. Create the Linode

Fresh **Ubuntu 24.04 LTS**, **2 GB** plan (`g6-standard-1`) -- a 1 GB Nanode
OOMs during `npm run build`. Add your SSH public key. Via linode-cli:

```sh
linode-cli linodes create \
  --type g6-standard-1 --region us-east --image linode/ubuntu24.04 \
  --label woco-dev --root_pass "$(openssl rand -base64 24)" \
  --authorized_keys "$(cat ~/.ssh/id_ed25519.pub)"
linode-cli linodes list   # note the IPv4
```

### 2. Clone the repo and provision

SSH in as root, clone the repo to `/srv/woco` (provide your own git auth --
deploy key or token), then run the provisioning script:

```sh
ssh root@<IP>
git clone https://github.com/covercensus/worldcovers.git /srv/woco
WOCO_HOSTNAME=woco.dev /srv/woco/tools/provision.sh
```

`provision.sh` installs all packages, creates the `wocod` user, sets up MySQL +
`mysql.cnf` + `backend/.env` (fresh secret, `DEBUG=False`, console email), the
systemd unit, the HTTP nginx site, the firewall, then runs `deploy.sh` and starts
the service. It also installs the staging-only root-owned unit helper used by
GitHub Actions. It is idempotent (`WOCO_FORCE=1` to regenerate secrets).

### 3. Point DNS at the box (Porkbun)

In the Porkbun DNS panel for `woco.dev`, add an **A record** `@` -> `<IP>`
(optionally `www` -> `<IP>`). Wait for it to resolve:

```sh
dig +short woco.dev   # should print <IP>
```

### 4. Issue the TLS cert

Once DNS resolves, on the box:

```sh
certbot --nginx -d woco.dev -d www.woco.dev --redirect \
  -m you@example.com --agree-tos -n
```

certbot rewrites the nginx site in place to add the 443 server and 80->443
redirect. Django's HTTPS redirect / HSTS are already on under `DEBUG=False`.

## Loading a state (e.g. Michigan)

The pipeline runs locally; only the bundle + media are pushed. From your local
checkout:

```sh
# 1. Build the Django-shape bundle from verified catalog rows.
#    (Michigan's seed regions.csv already carries Michigan/Indiana Territory.)
./woco ascc munge MI --import-check never

# 2. Push the bundle + media and reload (truncate + import) on the box.
#    push_data.sh honours WOCO_HOST / WOCO_REMOTE_ROOT.
WOCO_HOST=root@woco.dev ./tools/push_data.sh --import
```

`--import` runs `reload_data.sh` on the box, which is
`import_ascc_bundle tools/wip/out --truncate` -- it wipes all 14 catalog tables
and loads the pushed bundle. To swap to a different state later, re-munge that
state locally and repeat step 2.

## Deploying a UI branch

This is a personal box, so branch deploys can still be manual. SSH as `wocod`
or switch to `wocod` first, then run:

```sh
cd /srv/woco
git fetch origin
git reset --hard origin/<your-ui-branch>
export PATH=$HOME/.local/bin:$PATH
sudo -n /bin/systemctl stop worldcovers
./tools/deploy.sh
sudo -n /bin/systemctl start worldcovers
```

For deploy key rotation, sudoers, and the staging unit helper, see
`docs/devel/DEPLOY.md`.

## Verifying

```sh
curl -sI http://woco.dev            # 301 -> https
curl -sI https://woco.dev           # 200, valid cert
curl -s  https://woco.dev/api/v2/markings/?page=1   # Michigan rows
```

Then in a browser: `https://woco.dev` loads the SPA, Michigan markings render
with thumbnails from `/media/mi/`, and the state dropdown lists Michigan +
Michigan Territory. `https://woco.dev/admin/` reaches the Django admin login.

## Troubleshooting

```sh
sudo systemctl status worldcovers
sudo journalctl -u worldcovers -f
sudo nginx -t && sudo systemctl reload nginx
```

A 400 (Bad Request) usually means the request host is not in `ALLOWED_HOSTS`
(driven by `DJANGO_APP_HOSTNAME`). A redirect loop before the cert exists is
expected -- finish the certbot step.
