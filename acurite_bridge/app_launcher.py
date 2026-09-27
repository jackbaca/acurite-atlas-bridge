"""Home Assistant app entry point. No HA token or USB access is required."""
from __future__ import annotations

import asyncio
import base64
import inspect
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import sys


class SetupError(Exception):
    pass


def validate_options(options: dict) -> dict:
    if not isinstance(options, dict):
        raise SetupError("App configuration must be an object.")
    host = options.get("device_host", "")
    if not isinstance(host, str):
        raise SetupError("Set device_host to the receiver's IP address or hostname.")
    host = host.strip()
    if not host or host.startswith("-") or len(host) > 253 or not re.fullmatch(r"[a-zA-Z0-9.:-]+", host):
        raise SetupError("Set device_host to the receiver's IP address or hostname.")
    ssid = options.get("wifi_ssid", "")
    if not isinstance(ssid, str) or len(ssid.encode()) > 32 or any(c in ssid for c in "\r\n\0"):
        raise SetupError("wifi_ssid must be the receiver's Wi-Fi network name.")
    key = options.get("api_encryption_key", "")
    try:
        if not isinstance(key, str) or (key and len(base64.b64decode(key, validate=True)) != 32):
            raise ValueError()
    except ValueError:
        raise SetupError("API encryption key must be a base64-encoded 32-byte ESPHome key.") from None
    return {"device_host": host, "wifi_ssid": ssid, "api_encryption_key": key}


async def discover(options: dict, *, client_factory=None, resolver=None) -> dict:
    if client_factory is None:
        from aioesphomeapi import APIClient
        client_factory = APIClient
    host = options["device_host"]
    resolver = resolver or (lambda h: socket.getaddrinfo(h, 6638, socket.AF_INET, socket.SOCK_STREAM))
    addresses = await asyncio.to_thread(resolver, host)
    if not addresses:
        raise SetupError("Receiver hostname did not resolve. Try its IPv4 address.")
    address = addresses[0][4][0]
    if ipaddress.ip_address(address).version != 4:
        raise SetupError("The receiver bridge requires an IPv4 address.")
    client = client_factory(address, 6053, password=None,
                            noise_psk=options["api_encryption_key"] or None,
                            client_info="AcuRite Atlas Bridge setup")
    try:
        await asyncio.wait_for(client.connect(login=True, log_errors=False), 15)
        info = await asyncio.wait_for(client.device_info(), 10)
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", info.mac_address):
            raise SetupError("Receiver returned an invalid device identity.")
        entities, services = await asyncio.wait_for(client.list_entities_services(), 10)
        if not any(s.name == "publish_upload" for s in services):
            raise SetupError("Install the AcuRite Atlas Bridge ESPHome firmware first.")
        wanted = {e.key: e.name.casefold() for e in entities
                  if e.name.casefold() in {"wifi ssid", "wifi bssid", "wifi channel", "wifi rssi"}}
        values = {}
        ready = asyncio.Event()

        def state(value):
            name = wanted.get(value.key)
            if name and not getattr(value, "missing_state", False):
                values[name] = value.state
            if all(n in values for n in wanted.values()):
                ready.set()

        result = client.subscribe_states(state)
        if inspect.isawaitable(result):
            await result
        if wanted:
            try:
                await asyncio.wait_for(ready.wait(), 6)
            except asyncio.TimeoutError:
                pass
        ssid = options["wifi_ssid"] or values.get("wifi ssid")
        if not isinstance(ssid, str) or not ssid or len(ssid.encode()) > 32 or any(c in ssid for c in "\r\n\0"):
            raise SetupError("Enter wifi_ssid in app configuration; older firmware does not report it.")
        bssid = values.get("wifi bssid", "02:00:00:00:00:01")
        if not isinstance(bssid, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", bssid):
            bssid = "02:00:00:00:00:01"
        def numeric(name, low, high, default):
            value = values.get(name)
            return int(value) if isinstance(value, (int, float)) and math.isfinite(value) and low <= value <= high else default
        return {"host": address, "mac": info.mac_address, "ssid": ssid, "bssid": bssid,
                "channel": numeric("wifi channel", 1, 14, 6),
                "rssi": numeric("wifi rssi", -127, 0, -45)}
    finally:
        try:
            await asyncio.wait_for(client.disconnect(force=True), 5)
        except Exception:
            pass


async def prepare(options):
    while True:
        try:
            result = await discover(options)
            print("Receiver verified. Starting modem bridge and ESPHome publisher; allow up to five minutes for weather.", flush=True)
            return result
        except SetupError:
            raise
        except Exception as error:
            # Exception messages can include a host/key. Print only the class.
            print(f"Receiver unavailable ({type(error).__name__}); retrying in 10 seconds.", flush=True)
            await asyncio.sleep(10)


def main():
    try:
        options = validate_options(json.loads(Path("/data/options.json").read_text()))
        device = asyncio.run(prepare(options))
    except (SetupError, OSError, json.JSONDecodeError) as error:
        print(str(error) if isinstance(error, SetupError) else "Could not read app configuration.", file=sys.stderr)
        raise SystemExit(1) from None
    os.environ["ACURITE_API_KEY"] = options["api_encryption_key"]
    command = [sys.executable, str(Path(__file__).parent / "bridge/modem_host.py"),
               "--host", device["host"], "--station-ip", device["host"],
               "--station-mac", device["mac"], "--ssid", device["ssid"],
               "--ap-bssid", device["bssid"], "--ap-channel", str(device["channel"]),
               "--ap-rssi", str(device["rssi"]), "--initial-status", "4",
               "--ack-closed-links", "--announce-ready", "--publish-esphome", "--quiet-polls"]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
