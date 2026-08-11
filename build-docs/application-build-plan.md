# TaskNotes to ntfy Notification Service — Build Plan

Status: implemented and automated validation complete; real-service production acceptance pending
Last updated: 2026-08-11  
Target TaskNotes fixture version: 4.12.3  
Deployment model: one self-initializing Docker container on an amd64 remote host, running Obsidian Headless in pull-only continuous-sync mode alongside the notifier

## 1. Outcome

Build a small, durable service that reads TaskNotes Markdown files from an Obsidian vault, calculates their reminder times, and publishes phone notifications through ntfy.

The service must continue to work when Obsidian Desktop is closed. TaskNotes webhooks are deliberately excluded because the TaskNotes HTTP API/webhook service is desktop-only. The Markdown files synced by Obsidian Headless are the source of truth.

The deployed system will have one container. Its PID 1 supervisor will initialize and run two child processes:

1. The official `obsidian-headless` package continuously pulls the vault from Obsidian Sync.
2. The `tasknotes-ntfy` notifier watches the synced task directory, periodically reconciles it into SQLite, and sends due notifications to ntfy.

```text
Obsidian on desktop/mobile
          |
          v
     Obsidian Sync
          |
          v
single Docker container (/data persisted)
  |
  +-- supervisor
       |-- obsidian-headless (pull-only, continuous) -> /data/vault
       `-- parser -> reconciler -> SQLite -> scheduler -> ntfy.sh -> phone
