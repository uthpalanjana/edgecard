"""
adapters/rest.py — RESTAdapter for polling HTTP/JSON endpoints.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

from ..card import Quality, Reading
from ..sources import ConnectionResult, DataSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth types
# ---------------------------------------------------------------------------

@dataclass
class BearerAuth:
    token: str

    def apply(self, headers: dict) -> dict:
        return {**headers, "Authorization": f"Bearer {self.token}"}


@dataclass
class BasicAuth:
    username: str
    password: str

    def apply(self, headers: dict) -> dict:
        import base64
        creds = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {**headers, "Authorization": f"Basic {creds}"}


@dataclass
class APIKeyAuth:
    header: str
    key: str

    def apply(self, headers: dict) -> dict:
        return {**headers, self.header: self.key}


# ---------------------------------------------------------------------------
# EndpointMapping
# ---------------------------------------------------------------------------

@dataclass
class EndpointMapping:
    endpoint: str
    field: str
    unit: Optional[str] = None
    json_path: Optional[str] = None  # dot-notation e.g. "attributes.temperature"
    transform: Optional[Callable[[Any], Any]] = None


# ---------------------------------------------------------------------------
# RESTAdapter
# ---------------------------------------------------------------------------

class RESTAdapter(DataSource):
    """
    Polls one or more REST endpoints and maps JSON responses to Readings.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        endpoints: list[EndpointMapping],
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 5.0,
        auth=None,  # BearerAuth | BasicAuth | APIKeyAuth | None
        poll_interval_seconds: int = 30,
    ) -> None:
        super().__init__(name=name, poll_interval_seconds=poll_interval_seconds)
        self.base_url = base_url.rstrip("/")
        self.endpoints = endpoints
        self._base_headers = headers or {}
        self.timeout_seconds = timeout_seconds
        self.auth = auth
        self._last_readings: dict[str, Reading] = {}

    def _build_headers(self) -> dict:
        headers = dict(self._base_headers)
        if self.auth is not None:
            headers = self.auth.apply(headers)
        return headers

    def _get_json_path(self, data: Any, json_path: str) -> Any:
        parts = json_path.split(".")
        cur = data
        for p in parts:
            if isinstance(cur, dict):
                cur = cur[p]
            else:
                raise KeyError(f"Cannot traverse path '{json_path}': '{p}' not accessible")
        return cur

    def _fetch_endpoint(self, mapping: EndpointMapping) -> Reading:
        url = f"{self.base_url}/{mapping.endpoint.lstrip('/')}"
        headers = self._build_headers()
        now = datetime.now(timezone.utc)

        for attempt in range(2):
            try:
                if not _HTTPX_AVAILABLE:
                    raise ImportError("httpx is not installed")
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.get(url, headers=headers)
                    response.raise_for_status()
                    data = response.json()

                if mapping.json_path:
                    raw_value = self._get_json_path(data, mapping.json_path)
                else:
                    raw_value = data

                if mapping.transform is not None:
                    raw_value = mapping.transform(raw_value)

                return Reading(
                    value=raw_value,
                    unit=mapping.unit,
                    quality=Quality.measured,
                    timestamp=now,
                )
            except Exception as exc:
                if attempt == 0:
                    logger.warning(
                        "RESTAdapter '%s': attempt 1 failed for field '%s': %s — retrying",
                        self.name, mapping.field, exc,
                    )
                    time.sleep(1)
                else:
                    logger.error(
                        "RESTAdapter '%s': all attempts failed for field '%s': %s",
                        self.name, mapping.field, exc,
                    )
                    # Return stale if we have a prior reading
                    if mapping.field in self._last_readings:
                        old = self._last_readings[mapping.field]
                        return Reading(value=old.value, unit=old.unit, quality=Quality.stale, timestamp=now)
                    return Reading(value=0, unit=mapping.unit, quality=Quality.stale, timestamp=now)

    def poll(self) -> dict[str, Reading]:
        results: dict[str, Reading] = {}
        for mapping in self.endpoints:
            results[mapping.field] = self._fetch_endpoint(mapping)
        self._last_readings = results
        return results

    def test_connection(self) -> ConnectionResult:
        if not _HTTPX_AVAILABLE:
            return ConnectionResult(success=False, message="httpx not installed")
        try:
            headers = self._build_headers()
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.head(self.base_url, headers=headers)
            if 200 <= response.status_code < 500:
                return ConnectionResult(success=True, message=f"HEAD {self.base_url} → {response.status_code}")
            return ConnectionResult(success=False, message=f"HEAD {self.base_url} → {response.status_code}")
        except Exception as exc:
            return ConnectionResult(success=False, message=str(exc))
