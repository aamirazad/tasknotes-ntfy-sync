# Production acceptance checklist

Run this only with an isolated test vault and a separate high-entropy test ntfy topic. Record
the image digest, timestamps, and observed notification IDs in the deployment change log.

## Automated gate

```sh
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
RUN_CONTAINER_TESTS=1 .venv/bin/pytest
ENV_FILE=.env.example docker compose config --quiet
```

All checks must pass. The container suite uses a fake `ob` executable; it validates runtime
orchestration and permissions without accessing a real account.

## Real-service acceptance

1. Populate a mode-0600 `.env` with the test vault, phone vault name, auth token, E2EE
   password, and test ntfy topic.
2. Mount an empty `data` directory and run `docker compose up -d`.
3. Require `ob sync-status --path /data/vault --json` to report `pull-only`; verify the
   expected fixture appears under `/data/vault/Efforts/Tasks`.
4. Require `tasknotes-ntfy health` to become healthy and confirm Docker publishes no ports.
5. Create one due task and one scheduled task a few minutes ahead. Require exactly one
   correctly titled notification for each, at the expected local times.
6. Tap each notification and verify the phone opens the correct vault and nested note.
7. Reschedule another task before delivery. Require only its new occurrence to fire.
8. Complete and archive separate pending tasks. Require neither to fire.
9. Put two reminders on one task. Require both to fire independently.
10. Restart the container shortly before a reminder. Require one phone notification.
11. Block outbound access to ntfy until a reminder becomes due, restore it inside the grace
    window, and require retry delivery. Repeat outside the grace window and require expiry.
12. Stop and recreate the container with the same `/data`. Require existing sync state and
    sent history to be reused without the E2EE password.
13. Attempt create, edit, rename, and delete operations as UID 10002 inside `/data/vault`;
    require every operation to fail. The automated container suite performs the same probe.
14. Change `OBSIDIAN_REMOTE_VAULT` while retaining `/data`; require startup to fail without
    unlinking or modifying the persisted remote configuration.
15. Stop the container, back up `/data`, restore it to a staging location, and start the same
    image against the restore. Require health and pending state to match.
16. Roll back to the previously retained image with the same `/data` and verify health.

Only after every observation passes should the operator subscribe the phone to the
production topic, replace the topic in `.env`, recreate the container, and retain the tested
image digest as the rollback target.