```

## 2. Source references

- [TaskNotes reminder data format](https://tasknotes.dev/features/reminders/)
- [TaskNotes webhook limitations and delivery behavior](https://tasknotes.dev/webhooks/)
- [TaskNotes HTTP API availability](https://tasknotes.dev/HTTP_API/)
- [Official Obsidian Headless repository and CLI reference](https://github.com/obsidianmd/obsidian-headless)
- [ntfy publishing documentation](https://docs.ntfy.sh/publish/)
- [ntfy priorities](https://docs.ntfy.sh/publish/#message-priority)
- [ntfy click actions](https://docs.ntfy.sh/publish/#click-action)
- [ntfy scheduled delivery](https://docs.ntfy.sh/publish/#scheduled-delivery)

Version-sensitive external tools must be pinned and updated intentionally. At planning time, the observed official `obsidian-headless` package version is `0.0.14` and it requires Node.js 22 or newer.

## 3. Scope

### 3.1 Required in the first production release

- Pull the vault continuously with Obsidian Headless.
- Initialize Obsidian Headless automatically from `.env` configuration on the first container start.
- Run Obsidian Headless and the notifier in one container under a signal-aware supervisor.
- Ensure the headless client is configured as `pull-only` before continuous sync begins.
- Read only Markdown files under a configured task directory.
- Recognize TaskNotes task files using the configured identifying property.
- Parse TaskNotes relative and absolute reminders.
- Support reminders anchored to either `due` or `scheduled`.
- Support date-only and date-time anchors.
- Apply a configurable time, initially `07:00`, to date-only anchors.
- Apply ISO-8601 reminder offsets after resolving the anchor time.
- Ignore tasks with no reminders.
- Ignore or cancel reminders for completed and archived tasks.
- Handle task creation, edits, deletion, and rename.
- Handle multiple reminders on one task.
- Persist reminder state in SQLite across restarts.
- Prevent duplicate notifications as far as the ntfy API permits.
- Map TaskNotes priorities to ntfy priorities.
- Include the task body, truncated safely.
- Include an Obsidian deep link that opens the task.
- Reconcile on startup, on file events, and on a periodic interval.
- Retry transient ntfy failures.
- Run without a public inbound port.
- Provide Docker health checks and useful structured logs.
- Include unit, integration, and container-level tests.

### 3.2 Explicitly out of scope for the first release

- A new Obsidian plugin.
- TaskNotes webhooks or its HTTP API.
- Writing any task data back into the vault.
- Editing, completing, snoozing, or rescheduling tasks from ntfy actions.
- A browser-based administration interface.
- Supporting non-TaskNotes task syntaxes.
- Sending notifications for a due/scheduled date when the task has no reminder.
- Running a self-hosted ntfy server.
- Reimplementing Obsidian Sync.
- Requiring a second application container.

### 3.3 Possible later enhancements

- Hand reminders within a rolling 72-hour window to ntfy scheduled delivery. The public ntfy service currently caps scheduled delivery at three days, so this cannot replace the local scheduler for all future reminders.
- An ntfy action to complete or snooze a task. This would require a carefully designed write path and would change the current read-only safety model.
- Prometheus metrics or a small read-only status endpoint.
- Support multiple vaults or task directories in one process.

## 4. Confirmed behavior from the fixture vault

The vault currently uses:

| Setting | Value |
| --- | --- |
| Vault name | `NTFY Test` |
| Task directory | `Efforts/Tasks` |
| Identification property | `base` |
| Identification value | `[[Tasks.base]]` |
| Title storage | Filename, unless a `title` property exists |
| Active statuses | `To-do`, `In progress` |
| Completed statuses | `Done`, `Not doing` |
| Priorities | `None`, `Low`, `Medium`, `High` |

The implementation must not hardcode these fixture values into business logic. They will be configuration defaults or example values.

### 4.1 Fixture expectations

Assuming `America/New_York` and the date-only time `07:00`:

| Fixture | Expected result |
| --- | --- |
| `Example task due tomorrow.md` | Notify at 2026-08-12 07:00 local time |
| `Example task schedued wednesday but due thursday with notifications.md` | Notify at 2026-08-13 06:45 local time; the reminder is anchored to `due` with `-PT15M` |
| `Example task schedued.md` | Notify at 2026-08-14 07:00 local time; the reminder is anchored to `scheduled` with `PT0M` |
| `Task with a due time and set amount of time.md` | Notify at 2026-08-11 19:27 local time |
| `Example task without notifications.md` | Do not schedule anything |
| `Regular Task.md` | Do not schedule anything |

These files become end-to-end fixtures and must be copied into the test suite rather than modified in place during tests.

## 5. Decisions and default policies

### 5.1 Technology stack

Use Python 3.13 for the notifier executable and supervisor unless a literal native compiled binary is confirmed as a requirement before implementation.

Recommended libraries:

- `pydantic-settings`: validated environment configuration.
- `PyYAML`: safe YAML frontmatter parsing.
- `watchfiles`: efficient recursive filesystem observation.
- `httpx`: ntfy HTTP client with explicit timeouts.
- `isodate`: ISO-8601 duration parsing.
- `pytest`, `pytest-asyncio`, and `respx`: tests.

Use the Python standard library for:

- SQLite through `sqlite3`.
- Time zones through `zoneinfo`.
- URL creation through `urllib.parse`.
- Hashing and stable notification identifiers through `hashlib`.
- The async scheduler loop through `asyncio`.

Do not add APScheduler initially. A small database-driven due loop is easier to make deterministic, inspect, and test for this single-purpose service.

### 5.2 Time zone

- Default: `America/New_York`.
- Configuration: `TIMEZONE` using an IANA name.
- Convert every effective reminder instant to UTC before storing it.
- Convert to local time only for parsing naive TaskNotes values and formatting notification text/logs.
- Reject startup configuration if the time zone is invalid.

### 5.3 Date-only anchors

- Default configured time: `07:00`.
- Configuration: `DATE_ONLY_TIME` in `HH:MM` 24-hour format.
- Resolve the date at this local time first, then apply the reminder offset.
- Example: a date-only due date with `-PT15M` becomes 06:45 on that date.

This intentionally avoids treating a date-only value as midnight.

### 5.4 Missed reminders

- Default grace window: 15 minutes.
- Configuration: `MISSED_REMINDER_GRACE_SECONDS=900`.
- On startup or recovery, send an unsent reminder if it is no more than the grace period late.
- Mark older unsent reminders as `expired`; do not flood the phone with stale tasks.
- A reminder already recorded as sent is never sent again for the same occurrence.

This 15-minute default is confirmed for the first release.

### 5.5 File event policy

Filesystem events improve latency but are not authoritative.

- Debounce events for 500 milliseconds to tolerate Obsidian/Sync atomic rewrites.
- Re-read a changed file only after it can be opened and parsed completely.
- Retry a temporarily malformed/partial file a few times with short backoff.
- Run a full task-directory reconciliation at startup.
- Run a full reconciliation every 60 seconds by default.
- Use the full scan to detect events that were missed, especially deletions and renames.

### 5.6 ntfy priority mapping

Default mapping:

| TaskNotes | ntfy numeric priority |
| --- | ---: |
| `None` | 3 |
| `Low` | 2 |
| `Medium` | 3 |
| `High` | 4 |
| Unknown value | 3 |

Reserve priority 5 for a future TaskNotes priority such as `Urgent`.

Allow an override using a JSON configuration value such as:

```text
PRIORITY_MAP_JSON={"None":3,"Low":2,"Medium":3,"High":4,"Urgent":5}
```

### 5.7 Notification content

Required fields sent to ntfy:

- `topic`: from the secret runtime configuration.
- `title`: derived from task/reminder context.
- `message`: Markdown body after frontmatter, truncated by UTF-8 byte length.
- `priority`: mapped priority.
- `click`: Obsidian deep link.
- `tags`: at least `calendar` or another configurable task tag.

Default title rules:

1. Date-only due anchor with `PT0M`: `<Task name> is due today`.
2. Date-only scheduled anchor with `PT0M`: `<Task name> is scheduled today`.
3. Timed due anchor with `PT0M`: `<Task name> is due now`.
4. Timed scheduled anchor with `PT0M`: `<Task name> is scheduled now`.
5. Negative due offset: `<Task name> is due in <humanized duration>`.
6. Negative scheduled offset: `<Task name> is scheduled in <humanized duration>`.
7. Positive due offset: `<Task name> was due <humanized duration> ago`.
8. Positive scheduled offset: `<Task name> was scheduled <humanized duration> ago`.
9. Absolute reminder: `<Task name> reminder`.

Examples include `is due in 15 minutes`, `is due now`, `is scheduled now`, and `is scheduled today`. The message remains the task body; timing text belongs in the title rather than being prepended to the body. Do not use the possibly auto-generated reminder `description` as the title.

Body rules:

- Strip frontmatter completely.
- Trim leading/trailing blank lines.
- Default maximum: 1,000 UTF-8 bytes, configurable with `BODY_MAX_BYTES`.
- Truncate at a Unicode code-point boundary and append an ellipsis.
- If the body is empty, use `Open this task in Obsidian.`.
- Never log the body at normal log levels.

### 5.8 Obsidian URL

Construct:

```text
obsidian://open?vault=<encoded-vault-name>&file=<encoded-vault-relative-path-without-.md>
```

Rules:

- Use the configured user-facing Obsidian vault name, not the container path.
- Use POSIX `/` path separators.
- Remove only the final `.md` suffix.
- Percent-encode the vault name and file value as query parameters.
- Preserve subdirectories.

Example:

```text
obsidian://open?vault=NTFY%20Test&file=Efforts%2FTasks%2FRegular%20Task
```

### 5.9 Secrets

- Never commit the ntfy topic, Obsidian authentication token, or end-to-end encryption password.
- The normal single-container setup reads its configuration from a host-side `.env` file that is excluded from Git and should have mode `0600`.
- The official Obsidian Headless CLI reads `OBSIDIAN_AUTH_TOKEN` directly.
- The end-to-end encryption password is required only when the persistent sync state is first initialized, but it may remain in `.env` to support rebuilding the persistent data directory.
- The ntfy topic is protected only by its high-entropy name; no ntfy access token is required.
- Do not print the token, encryption password, or full ntfy topic in logs.
- `.env.example` contains placeholders only and is safe to commit.

## 6. Configuration contract

Proposed single-container settings:

| Variable | Required | Default/example | Purpose |
| --- | --- | --- | --- |
| `DATA_ROOT` | No | `/data` | Root of the one persistent container volume |
| `VAULT_ROOT` | No | `/data/vault` | Synced vault root inside the container |
| `OBSIDIAN_REMOTE_VAULT` | Yes | production name or ID | Remote Obsidian Sync vault; exact name is sufficient if unique |
| `OBSIDIAN_DEEP_LINK_VAULT` | Yes | phone's local vault name | User-facing vault name used by `obsidian://` links |
| `OBSIDIAN_AUTH_TOKEN` | Yes | secret | Authentication token consumed by the official headless CLI |
| `OBSIDIAN_E2EE_PASSWORD` | First setup | secret | End-to-end encryption password used by `ob sync-setup` |
| `OBSIDIAN_DEVICE_NAME` | No | `tasknotes-ntfy` | Device label in Obsidian Sync history |
| `TASKS_PATH` | No | `Efforts/Tasks` | Directory relative to `VAULT_ROOT` |
| `TASK_PROPERTY_NAME` | No | `base` | Task-identifying property |
| `TASK_PROPERTY_VALUE` | No | `[[Tasks.base]]` | Required identifying value |
| `TIMEZONE` | No | `America/New_York` | IANA time zone |
| `DATE_ONLY_TIME` | No | `07:00` | Time assigned to date-only anchors |
| `COMPLETED_STATUSES` | No | `Done,Not doing` | Statuses that cancel reminders |
| `PRIORITY_MAP_JSON` | No | mapping above | Priority mapping |
| `NTFY_BASE_URL` | No | `https://ntfy.sh` | ntfy server |
| `NTFY_TOPIC` | Yes | high-entropy secret | Topic used without a separate ntfy access token |
| `BODY_MAX_BYTES` | No | `1000` | Message body limit |
| `RECONCILE_INTERVAL_SECONDS` | No | `60` | Full scan frequency |
| `WATCH_DEBOUNCE_MS` | No | `500` | File event debounce |
| `DUE_POLL_SECONDS` | No | `5` | Scheduler polling interval |
| `MISSED_REMINDER_GRACE_SECONDS` | No | `900` | Late-send grace period |
| `NTFY_TIMEOUT_SECONDS` | No | `10` | HTTP timeout |
| `LOG_LEVEL` | No | `INFO` | Log verbosity |
| `DATABASE_PATH` | No | `/data/notifier/reminders.sqlite3` | Persistent state database |

