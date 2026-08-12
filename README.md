# TaskNotes to ntfy

`tasknotes-ntfy` continuously pulls an Obsidian Sync vault and publishes TaskNotes reminders
to ntfy. One hardened amd64 container runs the official Obsidian Headless client in
`pull-only` mode beside a Python notifier. There are no inbound ports, and the notifier's
Unix account can read but cannot modify the synced vault.

The Markdown files are the source of truth. The service does not use the desktop-only
TaskNotes HTTP API, does not write task data, and keeps notification state in SQLite under
the single persistent `/data` mount.

## Prerequisites

- An amd64 Docker host with Docker Compose v2 (or Docker Engine).
- An Obsidian Sync subscription, an Obsidian auth token, and the vault's E2EE password.
- The exact, unique remote Sync vault name or its ID.
- The local vault name used by Obsidian on the phone.
- The ntfy app subscribed to a separate, high-entropy topic name.

The image pins Python 3.13, Node.js 22, `obsidian-headless` 0.0.14, Python dependencies,
and the validated Debian base-image digests.

## Configure

Create the runtime file and restrict it before adding secrets:

```sh
cp .env.example .env
chmod 0600 .env
```

At minimum, replace these values:

- `OBSIDIAN_REMOTE_VAULT`: exact remote name or vault ID.
- `OBSIDIAN_DEEP_LINK_VAULT`: the phone's local vault name for `obsidian://` links.
- `OBSIDIAN_AUTH_TOKEN`: token consumed only by Headless commands and the sync child.
- `OBSIDIAN_E2EE_PASSWORD`: required when initializing an empty `/data` volume.
- `NTFY_TOPIC`: an unguessable topic already subscribed on the phone.

Confirm `TASKS_PATH`, `TASK_PROPERTY_NAME`, and `TASK_PROPERTY_VALUE` for the production
vault. Defaults match the supplied fixture (`Efforts/Tasks`, `base`, and `[[Tasks.base]]`).
Set `OBSIDIAN_EXCLUDED_FOLDERS` to a comma-separated list (for example,
`Archive,Private/Attachments`) to exclude folders from Sync; leave it empty to clear
exclusions. The other defaults implement `America/New_York`, 07:00 for date-only anchors,
and a 15-minute late-delivery grace window. `.env.example` documents every common override.

Do not commit `.env`. Back it up through the same secrets mechanism used for the host.

## Start and verify

```sh
docker compose build
docker compose up -d
docker compose logs -f tasknotes-ntfy
```

The first normal start performs all setup: it resolves the unique remote vault, validates
the E2EE password, creates the local sync configuration, disables config and attachment
sync, applies `pull-only`, verifies it, then launches continuous sync and the notifier.
Later starts validate and reuse that state. Startup refuses a persisted configuration for a
different remote vault; it never unlinks or replaces it automatically.

The official CLI accepts the E2EE password only as a setup command argument. During first
initialization it can therefore be visible briefly to a privileged host user inspecting the
container process list. It is never placed in the image or normal logs.

Check status:

```sh
docker compose ps
docker compose exec tasknotes-ntfy tasknotes-ntfy health
docker compose exec tasknotes-ntfy ob sync-status --path /data/vault --json
docker compose exec tasknotes-ntfy tasknotes-ntfy list --state scheduled
```

The sync status must contain `"syncMode":"pull-only"`. Health requires recent main,
scheduler, and full-scan heartbeats, a readable task directory, a queryable database, and a
completed initial sync. An ntfy outage is reported through retries and logs rather than
forcing a container restart.

An equivalent basic Docker invocation is:

```sh
docker run -d --name tasknotes-ntfy --platform linux/amd64 \
  --env-file .env --mount type=bind,src="$PWD/data",dst=/data \
  --restart unless-stopped --read-only --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
  --security-opt no-new-privileges --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add KILL \
  --cap-add SETGID --cap-add SETUID tasknotes-ntfy:0.1.0
```

Compose also applies memory, CPU, and process limits. Tune those values for the host if the
vault is unusually large.

## Diagnostics

All commands are read-only with respect to the vault:

```sh
# Parse and resolve everything without changing the persistent database
docker compose exec tasknotes-ntfy tasknotes-ntfy scan --dry-run

# Reconcile immediately
docker compose exec tasknotes-ntfy tasknotes-ntfy scan

# Inspect pending/retrying/failed state (the task body is not printed)
docker compose exec tasknotes-ntfy tasknotes-ntfy list --state retry

# Show anchors and calculated UTC instants for one note
docker compose exec tasknotes-ntfy \
  tasknotes-ntfy explain 'Efforts/Tasks/Example task.md'
```

Production logs are JSON lines. They never include task bodies, authentication headers,
the full ntfy topic, the Obsidian token, or the E2EE password.

## Delivery behavior

The database provides durable claims, sent history, a delivery lease, and retry state.
Network errors, timeouts, HTTP 408/429, and 5xx responses retry with bounded exponential
backoff; ordinary 4xx responses become terminal failures. Old unsent occurrences expire
outside the configured grace window.

ntfy sequence IDs are stable per resolved occurrence. A retry after an ambiguous timeout
updates/replaces the phone's existing notification on current ntfy clients, but ntfy's
server history is append-only. The honest network-boundary guarantee is therefore
at-least-once, with client-side duplicate suppression where ntfy supports it.

## Updating and rollback

Before updating, back up `/data` and retain the currently deployed image tag or digest:

```sh
docker compose stop
tar -C . -czf "tasknotes-ntfy-data-$(date +%Y%m%d-%H%M%S).tar.gz" data
docker compose start
```

A stopped-container archive safely includes SQLite, WAL state, the vault, and Headless
configuration. For an online database-only backup, use Python's `sqlite3.Connection.backup`
API inside the container; do not copy only the live `.sqlite3` file while WAL writes may be
active.

Update and roll back with explicit tags:

```sh
docker compose build --pull
docker compose up -d

# Roll back: set image/build to the retained tag, then recreate with the same data mount
docker compose up -d --force-recreate
```

Migrations are forward-only. Read release notes before rolling an older image onto a
database created by a newer schema.

## Secret rotation and recovery

- Obsidian auth token: update `.env`, then run `docker compose up -d --force-recreate`.
  If the old token is revoked, the sync child remains degraded and retries until recreated
  with a working token.
- ntfy topic: subscribe the phone to the new high-entropy topic first, update `.env`, and
  recreate the container. Test before unsubscribing from the previous topic.
- E2EE password: changing this value does not rewrite existing persisted Headless keys.
  After changing encryption in Obsidian, stop the service, back up `/data`, move
  `data/config` and `data/vault` aside together, create empty replacements, update `.env`,
  and start normally. Keep the moved copy until the new pull is verified.

If only notifier state must be rebuilt, stop the container and move
`data/notifier/reminders.sqlite3`, `-wal`, and `-shm` files into a backup directory. On
restart, a full scan recreates state. The grace window prevents a flood of old reminders,
but losing the old database also loses sent-history duplicate protection.

To recover from a revoked/expired Obsidian login, replace `OBSIDIAN_AUTH_TOKEN` and
recreate the container. Do not remove sync state unless status still fails with a valid token
and a backup exists.

## Test ntfy safely

Use a dedicated test topic, not the production topic. After subscribing the phone, publish a
probe without placing the topic in shell history:

```sh
read -r -s -p 'Test ntfy topic: ' TEST_NTFY_TOPIC
printf '\n'
curl --fail --silent --show-error \
  -H 'Title: tasknotes-ntfy test' -d 'Delivery path is working.' \
  "https://ntfy.sh/${TEST_NTFY_TOPIC}"
unset TEST_NTFY_TOPIC
```

The production rollout checklist is in
[`build-docs/production-acceptance.md`](build-docs/production-acceptance.md).

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest
RUN_CONTAINER_TESTS=1 .venv/bin/pytest tests/container
```

Tests cover fixture parsing, time zones and DST, reconciliation, retries, restart recovery,
watcher behavior, health, automatic Headless initialization, process users, permissions,
signal handling, persistent state reuse, amd64 architecture, and the absence of published
ports.

