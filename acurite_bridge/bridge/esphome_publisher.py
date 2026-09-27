"""Publish captured UART weather through the receiver's native ESPHome service.

Purpose: automatic local publication without delaying modem serial parsing.
Effect: one background API connection calls only publish_upload(query=...).
Disposal: await close() to cancel retries, discard pending data, and disconnect.
No credentials, raw payloads, queries, or weather values are logged or saved.

dateutc is the host's weather-event capture timestamp, not a station-reported
observation time. A completed execute_service coroutine means the native API
request was sent; this service supplies no execution or Home Assistant receipt.
aioesphomeapi 46.6.0 is imported lazily inside the worker, never by submit().
Use from one asyncio event loop; start() is optional because submit() starts it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from urllib.parse import urlencode


class IdentityMismatch(Exception):
    pass


class PublishServiceMissing(Exception):
    pass


def _mac(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{12}|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", value
    ):
        raise ValueError("invalid MAC")
    return value.replace(":", "").lower()


@dataclass(frozen=True)
class _Upload:
    captured: datetime
    query: str
    count: int


class ESPHomePublisher:
    def __init__(
        self,
        event: Callable[[dict], None],
        *,
        allowed_keys: Collection[str],
        host: str = "192.0.2.10",
        port: int = 6053,
        expected_mac: str = "020000000010",
        noise_psk: str | None = None,
        max_age: float = 300,
        retry_delay: float = 1,
        max_retry_delay: float = 5,
        request_timeout: float = 15,
        client_factory=None,
        clock: Callable[[], datetime] | None = None,
        sleep=asyncio.sleep,
    ):
        self.allowed_keys = frozenset(allowed_keys)
        if any(not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k)
               or k == "dateutc" for k in self.allowed_keys):
            raise ValueError("invalid weather allowlist")
        if not 1 <= port <= 65535 or not 0 < max_age <= 300 or request_timeout <= 0:
            raise ValueError("invalid publisher limits")
        if not 1 <= retry_delay <= max_retry_delay <= 5:
            raise ValueError("retry delay must remain between one and five seconds")
        self.event = event
        self.host, self.port = host, port
        self.expected_mac = _mac(expected_mac)
        self.noise_psk = noise_psk
        self.max_age = max_age
        self.retry_delay, self.max_retry_delay = retry_delay, max_retry_delay
        self.request_timeout = request_timeout
        self.client_factory = client_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self._pending: _Upload | None = None  # Capacity one; new submissions replace this item.
        self._wake = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._client = None
        self._service = None
        self._closed = False

    def _emit(self, name: str, *, count: int | None = None, error: Exception | None = None) -> None:
        record = {"event": name, "time": self.clock().astimezone(timezone.utc).isoformat(timespec="seconds")}
        if count is not None:
            record["count"] = count
        if error is not None:
            record["error"] = type(error).__name__
        self.event(record)

    def _stale(self, item: _Upload) -> bool:
        return (self.clock() - item.captured).total_seconds() > self.max_age

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("publisher is closed")
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def submit(self, weather_event: dict) -> bool:
        """Queue one actual weather event synchronously; never await API I/O."""
        if self._closed or not isinstance(weather_event, dict) or weather_event.get("event") != "weather":
            return False
        try:
            captured = datetime.fromisoformat(weather_event["time"])
            if captured.tzinfo is None or not isinstance(weather_event["values"], dict):
                raise ValueError("invalid capture timestamp or values")
            captured = captured.astimezone(timezone.utc)
            values = {}
            for key in sorted(self.allowed_keys):
                value = weather_event["values"].get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                try:
                    if math.isfinite(value):
                        values[key] = str(value)
                except OverflowError:
                    continue
            if not values:
                raise ValueError("no finite allowlisted readings")
            texts = weather_event.get("texts", {})
            if isinstance(texts, dict):
                for key in ("sensorbattery", "hubbattery"):
                    value = texts.get(key)
                    if key in self.allowed_keys and isinstance(value, str) and value in {"normal", "low"}:
                        values[key] = value
            query = urlencode({**values, "dateutc": captured.strftime("%Y-%m-%d %H:%M:%S")})
            if len(query.encode("ascii")) > 4096:
                raise ValueError("query exceeds service limit")
            item = _Upload(captured, query, len(values))
        except (KeyError, TypeError, ValueError) as exc:
            self._emit("publisher_rejected", count=1, error=exc)
            return False
        if self._stale(item):
            self._emit("publisher_stale", count=1)
            return False
        if self._pending is not None:
            self._emit("publisher_replaced", count=1)
        self._pending = item
        self._wake.set()
        self.start()
        return True

    async def _connect(self) -> None:
        factory = self.client_factory
        if factory is None:
            from aioesphomeapi import APIClient
            factory = APIClient
        client = factory(self.host, self.port, password=None, expected_mac=self.expected_mac,
                         client_info="AcuRite Atlas Bridge", noise_psk=self.noise_psk)
        self._client = client
        self._disconnected.clear()

        async def stopped(expected_disconnect: bool) -> None:
            # A device-initiated graceful stop also requires reconnection.
            if self._client is client and not self._closed:
                self._disconnected.set()
                self._wake.set()

        await asyncio.wait_for(client.connect(on_stop=stopped, login=True, log_errors=False), self.request_timeout)
        info = await asyncio.wait_for(client.device_info(), self.request_timeout)
        if _mac(info.mac_address) != self.expected_mac:
            raise IdentityMismatch()
        _, services = await asyncio.wait_for(client.list_entities_services(), self.request_timeout)
        self._service = next((s for s in services if s.name == "publish_upload"
                              and len(s.args) == 1 and s.args[0].name == "query"
                              and getattr(s.args[0].type, "name", None) == "STRING"), None)
        if self._service is None:
            raise PublishServiceMissing()
        self._emit("publisher_connected", count=1)

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        self._service = None
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(force=True), self.request_timeout)
            except Exception as exc:
                self._emit("publisher_disconnect_error", error=exc)

    async def _run(self) -> None:
        delay = self.retry_delay
        try:
            while not self._closed:
                if self._pending is not None and self._stale(self._pending):
                    self._pending = None
                    self._emit("publisher_stale", count=1)
                try:
                    if self._disconnected.is_set() and self._client is not None:
                        self._emit("publisher_disconnected", count=1)
                        await self._disconnect()
                        await self.sleep(delay)
                    if self._client is None:
                        await self._connect()
                    if self._disconnected.is_set():
                        continue
                    item = self._pending
                    if item is None:
                        delay = self.retry_delay
                        self._wake.clear()
                        if not self._disconnected.is_set():
                            await self._wake.wait()
                        continue
                    if self._stale(item):
                        self._pending = None
                        self._emit("publisher_stale", count=1)
                        continue
                    # Version 46.6.0 requires awaiting this coroutine. Leaving
                    # return_response=None sends without claiming execution ACK.
                    await asyncio.wait_for(
                        self._client.execute_service(self._service, {"query": item.query}),
                        self.request_timeout,
                    )
                    if self._pending is item:
                        self._pending = None
                    self._emit("publisher_submitted", count=item.count)
                    delay = self.retry_delay
                except Exception as exc:
                    self._emit("publisher_error", error=exc)
                    await self._disconnect()
                    await self.sleep(delay)
                    delay = min(self.max_retry_delay, delay * 2)
        finally:
            await self._disconnect()

    async def close(self) -> None:
        self._closed = True
        self._pending = None
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