Validate every setting before starting the watcher. A bad configuration must fail fast with a concise, non-secret error.

## 7. Internal domain model

Keep parsing, scheduling, persistence, and delivery separate.

### 7.1 Parsed task

```text
Task
  path: vault-relative Markdown path
  title: explicit title or filename stem
  status: string
  priority: string
  due: optional raw date/date-time
  scheduled: optional raw date/date-time
  archived: boolean
  body: Markdown body without frontmatter
  reminders: list[Reminder]
  source_hash: SHA-256 of relevant file content
```

### 7.2 Parsed reminder

```text
Reminder
  id: stable TaskNotes reminder ID
  type: relative | absolute
  related_to: due | scheduled | null
  offset: ISO-8601 duration | null
  absolute_time: date-time | null
  description: optional string
```

### 7.3 Resolved reminder occurrence

```text
ReminderOccurrence
  occurrence_id: SHA-256(vault identity + task path + reminder ID + effective UTC instant)
  task_path
  reminder_id
  effective_at_utc
  payload_hash
  title
  message
  click_url
  ntfy_priority
```

Including the effective instant in `occurrence_id` lets a recurring task reuse its reminder ID after TaskNotes advances the task date without being mistaken for an already-sent occurrence.

## 8. Frontmatter and reminder parsing

### 8.1 Markdown parsing algorithm

1. Enforce a configurable maximum file size before reading.
2. Read UTF-8 text.
3. If the file does not start with a YAML frontmatter fence, it is not a task.
4. Extract the first complete `---` frontmatter block.
5. Parse with `yaml.safe_load` only.
6. Normalize YAML date/datetime objects and strings to a common internal representation.
7. Require the configured identifying property and value.
8. Derive title from `title`, otherwise the Markdown filename stem.
9. Keep the content after the closing fence as the task body.
10. Validate reminders individually; one malformed reminder must not discard valid reminders from the same task.

### 8.2 Relative reminder resolution

For each relative reminder:

