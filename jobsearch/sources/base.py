"""Shared HTTP client with timeouts and polite retry/backoff."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "jobsearch-assistant/0.1 (personal job discovery; read-only)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class SourceError(Exception):
    pass


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


def get_json(client: httpx.Client, url: str, params: dict | None = None, attempts: int = 3):
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url, params=params)
        except httpx.TransportError as e:
            if attempt == attempts:
                raise SourceError(f"{url}: {e}") from e
            log.warning("transport error on %s (%s), retrying in %.0fs", url, e, delay)
        else:
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code not in RETRY_STATUSES or attempt == attempts:
                raise SourceError(f"{url}: HTTP {resp.status_code}")
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
            log.warning("HTTP %s on %s, retrying in %.0fs", resp.status_code, url, delay)
        time.sleep(delay)
        delay *= 2
    raise SourceError(url)  # unreachable


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def from_epoch(value: float | int | None, millis: bool = False) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000 if millis else value, tz=timezone.utc)
