#!/usr/bin/env python3
"""Host ESP-AT experiment for the receiver's raw TCP-to-UART bridge.

Purpose: exercise the display's real cloud dialogue and observe weather uploads.
Effect: replies on the display UART, opens permitted cloud TCP/TLS connections,
and maps a requested TCP server to an ephemeral port on 127.0.0.1 only.
Disposal: stop this process to end the experiment; no credentials or raw captures
are saved. Keep this source and sanitized weather/status receipts.

Network scan/join responses describe a simulated station on an already connected
bridge. This is not a replacement Wi-Fi driver. Unknown commands return ERROR.
Start a bounded trial: python tools/modem_host.py --duration 120
Weather JSON goes to stdout; sanitized protocol events go to stderr. The optional
--publish-esphome worker sends captured readings to the receiver's native API.
Outgoing HTTP remains unmodified; the server's
actual bytes are forwarded even when local weather inspection skips a request.
Initial state is associated with no socket (STATUS:2), echo enabled, CIPMUX=1.
TLS uses system certificate verification; modem TLS preference commands do not
disable it. Scan records are simulated, not measurements of nearby networks.
Server events report requested and actual loopback ports; inbound bytes are
forwarded to the receiver without logging or inspecting their contents.

Framing references:
https://docs.espressif.com/projects/esp-at/en/release-v2.2.0.0_esp8266/AT_Command_Set/TCP-IP_AT_Commands.html
https://docs.espressif.com/projects/esp-at/en/release-v2.1.0.0_esp8266/AT_Command_Set/Wi-Fi_AT_Commands.html
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import sys
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlsplit


MAX_SEND = 8192
MAX_COMMAND = 2048
MAX_HTTP = 65536
WEATHER_KEYS = frozenset(
    "tempf tempc humidity baromin baromabsin pressure dewptf dewpt windspeedmph "
    "windgustmph winddir winddirdeg rainin dailyrainin weeklyrainin monthlyrainin "
    "yearlyrainin totalrainin lightintensity measured_light_seconds uv uvi "
    "solarradiation heatindex feelslike windchill rssi indoortempf indoorhumidity "
    "strike_count strike_distance lightningcount lightningdistance uvindex "
    "windgustdir windspeedavgmph hubbattery sensorbattery strikecount interference "
    "last_strike_distance".split()
)
DEFAULT_DOMAINS = ("myacurite.com", "acu-link.com", "wunderground.com")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def quote_at(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def normalize_host(host: str) -> str:
    host = host.rstrip(".").lower()
    if not host or len(host) > 253:
        raise ValueError("invalid host")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                   for p in host.split(".")):
            raise ValueError("invalid host") from None
        return host


def parse_args_at(value: str) -> list[str]:
    return next(csv.reader([value], escapechar="\\", skipinitialspace=True, strict=True))


def safe_path(target: str) -> str:
    """Never log arbitrary path segments, which might contain an identifier."""
    path = urlsplit(target).path
    known = {"/", "/weatherstation/updateweatherstation", "/weatherstation/updateweatherstation.php",
             "/updateweatherstation.php"}
    return path if path in known else "<other>"


class HttpInspector:
    """Bounded in-memory request framing; emits allowlisted numbers only."""

    def __init__(self, emit: Callable[..., None], link_id: int):
        self.buffer = bytearray()
        self.emit = emit
        self.link_id = link_id
        self.disabled = False

    def feed(self, data: bytes) -> None:
        if self.disabled:
            return
        self.buffer.extend(data)
        if len(self.buffer) > MAX_HTTP:
            self._disable("too_large")
            return
        while self.buffer:
            end = self.buffer.find(b"\r\n\r\n")
            if end < 0:
                return
            head = bytes(self.buffer[:end]).decode("latin1")
            lines = head.split("\r\n")
            first = lines[0].split(" ")
            if len(first) != 3 or first[0] not in {"GET", "POST", "PUT", "HEAD"}:
                self._disable("unsupported_request")
                return
            headers = {}
            for line in lines[1:]:
                name, sep, value = line.partition(":")
                if not sep:
                    self._disable("invalid_header")
                    return
                headers[name.lower()] = value.strip()
            if "transfer-encoding" in headers:
                self._disable("transfer_encoding")
                return
            try:
                size = int(headers.get("content-length", "0"))
            except ValueError:
                self._disable("invalid_length")
                return
            if size < 0 or end + 4 + size > MAX_HTTP:
                self._disable("invalid_length")
                return
            total = end + 4 + size
            if len(self.buffer) < total:
                return
            body = bytes(self.buffer[end + 4:total])
            del self.buffer[:total]
            try:
                query = urlsplit(first[1]).query
                values = parse_qsl(query, keep_blank_values=True, max_num_fields=256)
                if headers.get("content-type", "").split(";")[0].lower() == "application/x-www-form-urlencoded":
                    values += parse_qsl(body.decode("ascii"), max_num_fields=256)
            except (ValueError, UnicodeError):
                self.emit("inspection_skip", link=self.link_id, reason="invalid_query")
                continue
            # Parameter names reveal missing measurements without retaining
            # credentials, identifiers, or arbitrary unrecognized values.
            fields = sorted({name.lower() for name, _ in values
                             if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", name)})
            self.emit("http_request", link=self.link_id, method=first[0], path=safe_path(first[1]),
                      bytes=total, fields=fields)
            weather = {}
            texts = {}
            for name, value in values:
                key = name.lower()
                if key in {"sensorbattery", "hubbattery"}:
                    if value.lower() in {"normal", "low"}:
                        texts[key] = value.lower()
                    else:
                        self.emit("battery_status_unknown", field=key)
                    continue
                if key not in WEATHER_KEYS:
                    continue
                try:
                    number = float(value)
                except ValueError:
                    continue
                if math.isfinite(number):
                    weather[key] = number
            if weather:
                extra = {"texts": texts} if texts else {}
                self.emit("weather", link=self.link_id, source="uart_upload", values=weather, **extra)

    def _disable(self, reason: str) -> None:
        self.buffer.clear()
        self.disabled = True
        self.emit("inspection_skip", link=self.link_id, reason=reason)


@dataclass
class Link:
    ident: int
    kind: str
    host: str
    port: int
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    inspector: HttpInspector
    multi: bool
    server: bool = False
    pump: asyncio.Task | None = None
    idle_pump: asyncio.Task | None = None
    last_activity: float = 0
    activity_changed: asyncio.Event = field(default_factory=asyncio.Event)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Modem:
    def __init__(
        self,
        send: Callable[[bytes], Awaitable[None]],
        event: Callable[[dict], None],
        *,
        ssid: str = "weather-network",
        station_ip: str = "192.0.2.10",
        station_mac: str = "02:00:00:00:00:10",
        ap_bssid: str = "02:00:00:00:00:01",
        ap_channel: int = 6,
        ap_rssi: int = -45,
        allow_hosts: tuple[str, ...] = (),
        connector=asyncio.open_connection,
        connect_timeout: float = 10,
        initial_status: int = 2,
        ack_closed_links: bool = False,
    ):
        if any(c in ssid for c in "\r\n\0") or len(ssid.encode()) > 32:
            raise ValueError("invalid SSID")
        ipaddress.ip_address(station_ip)
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", station_mac):
            raise ValueError("invalid station MAC")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", ap_bssid):
            raise ValueError("invalid AP BSSID")
        if not 1 <= ap_channel <= 14 or not -127 <= ap_rssi <= 0:
            raise ValueError("invalid AP channel or RSSI")
        self.send = send
        self.event = event
        self.ssid = ssid
        self.station_ip = station_ip
        self.station_mac = station_mac
        self.bssid = ap_bssid
        self.ap_channel = ap_channel
        self.ap_rssi = ap_rssi
        self.allowed = {normalize_host(h) for h in allow_hosts}
        self.connector = connector
        self.connect_timeout = connect_timeout
        self.echo = True
        self.joined = True
        if initial_status not in {2, 4}:
            raise ValueError("initial status must describe an idle station")
        self.ever_connected = initial_status == 4
        self.ack_closed_links = ack_closed_links
        self.multi = True  # Attach to existing receiver workflow, whose factory slot is 3.
        self.mode = 1
        # Setup-network preferences are held only in memory; this diagnostic
        # host does not create a radio AP or log its name/password.
        self.ap_config = ["acurite-diagnostic", "", "1", "0", "4", "0"]
        self.ipd_info = False
        self.scan_mask = 31
        self.sntp_enabled = True
        self.sntp_timezone = 0
        self.sntp_servers = ["pool.ntp.org"]
        self.sni: dict[int | None, str] = {}
        self.resolved: dict[str, str] = {}
        self.links: dict[int, Link] = {}
        self.server: asyncio.Server | None = None
        self.server_port: int | None = None
        self.server_generation = 0
        self.server_timeout = 300  # Host diagnostic default; not a claimed factory setting.
        self.buffer = bytearray()
        self.pending: tuple[int, int] | None = None
        self.dropping_command = False
        self.output_lock = asyncio.Lock()
        self.input_lock = asyncio.Lock()
        self.closed = False

    def emit(self, event: str, **fields) -> None:
        self.event({"event": event, "time": utc_now(), **fields})

    async def write(self, data: bytes) -> None:
        async with self.output_lock:
            await self.send(data)

    async def reply(self, *lines: str) -> None:
        await self.write(("\r\n" + "\r\n".join(lines) + "\r\n").encode("ascii"))

    def host_allowed(self, host: str) -> bool:
        return host in self.allowed or any(host == d or host.endswith("." + d) for d in DEFAULT_DOMAINS)

    async def feed(self, data: bytes) -> None:
        """Consume arbitrary bridge fragments; exact CIPSEND counts take precedence."""
        async with self.input_lock:
            self.buffer.extend(data)
            while self.buffer and not self.closed:
                if self.pending is not None:
                    ident, size = self.pending
                    if len(self.buffer) < size:
                        return
                    payload = bytes(self.buffer[:size])
                    del self.buffer[:size]
                    self.pending = None
                    await self._payload(ident, payload)
                    continue
                end = self.buffer.find(b"\n")
                if end < 0:
                    if len(self.buffer) > MAX_COMMAND:
                        self.buffer.clear()
                        self.dropping_command = True
                    return
                raw = bytes(self.buffer[:end]).rstrip(b"\r")
                del self.buffer[:end + 1]
                if self.dropping_command or len(raw) > MAX_COMMAND:
                    self.dropping_command = False
                    self.emit("command_error", reason="too_long")
                    await self.reply("ERROR")
                    continue
                if not raw:
                    continue
                try:
                    command = raw.decode("ascii")
                    if any(ord(c) < 32 or ord(c) > 126 for c in command):
                        raise ValueError("nonprintable command")
                    await self._command(command)
                except (ValueError, UnicodeError, csv.Error, IndexError):
                    self.emit("command_error", reason="invalid_syntax")
                    await self.reply("ERROR")

    async def _command(self, command: str) -> None:
        # Only the command token is logged. Echo returns arguments exclusively to
        # their originating MCU; Wi-Fi passwords are never included in events.
        match = re.fullmatch(r"(AT(?:\+[A-Z0-9_]+|E[01])?)(\?|=(.*))?", command, re.I)
        if not match:
            raise ValueError("invalid command")
        name = match[1].upper()
        suffix = match[2] or ""
        value = match[3]
        self.emit("at", command=name, operation="set" if value is not None else "query" if suffix == "?" else "execute")
        if self.echo:
            await self.write(command.encode("ascii") + b"\r\n")
        base = re.sub(r"_(?:DEF|CUR)$", "", name)
        args = parse_args_at(value) if value is not None else []
        if name == "AT":
            await self.reply("OK")
        elif name in {"ATE0", "ATE1"}:
            self.echo = name == "ATE1"
            await self.reply("OK")
        elif name == "AT+RST":
            await self._stop_server(notify=False)
            await self._close_all(notify=False)
            self.pending = None
            self.echo = True
            self.joined = True
            self.ever_connected = False
            await self.reply("OK", "ready", "WIFI CONNECTED", "WIFI GOT IP")
        elif base == "AT+CWMODE":
            if value is None:
                await self.reply(f"+{name[3:]}:{self.mode}", "OK")
            else:
                mode = int(args[0])
                if mode not in {1, 3}:
                    raise ValueError("station mode required")
                self.mode = mode
                await self.reply("OK")
                self.emit("wifi_mode", mode=mode)
        elif base == "AT+CWSAP":
            if suffix == "?":
                fields = [quote_at(self.ap_config[0]), quote_at(self.ap_config[1]), *self.ap_config[2:]]
                await self.reply(f"+{name[3:]}:{','.join(fields)}", "OK")
            elif value is not None:
                if not 4 <= len(args) <= 6 or len(args[0].encode()) > 32 or len(args[1]) > 64:
                    raise ValueError("invalid AP configuration")
                channel, encryption = int(args[2]), int(args[3])
                clients = int(args[4]) if len(args) > 4 else 4
                hidden = int(args[5]) if len(args) > 5 else 0
                if not 1 <= channel <= 14 or encryption not in {0, 2, 3, 4} or not 1 <= clients <= 4 or hidden not in {0, 1}:
                    raise ValueError("invalid AP configuration")
                self.ap_config = [args[0], args[1], str(channel), str(encryption), str(clients), str(hidden)]
                await self.reply("OK")
                self.emit("ap_config", simulated=True, channel=channel, encryption=encryption)
            else:
                await self.reply("ERROR")
        elif base == "AT+CWJAP":
            if value is None:
                if self.joined:
                    await self.reply(f"+{name[3:]}:{quote_at(self.ssid)},{quote_at(self.bssid)},{self.ap_channel},{self.ap_rssi}", "OK")
                else:
                    await self.reply("No AP", "OK")
            else:
                # Consume but never persist password or requested SSID.
                if len(args) < 2:
                    raise ValueError("missing join arguments")
                if args[0] != self.ssid:
                    await self.reply("+CWJAP:3", "FAIL")
                else:
                    self.joined = True
                    self.ever_connected = False
                    await self.reply("WIFI CONNECTED", "WIFI GOT IP", "OK")
        elif name == "AT+CWQAP":
            # Leaving the station network does not shut down the separate
            # SoftAP/server side of this simulated modem.
            for ident, link in list(self.links.items()):
                if not link.server:
                    await self._close_link(ident)
            self.joined = False
            await self.reply("WIFI DISCONNECT", "OK")
        elif name == "AT+CWLAPOPT":
            mask = int(args[1])
            if not 0 <= mask <= 31:
                raise ValueError("unsupported scan mask")
            self.scan_mask = mask
            await self.reply("OK")
            self.emit("scan_config", mask=mask)
        elif name == "AT+CWLAP":
            if args and args[0] and args[0] != self.ssid:
                await self.reply("OK")
            else:
                fields = ["3", quote_at(self.ssid), str(self.ap_rssi), quote_at(self.bssid), str(self.ap_channel)]
                record = ",".join(v for i, v in enumerate(fields) if self.scan_mask & (1 << i))
                await self.reply(f"+CWLAP:({record})", "OK")
                self.emit("scan", simulated=True, records=1)
        elif name == "AT+CIFSR":
            await self.reply(f'+CIFSR:STAIP,"{self.station_ip}"', f'+CIFSR:STAMAC,"{self.station_mac}"', "OK")
        elif name == "AT+CIPSNTPCFG":
            if suffix == "?":
                servers = "".join("," + quote_at(server) for server in self.sntp_servers)
                await self.reply(f"+CIPSNTPCFG:{int(self.sntp_enabled)},{self.sntp_timezone}{servers}", "OK")
            elif value is not None:
                if not 2 <= len(args) <= 5 or args[0] not in {"0", "1"}:
                    raise ValueError("invalid SNTP configuration")
                offset = int(args[1])
                if not -11 <= offset <= 13:
                    raise ValueError("invalid SNTP timezone")
                servers = [normalize_host(server) for server in args[2:]]
                self.sntp_enabled = args[0] == "1"
                self.sntp_timezone = offset
                if servers:
                    self.sntp_servers = servers
                await self.reply("OK")
                self.emit("clock_config", enabled=self.sntp_enabled, offset_hours=offset,
                          source="host_clock", configured_servers=len(servers))
            else:
                self.sntp_enabled = False
                self.sntp_timezone = 0
                self.sntp_servers = []
                await self.reply("OK")
        elif name == "AT+CIPSNTPTIME" and suffix == "?":
            # ESP8266 AT uses the English C asctime layout, including a
            # space-padded day. The host clock supplies actual UTC (the modem's
            # configured SNTP offset), not a fixed success placeholder. Server
            # preferences are emulated; host time synchronization supplies time.
            now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=self.sntp_timezone)))
            weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[now.weekday()]
            month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[now.month - 1]
            value = f"{weekday} {month} {now.day:2d} {now:%H:%M:%S %Y}"
            await self.reply(f"+CIPSNTPTIME:{value}", "", "OK")
            self.emit("clock", source="host_clock", offset_hours=self.sntp_timezone)
        elif name == "AT+CIPSTATUS":
            state = 3 if self.links else 5 if not self.joined else 4 if self.ever_connected else 2
            rows = [f"STATUS:{state}"]
            for ident, link in sorted(self.links.items()):
                peer = link.writer.get_extra_info("peername") or (link.host, link.port)
                local = link.writer.get_extra_info("sockname") or ("", 0)
                local_port = self.server_port if link.server else local[1]
                rows.append(f'+CIPSTATUS:{ident},"{link.kind}","{peer[0]}",{link.port},{local_port},{int(link.server)}')
            await self.reply(*rows, "OK")
            self.emit("status", state=state, links=sorted(self.links))
        elif name == "AT+CIPMUX":
            if value is None:
                await self.reply(f"+CIPMUX:{int(self.multi)}", "OK")
            elif args[0] not in {"0", "1"} or self.links or self.server is not None:
                await self.reply("ERROR")
            else:
                self.multi = args[0] == "1"
                await self.reply("OK")
                self.emit("multiplexing", enabled=self.multi)
        elif name == "AT+CIPSERVER":
            if value is None or not 1 <= len(args) <= 2 or args[0] not in {"0", "1"}:
                raise ValueError("invalid server command")
            port = int(args[1]) if len(args) == 2 else 333
            if not 1 <= port <= 65535:
                raise ValueError("invalid server port")
            if args[0] == "1":
                await self._start_server(port)
            else:
                await self._stop_server()
                await self.reply("OK")
        elif name == "AT+CIPSTO":
            if suffix == "?":
                await self.reply(f"+CIPSTO:{self.server_timeout}", "OK")
            elif value is not None and len(args) == 1:
                timeout = int(args[0])
                if not 0 <= timeout <= 7200:
                    raise ValueError("invalid server timeout")
                self.server_timeout = timeout
                for link in self.links.values():
                    if link.server:
                        link.activity_changed.set()
                await self.reply("OK")
            else:
                raise ValueError("invalid server timeout command")
        elif name in {"AT+CIPMODE", "AT+CIPRECVMODE"}:
            if value is None:
                await self.reply(f"+{name[3:]}:0", "OK")
            elif args == ["0"]:
                await self.reply("OK")
            else:
                await self.reply("ERROR")
        elif name == "AT+CIPDINFO":
            if args not in [["0"], ["1"]]:
                raise ValueError("invalid IPD mode")
            self.ipd_info = args == ["1"]
            await self.reply("OK")
        elif name in {"AT+SNISERVER", "AT+SLISERVER", "AT+CIPSSLCSNI", "AT+CIPSSLSNI"}:
            ident = int(args[0]) if len(args) == 2 else None
            if len(args) not in {1, 2} or (ident is not None and not 0 <= ident <= 4):
                raise ValueError("invalid SNI")
            host = normalize_host(args[-1])
            if not self.host_allowed(host):
                raise ValueError("SNI not permitted")
            self.sni[ident] = host
            await self.reply("OK")
        elif name == "AT+CIPDOMAIN":
            host = normalize_host(args[0])
            if not self.host_allowed(host):
                raise ValueError("DNS host not permitted")
            try:
                results = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM), self.connect_timeout)
                address = results[0][4][0]
                self.resolved[address] = host
                await self.reply(f'+CIPDOMAIN:{address}', "OK")
            except OSError:
                await self.reply("ERROR")
        elif name == "AT+CIPSTART":
            await self._connect(args)
        elif name == "AT+CIPSEND":
            if len(args) != (2 if self.multi else 1):
                raise ValueError("send shape")
            ident = int(args[0]) if self.multi else 0
            size = int(args[-1])
            if ident not in self.links or not 1 <= size <= MAX_SEND:
                await self.reply("ERROR")
            else:
                self.pending = (ident, size)
                await self.write(b"\r\nOK\r\n>")
                self.emit("send_pending", link=ident, bytes=size)
        elif name == "AT+CIPCLOSE":
            if self.multi and not args:
                raise ValueError("missing link")
            ident = int(args[0]) if args else 0
            if ident == 5 and self.multi:
                await self._close_all()
                await self.reply("OK")
            elif ident in self.links:
                await self._close_link(ident)
                await self.reply("OK")
            elif self.multi and 0 <= ident <= 4:
                # Factory-era ESP8266 AT multi-link close uses this distinct
                # response when the slot is already absent (no trailing ERROR).
                # See Espressif ESP8266_AT/at/user/at_ipCmd.c at_setupCmdCipclose.
                if self.ack_closed_links:
                    # Explicit compatibility experiment: report the requested
                    # closed state idempotently. No connection is fabricated.
                    self.emit("close_missing", link=ident, response="closed_ack")
                    await self.reply(f"{ident},CLOSED", "OK")
                else:
                    self.emit("close_missing", link=ident, response="link_is_not")
                    await self.reply("link is not")
            else:
                await self.reply("ERROR")
        elif name == "AT+GMR":
            await self.reply("AT version:1.7.4.0(host-modem experiment)", "OK")
        elif base in {"AT+CWAUTOCONN", "AT+CWDHCP"} or name in {"AT+CIPSSLSIZE", "AT+CIPSSLCCONF", "AT+SYSSTORE"}:
            # Host TLS uses system certificate verification regardless of the
            # module's remembered TLS allocation/authentication preferences.
            if value is None:
                await self.reply("ERROR")
            else:
                await self.reply("OK")
        else:
            self.emit("unsupported", command=name)
            await self.reply("ERROR")

    async def _start_server(self, requested_port: int) -> None:
        if self.server is not None and self.server_port == requested_port:
            # Repeated enable requests leave the already-listening server up.
            # Reporting failure here sends this display into a tight retry loop.
            await self.reply("no change", "OK")
            return
        if not self.multi or self.server is not None:
            await self.reply("ERROR")
            return
        self.server_generation += 1
        generation = self.server_generation
        try:
            # Never bind the requested port or a LAN interface. This listener is
            # a local diagnostic route into the receiver's own server handler.
            server = await asyncio.start_server(
                lambda reader, writer: self._accept_client(reader, writer, generation),
                "127.0.0.1", 0, start_serving=False,
            )
            self.server = server
            self.server_port = requested_port
            await server.start_serving()
        except OSError as exc:
            await self._stop_server(notify=False)
            self.emit("server_error", error=type(exc).__name__)
            await self.reply("ERROR")
            return
        actual_port = server.sockets[0].getsockname()[1]
        self.emit("server_listening", requested_port=requested_port, loopback_port=actual_port)
        await self.reply("OK")

    def _accept_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       generation: int) -> None:
        ident = next((i for i in range(5) if i not in self.links), None)
        if self.closed or self.server is None or generation != self.server_generation or ident is None:
            writer.close()
            return
        peer = writer.get_extra_info("peername")
        link = Link(ident, "TCP", peer[0], peer[1], reader, writer,
                    HttpInspector(self.emit, ident), True, server=True,
                    last_activity=asyncio.get_running_loop().time())
        self.links[ident] = link
        self.ever_connected = True
        link.pump = asyncio.create_task(self._serve_client(link))

    async def _serve_client(self, link: Link) -> None:
        try:
            await self.reply(f"{link.ident},CONNECT")
            self.emit("server_connected", link=link.ident)
            link.idle_pump = asyncio.create_task(self._server_idle(link))
            await self._cloud_reader(link)
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            if self.links.get(link.ident) is link:
                await self._close_link(link.ident)

    async def _server_idle(self, link: Link) -> None:
        while self.links.get(link.ident) is link:
            timeout = None
            if self.server_timeout:
                timeout = max(0, link.last_activity + self.server_timeout - asyncio.get_running_loop().time())
                if not timeout:
                    self.emit("server_timeout", link=link.ident)
                    await self._close_link(link.ident)
                    return
            try:
                await asyncio.wait_for(link.activity_changed.wait(), timeout)
                link.activity_changed.clear()
            except asyncio.TimeoutError:
                pass  # Recompute against the latest timeout and activity.

    async def _stop_server(self, notify: bool = True) -> None:
        server, self.server = self.server, None
        self.server_port = None
        self.server_generation += 1
        if server is not None:
            server.close()
        for ident, link in list(self.links.items()):
            if link.server:
                await self._close_link(ident, notify=notify)
        if server is not None:
            await server.wait_closed()
            self.emit("server_closed")

    async def _connect(self, args: list[str]) -> None:
        if len(args) < (4 if self.multi else 3):
            raise ValueError("connect shape")
        ident = int(args[0]) if self.multi else 0
        args = args[1:] if self.multi else args
        kind, host, port = args[0].upper(), normalize_host(args[1]), int(args[2])
        original_host = self.resolved.get(host, host)
        if not self.joined or not 0 <= ident <= 4 or kind not in {"TCP", "SSL"} or port not in {80, 443} or not self.host_allowed(original_host):
            self.emit("connect_denied", link=ident)
            await self.reply("ERROR")
            return
        if ident in self.links:
            await self.reply("ALREADY CONNECTED", "ERROR")
            return
        options = {}
        if kind == "SSL":
            options = {"ssl": ssl.create_default_context(), "server_hostname": self.sni.get(ident, self.sni.get(None, original_host))}
        self.emit("connecting", link=ident, host=original_host, port=port, transport=kind)
        try:
            reader, writer = await asyncio.wait_for(self.connector(host, port, **options), self.connect_timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            self.emit("connect_error", link=ident, error=type(exc).__name__)
            await self.reply("ERROR")
            return
        if ident in self.links:
            # An incoming diagnostic client may have claimed this slot while
            # the outbound connection was being established.
            writer.close()
            await writer.wait_closed()
            await self.reply("ALREADY CONNECTED", "ERROR")
            return
        link = Link(ident, kind, original_host, port, reader, writer, HttpInspector(self.emit, ident), self.multi)
        self.links[ident] = link
        self.ever_connected = True
        await self.reply(f"{ident},CONNECT" if self.multi else "CONNECT", "OK")
        self.emit("connected", link=ident, host=original_host, port=port, transport=kind)
        link.pump = asyncio.create_task(self._cloud_reader(link))

    async def _payload(self, ident: int, payload: bytes) -> None:
        link = self.links.get(ident)
        if link is None:
            await self.reply("SEND FAIL")
            return
        async with link.send_lock:
            try:
                link.writer.write(payload)
                await link.writer.drain()
            except OSError as exc:
                self.emit("send_error", link=ident, error=type(exc).__name__)
                await self.reply("SEND FAIL")
                await self._close_link(ident)
                return
            # Inspect only after the real socket accepted the bytes. This still
            # does not assert that the server accepted the weather observation.
            if link.server:
                link.last_activity = asyncio.get_running_loop().time()
                link.activity_changed.set()
            else:
                link.inspector.feed(payload)
            await self.reply(f"Recv {len(payload)} bytes", "SEND OK")
            self.emit("sent", link=ident, bytes=len(payload))

    async def _cloud_reader(self, link: Link) -> None:
        forecast = None
        if not link.server and link.host == "display.myacurite.com":
            try:
                from .forecast_inspector import ForecastInspector
            except ImportError:
                from forecast_inspector import ForecastInspector
            forecast = ForecastInspector(self.emit, link.ident)
        response_head = bytearray()
        response_head_done = link.server
        try:
            while data := await link.reader.read(1024):
                if forecast is not None:
                    forecast.feed(data)
                if not response_head_done:
                    response_head.extend(data)
                    end = response_head.find(b"\r\n\r\n")
                    if end >= 0:
                        lines = response_head[:end].decode("latin1").split("\r\n")
                        match = re.fullmatch(r"HTTP/1\.[01] ([0-9]{3})(?: .*)?", lines[0])
                        if match:
                            self.emit("http_response", link=link.ident, host=link.host,
                                      status=int(match[1]))
                        response_head_done = True
                        response_head.clear()
                    elif len(response_head) > 8192:
                        response_head_done = True
                        response_head.clear()
                if link.server:
                    link.last_activity = asyncio.get_running_loop().time()
                    link.activity_changed.set()
                async with link.send_lock:
                    if self.links.get(link.ident) is not link:
                        return
                    header = f"+IPD,{link.ident},{len(data)}" if link.multi else f"+IPD,{len(data)}"
                    if self.ipd_info:
                        peer = link.writer.get_extra_info("peername") or (link.host, link.port)
                        header += f',"{peer[0]}",{peer[1]}'
                    await self.write(b"\r\n" + header.encode("ascii") + b":" + data)
                    self.emit("received", link=link.ident, bytes=len(data))
        except asyncio.CancelledError:
            return
        except (OSError, asyncio.TimeoutError) as exc:
            self.emit("receive_error", link=link.ident, error=type(exc).__name__)
        finally:
            if forecast is not None:
                forecast.eof()
            if self.links.get(link.ident) is link:
                await self._close_link(link.ident)

    async def _close_link(self, ident: int, notify: bool = True) -> None:
        link = self.links.pop(ident, None)
        if link is None:
            return
        if link.pump is not None and link.pump is not asyncio.current_task():
            link.pump.cancel()
            await asyncio.gather(link.pump, return_exceptions=True)
        if link.idle_pump is not None and link.idle_pump is not asyncio.current_task():
            link.idle_pump.cancel()
            await asyncio.gather(link.idle_pump, return_exceptions=True)
        link.writer.close()
        try:
            await asyncio.wait_for(link.writer.wait_closed(), 2)
        except (OSError, asyncio.TimeoutError):
            pass
        if notify and not self.closed:
            await self.reply(f"{ident},CLOSED" if link.multi else "CLOSED")
        self.emit("closed", link=ident)

    async def _close_all(self, notify: bool = True) -> None:
        for ident in list(self.links):
            await self._close_link(ident, notify=notify)

    async def close(self) -> None:
        self.closed = True
        await self._stop_server(notify=False)
        await self._close_all(notify=False)
        self.buffer.clear()
        self.pending = None


def print_event(event: dict) -> None:
    stream = sys.stdout if event["event"] == "weather" else sys.stderr
    print(json.dumps(event, sort_keys=True, separators=(",", ":")), file=stream, flush=True)


async def run(args: argparse.Namespace) -> None:
    publisher = None
    if args.publish_esphome:
        try:
            from .esphome_publisher import ESPHomePublisher
        except ImportError:
            from esphome_publisher import ESPHomePublisher
        publisher = ESPHomePublisher(print_event, allowed_keys=WEATHER_KEYS,
                                     host=args.host, expected_mac=args.station_mac,
                                     noise_psk=os.environ.get(args.api_key_env) or None)
        publisher.start()

    def event(record: dict) -> None:
        quiet = args.quiet_polls and (
            (record.get("event") == "at" and record.get("command") in {"AT+CIPSTATUS", "AT+CWJAP_DEF"})
            or (record.get("event") == "status" and not record.get("links")))
        if not quiet:
            print_event(record)
        if publisher is not None and record.get("event") == "weather":
            publisher.submit(record)

    try:
        await run_bridge(args, event)
    finally:
        if publisher is not None:
            await publisher.close()


async def run_bridge(args: argparse.Namespace, event: Callable[[dict], None]) -> None:
    stop_at = asyncio.get_running_loop().time() + args.duration if args.duration else None
    while stop_at is None or asyncio.get_running_loop().time() < stop_at:
        modem = None
        writer = None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(args.host, args.port), 10)
            # A receiver power cycle or Wi-Fi outage may lose an idle TCP
            # connection without delivering FIN. Detect that on the host even
            # when neither side has UART bytes to send.
            bridge_socket = writer.get_extra_info("socket")
            if bridge_socket is not None:
                bridge_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                for option, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 3)):
                    if hasattr(socket, option):
                        bridge_socket.setsockopt(socket.IPPROTO_TCP, getattr(socket, option), value)
            async def send(data: bytes) -> None:
                writer.write(data)
                await writer.drain()
            modem = Modem(send, event, ssid=args.ssid, station_ip=args.station_ip,
                          station_mac=args.station_mac, ap_bssid=args.ap_bssid,
                          ap_channel=args.ap_channel, ap_rssi=args.ap_rssi,
                          allow_hosts=tuple(args.allow_host),
                          initial_status=args.initial_status, ack_closed_links=args.ack_closed_links)
            print_event({"event": "bridge_connected", "time": utc_now(), "port": args.port,
                         "initial_status": args.initial_status})
            if args.announce_ready:
                await modem.reply("ready", "WIFI CONNECTED", "WIFI GOT IP")
                modem.emit("boot_announced")
            while True:
                remaining = None if stop_at is None else max(0, stop_at - asyncio.get_running_loop().time())
                data = await asyncio.wait_for(reader.read(4096), remaining)
                if not data:
                    break
                await modem.feed(data)
        except (OSError, asyncio.TimeoutError) as exc:
            print_event({"event": "bridge_unavailable", "time": utc_now(), "error": type(exc).__name__})
        finally:
            if modem:
                await modem.close()
            if writer:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2)
                except (OSError, asyncio.TimeoutError):
                    pass
        if stop_at is not None and asyncio.get_running_loop().time() >= stop_at:
            return
        await asyncio.sleep(min(args.reconnect_delay, max(0, stop_at - asyncio.get_running_loop().time())) if stop_at else args.reconnect_delay)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.0.2.10", help="raw UART bridge address")
    parser.add_argument("--port", type=int, default=6638)
    parser.add_argument("--api-key-env", default="ACURITE_API_KEY", help="environment variable containing optional API encryption key")
    parser.add_argument("--ssid", default="weather-network", help="simulated connected network; no password needed")
    parser.add_argument("--station-ip", default="192.0.2.10")
    parser.add_argument("--station-mac", default="02:00:00:00:00:10")
    parser.add_argument("--ap-bssid", default="02:00:00:00:00:01", help="reported AP BSSID; default is simulated")
    parser.add_argument("--ap-channel", type=int, default=6, help="reported AP channel, 1–14")
    parser.add_argument("--ap-rssi", type=int, default=-45, help="reported AP RSSI in dBm, -127–0")
    parser.add_argument("--allow-host", action="append", default=[], help="additional exact outbound host (ports remain 80/443)")
    parser.add_argument("--duration", type=float, default=0, help="stop after seconds; 0 runs until interrupted")
    parser.add_argument("--initial-status", type=int, choices=(2, 4), default=2,
                        help="idle modem state: 2 newly associated, 4 previous socket closed (factory capture)")
    parser.add_argument("--announce-ready", action="store_true",
                        help="emit standard ESP ready/association notifications after bridge attachment")
    parser.add_argument("--ack-closed-links", action="store_true",
                        help="compatibility experiment: acknowledge already-closed links with CLOSED and OK")
    parser.add_argument("--publish-esphome", action="store_true",
                        help="asynchronously publish actual uploads through the receiver's native API")
    parser.add_argument("--quiet-polls", action="store_true",
                        help="omit routine idle status and association polls from logs")
    parser.add_argument("--reconnect-delay", type=float, default=3)
    args = parser.parse_args()
    if args.duration < 0 or args.reconnect_delay <= 0 or not 1 <= args.port <= 65535:
        parser.error("invalid duration, reconnect delay, or port")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Avoid exception strings/tracebacks: command/payload arguments can be
        # sensitive even when a malformed input caused the exception.
        print_event({"event": "fatal", "time": utc_now(), "error": type(exc).__name__})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