1. Require `relatedTo` to be `due` or `scheduled`.
2. Obtain that anchor value from the task.
3. If the anchor is absent, classify the reminder as invalid and log its path/reminder ID.
4. If the anchor is date-only, combine it with `DATE_ONLY_TIME` in `TIMEZONE`.
5. If the anchor is a naive date-time, interpret it in `TIMEZONE`.
6. If it has an explicit offset, honor that offset.
7. Parse the ISO-8601 duration and apply its sign.
8. Convert the result to UTC.

Compatibility tests must cover `PT0M`, `-PT15M`, positive offsets, hours, days, and weeks. Add explicit tests around daylight-saving transitions.

### 8.3 Absolute reminder resolution

- Require `absoluteTime`.
- Interpret a naive value in `TIMEZONE`.
- Honor an explicit UTC offset if present.
- Convert to UTC.

### 8.4 Invalid reminders

- Invalid reminders are not scheduled.
- Emit a structured warning containing only task path, reminder ID, and reason.
- Keep the task and its other reminders active.
- Re-evaluate invalid reminders on every change/full reconciliation so a corrected file recovers automatically.

## 9. SQLite design

Use WAL mode, foreign keys, and a short busy timeout. Store timestamps as UTC ISO-8601 strings or integer epoch milliseconds consistently.

### 9.1 `tasks`

| Column | Notes |
| --- | --- |
| `path` | Primary key; vault-relative path |
| `source_hash` | Detect unchanged files |
| `title` | Current title |
| `status` | Current status |
| `priority` | Current priority |
| `archived` | Boolean |
| `last_seen_scan_id` | Used to detect deletion/rename |
| `updated_at_utc` | Audit/diagnostics |

Do not store the full task body in this table unless later profiling shows a need. The current notification payload can be stored with each pending occurrence.

### 9.2 `reminder_occurrences`

| Column | Notes |
| --- | --- |
| `occurrence_id` | Primary key |
| `task_path` | Foreign key to `tasks` |
| `reminder_id` | TaskNotes reminder ID |
| `effective_at_utc` | Indexed due instant |
| `payload_hash` | Detect content/priority/link changes before delivery |
| `notification_title` | Pending payload snapshot |
| `notification_message` | Pending payload snapshot |
| `click_url` | Pending payload snapshot |
| `ntfy_priority` | Pending payload snapshot |
| `ntfy_message_id` | Stable sequence/message ID derived from occurrence ID |
| `state` | `scheduled`, `sending`, `retry`, `sent`, `canceled`, `expired`, `invalid`, `failed` |
| `attempt_count` | Delivery attempts |
| `next_attempt_at_utc` | Retry scheduling |
| `claimed_at_utc` | Recovery from a crashed sender |
| `sent_at_utc` | Successful delivery time |
| `last_error` | Sanitized error summary |
| `created_at_utc` | Audit |
| `updated_at_utc` | Audit |

Indexes:

- `(state, effective_at_utc)` for due work.
- `(state, next_attempt_at_utc)` for retry work.
- `(task_path, reminder_id)` for reconciliation.

### 9.3 `scan_runs`

Track scan ID, start/end time, file count, valid task count, errors, and completion status. A failed partial scan must never make unseen tasks look deleted.

### 9.4 Migration strategy

- Maintain a `schema_version` table.
- Run forward-only migrations in a transaction at startup.
- Back up the small SQLite file before any destructive future migration.
- Unit test migration from every released schema version.

## 10. Reconciliation algorithm

### 10.1 Full scan

1. Start a `scan_runs` row.
2. Enumerate `*.md` recursively under the configured task directory.
3. Parse each file independently.
4. Upsert every valid task.
5. Resolve its desired reminder occurrences.
6. Upsert desired occurrences.
7. Update pending payloads when title, body, priority, or link changes but the occurrence instant remains the same.
8. Cancel pending occurrences for reminders removed from an observed task.
9. Only after the scan completes successfully, mark tasks not seen in this scan as removed and cancel their pending occurrences.
10. Commit scan completion metrics.

If directory enumeration fails or too many files fail because the sync volume is temporarily unstable, fail the scan without performing deletion reconciliation.

### 10.2 File event

- A create/modify event schedules a debounced parse and task-level reconciliation.
- A delete/rename event triggers a prompt full scan, because rename streams vary by platform and volume driver.
- Repeated events collapse into one reconciliation.
- A periodic full scan remains mandatory.

### 10.3 Changes after a reminder was sent

- Editing title/body/priority after `sent` does not resend the same occurrence.
- Changing the effective reminder time creates a new occurrence ID and cancels the previous pending occurrence.
- If the old occurrence is already sent, preserve it as history and schedule the new occurrence only when its effective time is in the future or within the late grace window.
- When recurring task dates advance while the reminder ID remains unchanged, the new effective instant creates a new occurrence.

### 10.4 Completion and archive behavior

When `status` is in `COMPLETED_STATUSES` or `archived` is true:

- Cancel all `scheduled` and `retry` occurrences for that task.
- Do not delete `sent` history.
- If the task is reopened, resolve its reminders again. A reminder whose effective instant is already outside the grace window becomes expired instead of firing immediately.

## 11. Scheduler and ntfy delivery

### 11.1 Scheduler loop

Every `DUE_POLL_SECONDS`:

