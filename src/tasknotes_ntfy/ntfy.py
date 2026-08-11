"""ntfy JSON publisher with explicit transient/permanent failure classification."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from .config import Settings
from .domain import ClaimedOccurrence


class DeliveryError(RuntimeError):
    pass


class TransientDeliveryError(DeliveryError):
    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PermanentDeliveryError(DeliveryError):
    pass


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed - now).total_seconds())


class NtfyPublisher:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        timeout = httpx.Timeout(settings.ntfy_timeout_seconds)
        headers: dict[str, str] = {}
        if settings.ntfy_access_token is not None:
            headers["Authorization"] = f"Bearer {settings.ntfy_access_token.get_secret_value()}"
        self.headers = headers
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def publish(self, occurrence: ClaimedOccurrence) -> None:
        payload = {
            "topic": self.settings.ntfy_topic.get_secret_value(),
            "title": occurrence.title,
            "message": occurrence.message,
            "priority": occurrence.ntfy_priority,
            "click": occurrence.click_url,
            "tags": [self.settings.notification_tag],
            "markdown": True,
            # ntfy uses this to update the same client notification on an ambiguous retry.
            "sequence_id": occurrence.ntfy_message_id,
        }
        try:
            response = await self.client.post(
                self.settings.ntfy_base_url, json=payload, headers=self.headers
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientDeliveryError(type(exc).__name__) from exc
        if 200 <= response.status_code < 300:
            return
        error = f"ntfy returned HTTP {response.status_code}"
        if response.status_code in {408, 429} or response.status_code >= 500:
            retry_after = parse_retry_after(response.headers.get("Retry-After"), datetime.now(UTC))
            raise TransientDeliveryError(error, retry_after)
        raise PermanentDeliveryError(error)
