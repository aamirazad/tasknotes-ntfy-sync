from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tasknotes_ntfy.config import Settings
from tasknotes_ntfy.domain import ClaimedOccurrence
from tasknotes_ntfy.ntfy import (
    NtfyPublisher,
    PermanentDeliveryError,
    TransientDeliveryError,
    parse_retry_after,
)


def settings(tmp_path: Path, **values) -> Settings:
    return Settings(
        _env_file=None,
        data_root=tmp_path,
        vault_root=tmp_path / "vault",
        database_path=tmp_path / "notifier" / "db.sqlite3",
        health_path=tmp_path / "notifier" / "health.json",
        obsidian_remote_vault="Remote",
        obsidian_deep_link_vault="Phone",
        obsidian_auth_token="obsidian-token",
        ntfy_topic="secret-topic",
        ntfy_base_url="https://ntfy.example",
        **values,
    )


OCCURRENCE = ClaimedOccurrence(
    "occurrence",
    "Title",
    "Message",
    "obsidian://open",
    4,
    "tn-stable-sequence",
    1,
)


@pytest.mark.asyncio
async def test_publish_uses_json_sequence_id_and_optional_auth(tmp_path: Path) -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json={"id": "server-id"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    publisher = NtfyPublisher(settings(tmp_path, ntfy_access_token="access"), client)
    await publisher.publish(OCCURRENCE)
    assert captured is not None
    body = captured.read().decode()
    assert '"topic":"secret-topic"' in body
    assert '"sequence_id":"tn-stable-sequence"' in body
    assert captured.headers["Authorization"] == "Bearer access"
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 500, 503])
async def test_transient_statuses(tmp_path: Path, status: int) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, headers={"Retry-After": "17"})
        )
    )
    with pytest.raises(TransientDeliveryError) as caught:
        await NtfyPublisher(settings(tmp_path), client).publish(OCCURRENCE)
    assert caught.value.retry_after_seconds == 17
    await client.aclose()


@pytest.mark.asyncio
async def test_ordinary_4xx_is_permanent_and_body_is_not_exposed(tmp_path: Path) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="secret response details")
        )
    )
    with pytest.raises(PermanentDeliveryError, match="HTTP 401") as caught:
        await NtfyPublisher(settings(tmp_path), client).publish(OCCURRENCE)
    assert "secret response details" not in str(caught.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_is_transient(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(TransientDeliveryError, match="ReadTimeout"):
        await NtfyPublisher(settings(tmp_path), client).publish(OCCURRENCE)
    await client.aclose()


def test_retry_after_http_date() -> None:
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    assert parse_retry_after("Tue, 11 Aug 2026 12:01:00 GMT", now) == 60
    assert parse_retry_after("invalid", now) is None