1. Recover `sending` rows whose claim is older than the delivery lease timeout.
2. Select a small ordered batch of `scheduled` occurrences whose effective time is due.
3. Include `retry` rows whose `next_attempt_at_utc` is due.
4. In a transaction, atomically claim each row by changing it to `sending` and setting `claimed_at_utc`.
5. Publish outside the transaction.
6. Mark success as `sent`, or schedule a retry.

Only one notifier replica is supported initially. The claim design still prevents duplicate work if overlapping async loops occur within that process and leaves room for later multi-replica support.

### 11.2 ntfy request

Prefer the ntfy JSON publish API. Send explicit connect/read/write timeouts and optional bearer authentication.

Use a stable ntfy sequence/message ID derived from the occurrence ID. Verify during integration testing that retrying the same ID updates/replaces the same ntfy message rather than creating an extra phone notification. If server behavior cannot provide idempotency for an ambiguous timeout, document delivery as at-least-once and retain the stable ID for diagnosis.

### 11.3 Retry policy

- Retry network errors, timeouts, HTTP 408, HTTP 429, and HTTP 5xx.
- Honor `Retry-After` where provided.
- Default exponential backoff: 10 seconds, 30 seconds, 2 minutes, 10 minutes, 30 minutes.
- Add bounded jitter.
- Do not retry ordinary 4xx validation/authentication errors indefinitely.
- Move permanent delivery failures to the terminal `failed` state and surface them through logs/health diagnostics.
- Never log the authentication header or topic.

### 11.4 Delivery guarantees

The intended behavior is effectively once per resolved reminder occurrence. True exactly-once delivery is impossible across a network boundary without provider-supported idempotency, so the implementation combines:

- durable local occurrence state;
- atomic claims;
- a stable ntfy message ID;
- sent history;
- recovery leases;
- bounded retries.

## 12. Single-container runtime and Obsidian Headless

### 12.1 Image

Build one amd64 image containing:

- Node.js 22 and a pinned official `obsidian-headless` package.
- Python 3.13 and the locked `tasknotes-ntfy` application.
- `tini` as the real PID 1 for signal/zombie handling.
- A small supervisor/entrypoint that initializes sync and manages both long-running child processes.

The build should use compatible Debian slim stages and install:

```text
npm install --global obsidian-headless@<pinned-version>
```

The image contains no credentials or vault data. One host bind mount or named volume at `/data` persists:

```text
/data/
  config/obsidian-headless/   official headless auth, sync config, state, logs
  vault/                      pulled Obsidian vault
  notifier/                   SQLite database and health state
```

Set `XDG_CONFIG_HOME=/data/config` for the headless process so its Linux configuration survives container replacement.

### 12.2 Automatic first-start setup

The same normal container start command handles both first-time setup and later restarts.

On every start, before launching continuous sync, the supervisor will:

1. Validate all required `.env` values without printing secrets.
2. Create the `/data` directory structure with safe ownership and modes.
3. Export `OBSIDIAN_AUTH_TOKEN` only to Obsidian Headless commands.
4. Check for an existing sync configuration with `ob sync-status --path /data/vault --json`.
5. If no configuration exists, run `ob sync-list-remote --json` and resolve `OBSIDIAN_REMOTE_VAULT`. An exact unique vault name is sufficient; the user does not need to know the vault ID.
6. Run `ob sync-setup --vault <resolved-name-or-id> --path /data/vault --password <E2EE-password> --device-name <configured-name> --json`.
7. Run `ob sync-config --path /data/vault --mode pull-only`.
8. Disable configuration syncing and unnecessary attachment categories according to the official CLI options.
9. Re-read `ob sync-status --path /data/vault --json` and require `pull-only`.
10. If persistent state points to a different remote vault than `.env`, fail safely with instructions; never unlink or replace it automatically.

The E2EE password is passed to `sync-setup` only during initialization. Because the official CLI accepts it as a command argument for non-interactive setup, it may be briefly visible to a privileged host user inspecting the container process list. The password is already present in `.env`; this limitation must be documented.

### 12.3 Process supervisor

After setup succeeds, the supervisor starts:

1. `ob sync --path /data/vault --continuous`.
2. The notifier executable configured to read `/data/vault` and store SQLite under `/data/notifier`.

Supervisor behavior:

- Forward SIGTERM/SIGINT to both children and wait for graceful shutdown.
- Prefix or structure child logs so sync and notifier messages are distinguishable.
- Restart a failed headless-sync child with bounded exponential backoff while the notifier continues using the last complete local vault state.
- Exit the container if the notifier exits unexpectedly, allowing Docker's restart policy to recover the whole application.
- Mark health as degraded while sync is restarting or has not completed its initial pull.
- Do not start task reconciliation until the configured task directory exists, but keep waiting without crash-looping during the first pull.

### 12.4 Process-level write separation

A single container cannot use two different mount modes for the same `/data/vault` path, so the prior cross-container read-only mount is unavailable. Preserve the read-only notifier design with Unix process permissions:

- Run setup and `obsidian-headless` as a dedicated `sync` user.
- Run the notifier as a separate `notifier` user.
- Own `/data/vault` with `sync`; grant the notifier's shared group read/execute but no write permission.
- Own `/data/config` only by `sync`.
- Own `/data/notifier` only by `notifier`.
- Have the root supervisor perform only directory initialization and privilege dropping; it does not parse tasks or publish notifications.

Add a container test that attempts to create, edit, rename, and delete a vault file as the notifier user and requires all operations to fail.

### 12.5 Sync scope

The current CLI exposes folder exclusions, not a sync include-only list. Therefore:

