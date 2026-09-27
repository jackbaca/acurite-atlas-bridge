"""Read-only, bounded inspection of the display's existing forecast response.

Attach only to an outbound ``display.myacurite.com`` connection, feed the exact
HTTP response bytes, then call ``eof()`` when it closes. The caller still forwards
all original bytes unchanged. No URL, postal code, location, headers, arbitrary
JSON, or raw response is emitted or retained after parsing. Retain this source
and tests; the in-memory inspection buffer is disposable.

Schema source: the original 06088-RX firmware author's working forecast template:
https://community.home-assistant.io/t/acurite-weather-station-display-esphome-firmware/672058
Its current/tomorrow structure is a candidate for the 06099M response, not yet
verified on that model. Temperature numbers retain the provider's units; icon
numbers are opaque codes, not a claimed mapping to weather condition names.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable


MAX_RESPONSE = 16384
MAX_HEADER = 4096
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def _number(value: object, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if len(value) > 24 or not _NUMBER.fullmatch(value):
            return None
    elif not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and minimum <= result <= maximum else None


def parse_forecast(body: bytes) -> dict[str, float | int]:
    """Return only known numeric leaves; unknown data never leaves this function."""
    if len(body) > MAX_RESPONSE:
        return {}
    try:
        document = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return {}
    if not isinstance(document, dict):
        return {}
    values = {}
    for day in ("current", "tomorrow"):
        record = document.get(day)
        if not isinstance(record, dict):
            continue
        temperature = record.get("temperature")
        if isinstance(temperature, dict):
            for extreme in ("high", "low"):
                number = _number(temperature.get(extreme), -150, 150)
                if number is not None:
                    values[f"{day}_temperature_{extreme}"] = number
        precipitation = record.get("precipitation")
        if isinstance(precipitation, dict):
            value = precipitation.get("probability")
            if isinstance(value, str) and value.endswith("%"):
                value = value[:-1]
            number = _number(value, 0, 100)
            if number is not None:
                values[f"{day}_precipitation_probability"] = number
        icon = _number(record.get("icon"), 0, 255)
        if icon is not None and icon.is_integer():
            values[f"{day}_icon"] = int(icon)
    return values


class ForecastInspector:
    """Inspect one HTTP response, emitting allowlisted fields or a fixed reason."""

    def __init__(self, emit: Callable[..., None], link_id: int):
        self.emit = emit
        self.link_id = link_id
        self.buffer = bytearray()
        self.done = False
        self._body_start: int | None = None
        self._length: int | None = None
        self._chunked = False

    def feed(self, data: bytes) -> None:
        if self.done:
            return
        if len(data) > MAX_RESPONSE - len(self.buffer):
            self._skip("too_large")
            return
        self.buffer.extend(data)
        self._inspect(eof=False)

    def eof(self) -> None:
        if not self.done:
            self._inspect(eof=True)

    def _inspect(self, *, eof: bool) -> None:
        if self._body_start is None:
            end = self.buffer.find(b"\r\n\r\n")
            if end < 0:
                if len(self.buffer) > MAX_HEADER or eof:
                    self._skip("invalid_header")
                return
            if end > MAX_HEADER:
                self._skip("invalid_header")
                return
            lines = bytes(self.buffer[:end]).decode("latin1").split("\r\n")
            if not re.fullmatch(r"HTTP/1\.[01] 200(?: .*)?", lines[0]):
                self._skip("http_status")
                return
            headers = {}
            for line in lines[1:]:
                name, sep, value = line.partition(":")
                name = name.lower()
                if not sep or not re.fullmatch(r"[a-z0-9-]+", name) or name in headers:
                    self._skip("invalid_header")
                    return
                headers[name] = value.strip()
            if headers.get("content-encoding", "identity").lower() != "identity":
                self._skip("content_encoding")
                return
            transfer = headers.get("transfer-encoding")
            length = headers.get("content-length")
            if transfer is not None:
                if transfer.lower() != "chunked" or length is not None:
                    self._skip("invalid_framing")
                    return
                self._chunked = True
            elif length is not None:
                if not re.fullmatch(r"[0-9]{1,6}", length):
                    self._skip("invalid_framing")
                    return
                self._length = int(length)
                if end + 4 + self._length > MAX_RESPONSE:
                    self._skip("too_large")
                    return
            self._body_start = end + 4
        if self._chunked:
            body = self._chunks(eof=eof)
            if body is not None:
                self._finish(body)
        elif self._length is not None:
            end = self._body_start + self._length
            if len(self.buffer) >= end:
                self._finish(bytes(self.buffer[self._body_start:end]))
            elif eof:
                self._skip("truncated")
        elif eof:
            self._finish(bytes(self.buffer[self._body_start:]))

    def _chunks(self, *, eof: bool) -> bytes | None:
        position = self._body_start
        body = bytearray()
        while True:
            end = self.buffer.find(b"\r\n", position)
            if end < 0:
                break
            size_token = bytes(self.buffer[position:end]).split(b";", 1)[0]
            if not re.fullmatch(rb"[0-9A-Fa-f]{1,8}", size_token):
                self._skip("invalid_framing")
                return None
            size = int(size_token, 16)
            position = end + 2
            if size > MAX_RESPONSE:
                self._skip("too_large")
                return None
            if size == 0:
                # A zero chunk ends the body. Wait for empty or terminated
                # trailers without inspecting or retaining their contents.
                if (self.buffer[position:position + 2] == b"\r\n"
                        or self.buffer.find(b"\r\n\r\n", position) >= 0):
                    return bytes(body)
                break
            end = position + size
            if len(self.buffer) < end + 2:
                break
            if self.buffer[end:end + 2] != b"\r\n":
                self._skip("invalid_framing")
                return None
            body.extend(self.buffer[position:end])
            position = end + 2
        if eof:
            self._skip("truncated")
        return None

    def _finish(self, body: bytes) -> None:
        values = parse_forecast(body)
        self.buffer.clear()
        self.done = True
        if values:
            self.emit("forecast", link=self.link_id, source="cloud_forecast", values=values)
        else:
            self.emit("forecast_skip", link=self.link_id, reason="no_known_fields")

    def _skip(self, reason: str) -> None:
        self.buffer.clear()
        self.done = True
        self.emit("forecast_skip", link=self.link_id, reason=reason)