- Sync Markdown for the vault.
- Disable Obsidian configuration syncing unless explicitly needed.
- Disable unnecessary attachment categories.
- Optionally exclude known large folders.
- Enforce the strict `TASKS_PATH` boundary in the notifier regardless of what else is present in `/data/vault`.

Do not generate a brittle exclusion list that silently becomes incomplete when new folders are added. Extra synced Markdown affects disk usage but not notification behavior.

### 12.6 Restart behavior

- Docker restart policy: `unless-stopped`.
- Both children receive graceful termination.
- The single `/data` mount survives container replacement.
- Existing sync configuration is validated and reused.
- Pull-only mode is re-applied and verified on every start.
- If notifier state is absent, a full scan rebuilds it subject to the confirmed 15-minute grace rule.

## 13. Single-container Docker Compose topology

The shipped `compose.yaml` contains one service:

```text
services:
  tasknotes-ntfy:
    image: tasknotes-ntfy:<version>
    env_file:
      - .env
    volumes:
      - ./data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "tasknotes-ntfy", "health"]
```

Also document an equivalent `docker run --env-file .env -v ...:/data` command.

The service publishes no inbound host port. It needs outbound HTTPS to Obsidian Sync and ntfy.

Container hardening:

- The root filesystem is read-only after startup design is validated.
- Use a writable tmpfs for `/tmp`.
- Use `no-new-privileges` where compatible with the required startup privilege drop.
- Drop all capabilities that are not required for setuid/setgid during child launch; validate the exact minimal set in container tests.
- Pin base images by digest for production after initial validation.
- Set resource limits appropriate for Node.js Headless, Python, and SQLite.
- Keep `.env` outside the image, out of Git, and mode `0600`.

## 14. Proposed repository layout

```text
.
├── build-docs/
│   └── application-build-plan.md
├── compose.yaml
├── .env.example
├── .gitignore
├── Dockerfile
├── docker/
│   ├── entrypoint.sh
│   └── healthcheck.sh
├── src/
│   └── tasknotes_ntfy/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── domain.py
│       ├── frontmatter.py
│       ├── reminder_time.py
│       ├── notification.py
│       ├── repository.py
│       ├── reconcile.py
│       ├── watcher.py
│       ├── scheduler.py
│       ├── ntfy.py
│       ├── supervisor.py
│       ├── headless.py
│       ├── healthcheck.py
│       └── logging.py
├── tests/
│   ├── fixtures/
│   │   └── tasks/
│   ├── unit/
│   ├── integration/
│   └── container/
├── pyproject.toml
├── uv.lock
└── README.md
```

The production Docker build installs from lockfiles and runs tests/linting in CI before producing the one runtime image.

## 15. Observability and health

### 15.1 Structured logging

Use one JSON record per line in production with fields such as:

- `event`
- `task_path`
- `reminder_id`
- `occurrence_id` prefix
- `effective_at_utc`
- `state`
- `attempt`
- `scan_id`
- `duration_ms`
- sanitized `error`

Useful events:

- `startup_complete`
- `scan_started`, `scan_completed`, `scan_failed`
- `task_parsed`, `task_invalid`
- `reminder_scheduled`, `reminder_rescheduled`, `reminder_canceled`, `reminder_expired`
- `notification_claimed`, `notification_sent`, `notification_retry`, `notification_failed`
- `watcher_error`

Do not log task bodies, Obsidian tokens, ntfy tokens, or the full topic.

### 15.2 Health check

Implement `python -m tasknotes_ntfy.healthcheck` to inspect a heartbeat/state file or SQLite health row written by the running process.

Healthy requires:

- Main process heartbeat is recent.
- Last successful full reconciliation is recent enough.
- Task directory is readable after initial sync.
- SQLite can be queried.
- Scheduler loop heartbeat is recent.

Do not make transient ntfy connectivity a hard liveness failure; expose it as degraded information and rely on retries. Docker should restart a stuck process, not restart continuously during an ntfy outage.

### 15.3 Operational diagnostics

Provide read-only CLI commands:

```text
tasknotes-ntfy scan --dry-run
tasknotes-ntfy list --state scheduled
tasknotes-ntfy explain <vault-relative-task-path>
tasknotes-ntfy health
```

`explain` should print parsed anchors and effective times without publishing, making date/time errors easy to diagnose.

## 16. Test strategy

### 16.1 Unit tests

Frontmatter:

- Filename title fallback and explicit title override.
- Quoted and unquoted identification properties.
- Empty body and Unicode body.
- YAML dates returned as strings, `date`, or `datetime` values.
- Missing/malformed frontmatter.
- Multiple reminders and one malformed reminder.

Time resolution:

- Date-only due + `PT0M` -> 07:00.
- Date-only scheduled + `PT0M` -> 07:00.
- Date-only due + `-PT15M` -> 06:45.
- Timed due + `PT0M` -> exact time.
- Timed due + `-PT30M`.
- Positive offset after the anchor.
- Absolute naive and offset timestamps.
- Missing anchor.
- Invalid duration.
- DST spring-forward and fall-back boundaries.

Notification formatting:

- Required title wording.
- Correct UTF-8 byte-safe truncation.
- Empty-body fallback.
- Priority mapping and unknown priority.
- Correct deep link encoding for spaces, Unicode, `#`, `?`, and nested paths.

Persistence/reconciliation:

- Initial create schedules once.
- Unchanged scan creates no duplicate.
- Body edit updates pending payload.
- Date edit cancels old pending occurrence and creates a new one.
- Reminder removal cancels pending occurrence.
- Completion and archive cancel pending reminders.
- Reopen behavior respects the grace window.
- Rename and delete handling.
- Recurring task date advancement with reused reminder ID.
- Failed partial scan does not cancel unseen tasks.
- Sent occurrence remains sent after restart.

Scheduler:

- Atomic claim.
- Success transition.
- Retry/backoff transition.
- Permanent 4xx behavior.
- Stale `sending` claim recovery.
- Grace-window send and expiration.

### 16.2 Integration tests

- Use a temporary vault directory, real SQLite, watcher, and mocked ntfy HTTP server.
- Copy the current example files into the temporary vault.
- Advance a controllable clock instead of waiting in real time.
- Write/replace/delete files as Obsidian Sync would and verify database state.
- Simulate ntfy timeouts, 429, 500, and success.
- Simulate a process crash after the ntfy server accepts a request but before SQLite is marked sent; verify stable-ID retry behavior.

### 16.3 Container tests

- Build the one amd64 runtime image.
- Verify the container automatically initializes an empty persistent `/data` directory from environment configuration.
- Verify both the continuous-sync and notifier child processes run under the supervisor.
- Verify the notifier child runs as its dedicated non-root user.
- Verify `/data/vault` cannot be written, renamed, or deleted from by the notifier user while the sync user can update it.
- Verify `/data` persists across container recreation.
- Verify missing initial vault reports starting/unhealthy and later recovers.
- Verify graceful SIGTERM reaches both child processes.
- Verify a failed sync child is restarted without stopping the notifier.
- Verify a failed notifier causes the container to exit for Docker restart recovery.
- Verify no ports are published.
- Verify startup applies and verifies pull-only mode.
- Verify startup refuses persisted state for a different configured remote vault.

### 16.4 Manual end-to-end acceptance test

Use a separate high-entropy test ntfy topic:

1. Populate `.env`, mount an empty `/data` directory, and start the single container.
2. Verify automatic E2EE headless setup and confirm the scheduled fixture appears in `/data/vault/Efforts/Tasks`.
3. Create a task scheduled a few minutes in the future with a scheduled reminder.
4. Observe the notifier scheduling record.
5. Confirm one notification arrives at the expected local time.
6. Tap it and verify Obsidian opens the correct vault and note on the phone.
7. Reschedule a second task before its reminder and verify only the new time fires.
8. Complete a third task before its reminder and verify it does not fire.
9. Restart the notifier shortly before a reminder and verify it fires once.
10. Stop ntfy connectivity, allow a reminder to become due, restore connectivity, and verify retry behavior.

## 17. Implementation phases

### Phase 0 — Confirm deployment inputs

- Obtain the exact production remote vault name; the ID is not required when the name is unique.
- Obtain the phone's local Obsidian vault name for deep links.
- Confirm whether “binary” means the planned Python executable or requires a literal native compiled file.
- Confirm task folder/property values in the production vault.
- Create separate test and production ntfy topics/secrets.
- Obtain the Obsidian auth token and E2EE password for `.env`.

Exit criterion: all required configuration values have owners and safe storage locations.

### Phase 1 — Project scaffold

- Create Python package, lockfile, lint/type/test configuration.
- Add validated settings and secret-file loading.
- Add initial single Dockerfile and separate sync/notifier runtime users.
- Add supervisor scaffolding and `/data` directory initialization.
- Add CI commands for format, lint, type check, unit tests, and image build.

Exit criterion: empty service starts, validates configuration, writes a heartbeat, and passes CI.

### Phase 2 — Parser and time engine

- Implement frontmatter extraction and task identification.
- Implement task/reminder validation.
- Implement date/time and ISO-duration resolution.
- Implement deep links, title rules, body truncation, and priority mapping.
- Convert current vault examples to tests.

Exit criterion: all fixture expectations in section 4.1 pass deterministically.

### Phase 3 — Persistence and reconciliation

- Create SQLite schema/migration framework.
- Implement full scan and task-level reconciliation.
- Implement occurrence identity and state transitions.
- Implement completion, deletion, rename, and recurrence behavior.

Exit criterion: repeated scans are idempotent and all reconciliation tests pass.

### Phase 4 — Scheduler and ntfy delivery

- Implement due polling and atomic claims.
- Implement ntfy JSON publisher, authentication, stable ID, and retries.
- Implement late grace and expiration.
- Add mocked integration tests and crash-recovery test.

Exit criterion: a temporary test vault produces one expected mock notification per occurrence across restarts and retries.

### Phase 5 — Filesystem watcher and health

- Add debounced watcher.
- Add periodic full reconciliation.
- Add structured logs, heartbeats, health check, and diagnostic CLI commands.

Exit criterion: file changes reconcile promptly, missed events recover on the next scan, and Docker health reflects real process state.

### Phase 6 — Single-container Obsidian Headless and Compose

- Add pinned Node 22 and official Headless package to the runtime image.
- Implement automatic auth-token/E2EE setup from `.env`.
- Implement remote-vault resolution, persisted-state validation, and the pull-only guard.
- Complete supervisor signal, restart, and log handling.
- Add process-level sync/notifier permissions over the one `/data` mount.
- Add the one-service Compose file, health check, hardening, and restart policy.
- Document `.env` setup, token rotation, persistent-data backup, and clean recovery.

Exit criterion: one normal container start with an empty `/data` directory automatically configures the test vault, continuously pulls tasks, starts the notifier, and denies the notifier user write access to the vault.

### Phase 7 — End-to-end validation and rollout

- Execute the manual acceptance test.
- Observe at least one due, one scheduled, one rescheduled, and one canceled reminder.
- Test container/server restart behavior.
- Pin working image digests.
- Create backup and update instructions.
- Move from the test ntfy topic to the production secret only after validation.

Exit criterion: all acceptance criteria below are met and rollback has been tested.

## 18. Acceptance criteria

The application is complete when:

1. Obsidian Headless continuously pulls the configured vault and reports `pull-only`.
2. One container automatically initializes Headless and starts both managed processes from `.env` plus the persistent `/data` mount.
3. The notifier process user has no write access to `/data/vault`.
4. Creating or editing a TaskNotes file is reflected in SQLite within the event debounce plus a small processing allowance.
5. A missed filesystem event is corrected by the periodic full scan.
6. Tasks without reminders never create notification occurrences.
7. Due, scheduled, relative, absolute, date-only, and timed reminders resolve according to this plan.
8. The new `relatedTo: scheduled` fixture resolves to 07:00 on its scheduled date.
9. Titles use `is due in ...`, `is due now`, `is scheduled now`, or `is scheduled today` as applicable.
10. Completed, archived, deleted, renamed, or rescheduled tasks reconcile correctly.
11. Multiple reminders on one task work independently.
12. Restarting the notifier does not duplicate sent occurrences.
13. Transient ntfy failures retry without losing the occurrence.
14. Notification priority matches configured TaskNotes priority.
15. The body is safely truncated and the click action opens the correct Obsidian note.
16. Secrets do not appear in the repository, image layers, or normal logs.
17. The container targets amd64, runs managed children as their dedicated non-root users, and exposes no inbound ports.
18. Unit, integration, container, and manual acceptance tests pass.

## 19. Deployment and operations runbook requirements

The implementation must add a concise operator README containing:

- Prerequisites: Docker or Docker Compose, Obsidian Sync subscription, ntfy phone subscription, Obsidian auth token, and E2EE password.
- Required `.env` values and secure file permissions.
- The automatic first-start Headless/E2EE setup sequence.
- How to verify pull-only status.
- How to rotate the Obsidian auth token, E2EE password, or high-entropy ntfy topic in `.env`.
- How to start, stop, update, and roll back.
- How to inspect health and pending reminders.
- How to run a dry scan and explain a task.
- How to back up the single persistent `/data` directory.
- How to rebuild from an empty notifier database safely.
- How to recover from expired/revoked Obsidian authentication.
- How to test ntfy without using the production topic.

SQLite backup should use SQLite's online backup mechanism or a stopped container, not a raw copy while writes are active unless WAL files are included correctly.

## 20. Rollback and recovery

- The notifier never writes the vault, so disabling it cannot damage TaskNotes data.
- Rollback consists of stopping the new notifier image and starting the previously pinned image with the same `/data` volume.
- Preserve database compatibility or provide explicit downgrade limitations with every schema migration.
- If notifier state is lost, rebuild it with a full scan. The grace policy prevents a flood of old reminders, but already-sent future history may be lost; back up the database to retain duplicate protection.
- If Obsidian Headless state is lost, the next normal start automatically repeats setup from `.env` and then verifies pull-only mode.
- The pull-only guard must prevent newly reset sync state from defaulting to bidirectional mode unnoticed.
- If the configured remote vault differs from persisted state, startup fails without unlinking or altering the existing state.

## 21. Confirmed decisions and production inputs

Confirmed on 2026-08-11:

- `NTFY Test` is a fixture vault only, not the production vault.
- The production vault is end-to-end encrypted.
- The remote vault ID is unknown and need not be known; startup can resolve an exact unique remote vault name.
- Production timezone is `America/New_York`.
- Date-only due and scheduled anchors use `07:00`.
- The missed-reminder grace window is 15 minutes.
- `Not doing` is a completed status and cancels reminders.
- The priority mapping in section 5.6 is accepted.
- Relative and at-time titles use the contextual wording in section 5.7.
- ntfy is protected only by its high-entropy topic name, with no access token.
- Deployment architecture is amd64.
- The entire application runs in one self-initializing Docker container configured by `.env` and one persistent `/data` mount.
- Runtime credentials and the high-entropy topic may be supplied through the Git-ignored, mode-`0600` `.env` file; they are never copied into the image.

The implementation uses runtime configuration and is complete without embedding these
deployment-specific values. The following are still required before production rollout:

1. The exact production Obsidian Sync remote vault name for `OBSIDIAN_REMOTE_VAULT`.
2. The local vault name installed on the phone for `OBSIDIAN_DEEP_LINK_VAULT`; it may differ from the remote Sync vault name.
3. Confirmation that the production task directory and identifying property remain `Efforts/Tasks`, `base`, and `[[Tasks.base]]`, or their actual values.
4. An existing `OBSIDIAN_AUTH_TOKEN` placed in the mode-`0600` `.env` file.
5. Separate high-entropy test and production ntfy topics and completion of the real-service
   checklist in `build-docs/production-acceptance.md`.

Implementation choices now resolved:

- The notifier is the planned Python executable/process rather than a native compiled file.
- Auth-token provisioning uses `OBSIDIAN_AUTH_TOKEN` from `.env`.
- The uncommon-case titles are implemented as `was due ... ago`,
  `was scheduled ... ago`, and `<Task name> reminder`.

The current high-entropy topic must be moved to the local `.env` file and must not be written into committed configuration or documentation.
