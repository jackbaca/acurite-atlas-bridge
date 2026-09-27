"""Protocol tests: loopback cloud/server sockets, no receiver or external services."""

import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from acurite_bridge.bridge.modem_host import HttpInspector, Modem


class ModemTests(unittest.IsolatedAsyncioTestCase):
    async def test_forecast_inspection_preserves_cloud_reply(self):
        body = b'{"current":{"temperature":{"high":91,"low":68},"icon":3},"location":"private"}'
        self.response = b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
        await self.command('ATE0')
        await self.command('AT+CIPSTART=3,"SSL","display.myacurite.com",443')
        request = b'GET / HTTP/1.1\r\nHost: display.myacurite.com\r\n\r\n'
        await self.command(f'AT+CIPSEND=3,{len(request)}')
        await self.modem.feed(request)
        await self.wait_closed()
        forecasts = [e for e in self.events if e['event'] == 'forecast']
        self.assertEqual(len(forecasts), 1)
        self.assertEqual(forecasts[0]['values']['current_temperature_high'], 91)
        self.assertIn(body, bytes(self.output))
        self.assertNotIn('private', json.dumps(self.events))

    async def test_battery_capture_preserves_only_known_status_words(self):
        events = []
        inspector = HttpInspector(lambda event, **fields: events.append({'event': event, **fields}), 3)
        inspector.feed(b'GET /?tempf=89&sensorbattery=Normal&hubbattery=private HTTP/1.1\r\n\r\n')
        weather = next(e for e in events if e['event'] == 'weather')
        self.assertEqual(weather['texts'], {'sensorbattery': 'normal'})
        self.assertNotIn('private', json.dumps(events))

    async def asyncSetUp(self):
        self.output = bytearray()
        self.events = []
        self.cloud_requests = []
        self.connections = []
        self.cloud_writers = []
        self.cloud_tasks = set()
        self.response = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n\xff\x00OK"
        async def cloud(reader, writer):
            task = asyncio.current_task()
            self.cloud_tasks.add(task)
            self.cloud_writers.append(writer)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                self.cloud_requests.append(head)
                writer.write(self.response[:17])
                await writer.drain()
                await asyncio.sleep(0)
                writer.write(self.response[17:])
                await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()
                self.cloud_tasks.discard(task)
        self.server = await asyncio.start_server(cloud, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        async def connect(host, port, **kwargs):
            self.connections.append((host, port, kwargs))
            return await asyncio.open_connection("127.0.0.1", self.port)
        async def send(data):
            self.output.extend(data)
        self.modem = Modem(send, self.events.append, connector=connect)

    async def asyncTearDown(self):
        await self.modem.close()
        self.server.close()
        await self.server.wait_closed()
        for writer in self.cloud_writers:
            writer.close()
        for task in list(self.cloud_tasks):
            task.cancel()
        await asyncio.gather(*self.cloud_tasks, return_exceptions=True)

    async def command(self, command):
        self.output.clear()
        await self.modem.feed(command.encode() + b"\r\n")
        return bytes(self.output)

    async def wait_closed(self):
        for _ in range(100):
            if not self.modem.links:
                return
            await asyncio.sleep(0.005)
        self.fail("cloud link did not close")

    async def wait_output(self, fragment):
        for _ in range(100):
            if fragment in self.output:
                return
            await asyncio.sleep(0.005)
        self.fail(f"missing modem output {fragment!r}")

    async def test_reported_ap_metadata_and_validation(self):
        self.modem = Modem(self.modem.send, self.events.append,
                           ap_bssid="d0:21:f9:45:cb:61", ap_channel=11, ap_rssi=-68)
        await self.command("ATE0")
        self.assertIn(b'"d0:21:f9:45:cb:61",11,-68', await self.command("AT+CWJAP?"))
        self.assertIn(b'-68,"d0:21:f9:45:cb:61",11)', await self.command("AT+CWLAP"))
        for option in ({"ap_bssid": "bad\r\n"}, {"ap_channel": 0}, {"ap_channel": 15},
                       {"ap_rssi": -128}, {"ap_rssi": 1}):
            with self.assertRaises(ValueError):
                Modem(self.modem.send, self.events.append, **option)

    async def test_loopback_server_request_response_status_and_disable(self):
        await self.command("ATE0")
        # Leave the first slot occupied by an outbound socket. The accepted
        # client must get a free ID without replacing that connection.
        await self.command('AT+CIPSTART=0,"TCP","atlasapi.myacurite.com",80')
        self.assertEqual(await self.command("AT+CIPSERVER=1,80"), b"\r\nOK\r\n")
        listener = self.modem.server
        self.assertEqual(await self.command("AT+CIPSERVER=1,80"), b"\r\nno change\r\nOK\r\n")
        self.assertIs(self.modem.server, listener)
        address, port = listener.sockets[0].getsockname()
        self.assertEqual(address, "127.0.0.1")
        self.assertNotEqual(port, 80)
        listening = next(e for e in self.events if e["event"] == "server_listening")
        self.assertEqual((listening["requested_port"], listening["loopback_port"]), (80, port))
        reader, writer = await asyncio.open_connection(address, port)
        self.addAsyncCleanup(writer.wait_closed)
        self.addCleanup(writer.close)
        await self.wait_output(b"1,CONNECT")
        request = b"GET /private-route?password=private&tempf=99 HTTP/1.1\r\nHost: local\r\n\r\n"
        self.output.clear()
        writer.write(request)
        await writer.drain()
        await self.wait_output(request)
        self.assertEqual(bytes(self.output), b"\r\n+IPD,1," + str(len(request)).encode() + b":" + request)
        status = await self.command("AT+CIPSTATUS")
        self.assertIn(f'+CIPSTATUS:1,"TCP","127.0.0.1",{writer.get_extra_info("sockname")[1]},80,1'.encode(), status)
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nprivate\x00\xff"
        self.output.clear()
        await self.modem.feed(f"AT+CIPSEND=1,{len(response)}\r\n".encode() + response)
        self.assertIn(b"SEND OK", self.output)
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(response)), 1), response)
        self.assertFalse(any(e["event"] in {"weather", "http_request", "inspection_skip"} for e in self.events))
        self.assertNotIn("private", json.dumps(self.events))
        self.assertIn(b"1,CLOSED", await self.command("AT+CIPSERVER=0"))
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
        self.assertIsNone(self.modem.server)
        self.assertIn(0, self.modem.links)  # Listener deletion preserves cloud sockets.
        self.assertFalse(listener.is_serving())
        with self.assertRaises(OSError):
            await asyncio.open_connection(address, port)

    async def test_server_constraints_peer_close_and_modem_cleanup(self):
        await self.command("ATE0")
        await self.command("AT+CIPMUX=0")
        self.assertIn(b"ERROR", await self.command("AT+CIPSERVER=1,80"))
        await self.command("AT+CIPMUX=1")
        self.assertIn(b"ERROR", await self.command("AT+CIPSERVER?"))
        self.assertIn(b"ERROR", await self.command("AT+CIPSERVER=1,0"))
        self.assertIn(b"ERROR", await self.command('AT+CIPSERVER=1,80,"SSL"'))
        self.assertEqual(await self.command("AT+CIPSERVER=1"), b"\r\nOK\r\n")
        self.assertEqual(self.modem.server_port, 333)
        listener = self.modem.server
        address, port = listener.sockets[0].getsockname()
        self.assertIn(b"ERROR", await self.command("AT+CIPMUX=0"))
        self.assertIn(b"ERROR", await self.command("AT+CIPSERVER=1,81"))
        reader, writer = await asyncio.open_connection(address, port)
        await self.wait_output(b"0,CONNECT")
        self.output.clear()
        writer.close()
        await writer.wait_closed()
        await self.wait_output(b"0,CLOSED")
        self.assertNotIn(0, self.modem.links)
        reader, writer = await asyncio.open_connection(address, port)
        self.addAsyncCleanup(writer.wait_closed)
        self.addCleanup(writer.close)
        await self.wait_output(b"0,CONNECT")
        await self.modem.close()
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
        self.assertFalse(self.modem.links)
        self.assertFalse(listener.is_serving())

    async def test_server_timeout_query_update_and_expiry(self):
        await self.command("ATE0")
        self.assertEqual(await self.command("AT+CIPSTO=0"), b"\r\nOK\r\n")
        self.assertEqual(await self.command("AT+CIPSTO?"), b"\r\n+CIPSTO:0\r\nOK\r\n")
        await self.command("AT+CIPSERVER=1,80")
        address, port = self.modem.server.sockets[0].getsockname()
        reader, writer = await asyncio.open_connection(address, port)
        self.addAsyncCleanup(writer.wait_closed)
        self.addCleanup(writer.close)
        await self.wait_output(b"0,CONNECT")
        # Changing timeout wakes an existing client's idle watcher. Aging its
        # activity avoids a wall-clock sleep while testing actual disconnection.
        self.modem.links[0].last_activity -= 2
        self.assertEqual(await self.command("AT+CIPSTO=1"), b"\r\nOK\r\n")
        await self.wait_output(b"0,CLOSED")
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
        self.assertTrue(any(e["event"] == "server_timeout" for e in self.events))
        self.assertIn(b"ERROR", await self.command("AT+CIPSTO=7201"))
        self.assertIn(b"ERROR", await self.command("AT+CIPSTO=-1"))
        self.assertEqual(self.modem.server_timeout, 1)

    async def test_station_disconnect_preserves_server_and_accepted_client(self):
        await self.command("ATE0")
        await self.command('AT+CIPSTART=3,"TCP","atlasapi.myacurite.com",80')
        await self.command("AT+CIPSERVER=1,80")
        listener = self.modem.server
        address, port = listener.sockets[0].getsockname()
        reader, writer = await asyncio.open_connection(address, port)
        self.addAsyncCleanup(writer.wait_closed)
        self.addCleanup(writer.close)
        await self.wait_output(b"0,CONNECT")
        result = await self.command("AT+CWQAP")
        self.assertIn(b"3,CLOSED", result)
        self.assertNotIn(b"0,CLOSED", result)
        self.assertFalse(self.modem.joined)
        self.assertEqual(set(self.modem.links), {0})
        self.assertTrue(listener.is_serving())
        self.assertIn(b"STATUS:3", await self.command("AT+CIPSTATUS"))
        self.output.clear()
        writer.write(b"still-connected")
        await writer.drain()
        await self.wait_output(b"+IPD,0,15:still-connected")
        await self.command("AT+CIPCLOSE=0")
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
        self.assertIn(b"STATUS:5", await self.command("AT+CIPSTATUS"))
        self.assertTrue(listener.is_serving())

    async def test_echo_scan_join_and_status_no_secret_log(self):
        self.assertEqual(await self.command("AT"), b"AT\r\n\r\nOK\r\n")
        self.assertIn(b"STATUS:2", await self.command("AT+CIPSTATUS"))
        await self.command("ATE0")
        scan = await self.command("AT+CWLAP")
        self.assertIn(b'+CWLAP:(3,"weather-network",-45,"02:00:00:00:00:01",6)', scan)
        await self.command("AT+CWQAP")
        self.assertIn(b"STATUS:5", await self.command("AT+CIPSTATUS"))
        joined = await self.command('AT+CWJAP_DEF="weather-network","sensitive-password"')
        self.assertIn(b"WIFI GOT IP", joined)
        self.assertNotIn(b"sensitive-password", joined)
        self.assertNotIn("sensitive-password", json.dumps(self.events))
        self.assertIn(b'+CWJAP_DEF:"weather-network"', await self.command("AT+CWJAP_DEF?"))
        self.assertIn(b"+CWMODE_DEF:1", await self.command("AT+CWMODE_DEF?"))

    async def test_receiver_clock_query_uses_documented_asctime_framing(self):
        await self.command("ATE0")
        with patch("acurite_bridge.bridge.modem_host.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 7, 4, 5, 6, tzinfo=timezone.utc)
            result = await self.command("AT+CIPSNTPTIME?")
        self.assertEqual(result, b"\r\n+CIPSNTPTIME:Mon Sep  7 04:05:06 2026\r\n\r\nOK\r\n")
        self.assertEqual(self.events[-1]["event"], "clock")

    async def test_factory_absent_multilink_close_response(self):
        await self.command("ATE0")
        self.assertEqual(await self.command("AT+CIPCLOSE=3"), b"\r\nlink is not\r\n")
        self.assertEqual(self.events[-1]["link"], 3)
        self.modem.ack_closed_links = True
        self.assertEqual(await self.command("AT+CIPCLOSE=3"), b"\r\n3,CLOSED\r\nOK\r\n")
        self.assertFalse(self.modem.links)
        await self.command("AT+CIPMUX=0")
        self.assertEqual(await self.command("AT+CIPCLOSE"), b"\r\nERROR\r\n")

    async def test_sntp_configuration_changes_clock_date_and_rejects_invalid_offset(self):
        await self.command("ATE0")
        self.assertEqual(await self.command('AT+CIPSNTPCFG=1,-7,"pool.ntp.org"'), b"\r\nOK\r\n")
        with patch("acurite_bridge.bridge.modem_host.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 27, 0, 47, 0, tzinfo=timezone.utc)
            result = await self.command("AT+CIPSNTPTIME?")
        self.assertEqual(result, b"\r\n+CIPSNTPTIME:Sat Sep 26 17:47:00 2026\r\n\r\nOK\r\n")
        self.assertIn(b'+CIPSNTPCFG:1,-7,"pool.ntp.org"', await self.command("AT+CIPSNTPCFG?"))
        self.assertIn(b"ERROR", await self.command('AT+CIPSNTPCFG=1,99,"pool.ntp.org"'))
        self.assertEqual(self.modem.sntp_timezone, -7)

    async def test_setup_ap_configuration_is_volatile_and_not_logged(self):
        await self.command("ATE0")
        self.assertEqual(await self.command('AT+CWSAP_DEF="private-ap","private-password",6,3'), b"\r\nOK\r\n")
        self.assertIn(b'+CWSAP_DEF:"private-ap","private-password",6,3,4,0', await self.command("AT+CWSAP_DEF?"))
        self.assertNotIn("private-", json.dumps(self.events))
        self.assertIn(b"ERROR", await self.command('AT+CWSAP_DEF="private-ap","private-password",99,3'))
        self.assertEqual(self.modem.ap_config[2], "6")

    async def test_fragmented_payload_exact_ipd_bytes_and_link_identity(self):
        await self.command("ATE0")
        connected = await self.command('AT+CIPSTART=3,"TCP","atlasapi.myacurite.com",80')
        self.assertIn(b"3,CONNECT", connected)
        status = await self.command("AT+CIPSTATUS")
        self.assertIn(b"STATUS:3", status)
        self.assertIn(b'+CIPSTATUS:3,"TCP"', status)
        payload = b"POST /weatherstation/updateweatherstation?tempf=94&baromin=29.92&humidity=42&id=secret-id HTTP/1.1\r\nHost: atlasapi.myacurite.com\r\nX-Test: AT+CIPSTART=0\r\n\r\n"
        prefix = f"AT+CIPSEND=3,{len(payload)}\r\n".encode()
        self.output.clear()
        # Fragment command and body; AT-looking payload text must not be parsed.
        await self.modem.feed(prefix[:9])
        await self.modem.feed(prefix[9:] + payload[:23])
        self.assertEqual(bytes(self.output), b"\r\nOK\r\n>")
        await self.modem.feed(payload[23:-1])
        self.assertEqual(self.cloud_requests, [])
        # A following command coalesced into the same bridge packet must stay
        # outside the exact byte-counted cloud payload.
        await self.modem.feed(payload[-1:] + b"AT+CIPSTATUS\r\n")
        await self.wait_closed()
        self.assertEqual(self.cloud_requests, [payload])
        raw = bytes(self.output)
        self.assertLess(raw.index(b"SEND OK"), raw.index(b"+IPD"))
        reassembled = bytearray()
        cursor = 0
        while True:
            start = raw.find(b"+IPD,3,", cursor)
            if start < 0:
                break
            colon = raw.index(b":", start)
            size = int(raw[start + len(b"+IPD,3,"):colon])
            reassembled.extend(raw[colon + 1:colon + 1 + size])
            cursor = colon + 1 + size
        self.assertEqual(bytes(reassembled), self.response)
        self.assertIn(b"3,CLOSED", raw)
        self.assertIn(b"STATUS:4", await self.command("AT+CIPSTATUS"))
        weather = [e for e in self.events if e["event"] == "weather"]
        self.assertEqual(weather[0]["values"], {"tempf": 94.0, "baromin": 29.92, "humidity": 42.0})
        self.assertNotIn("secret-id", json.dumps(self.events))

    async def test_ssl_sni_and_denied_destination(self):
        await self.command("ATE0")
        await self.command('AT+SNISERVER="atlasapi.myacurite.com"')
        await self.command('AT+CIPSTART=3,"SSL","atlasapi.myacurite.com",443')
        host, port, options = self.connections[-1]
        self.assertEqual((host, port), ("atlasapi.myacurite.com", 443))
        self.assertEqual(options["server_hostname"], host)
        self.assertTrue(options["ssl"].check_hostname)
        count = len(self.connections)
        self.assertIn(b"ERROR", await self.command('AT+CIPSTART=2,"TCP","example.com",80'))
        self.assertIn(b"ERROR", await self.command('AT+CIPSTART=2,"TCP","atlasapi.myacurite.com",22'))
        self.assertIn(b"ERROR", await self.command('AT+CIPSTART=2,"TCP","myacurite.com.example.com",80'))
        self.assertEqual(len(self.connections), count)

    async def test_bad_commands_do_not_desynchronize_following_command(self):
        await self.command("ATE0")
        await self.modem.feed(b"X" * 3000)
        await self.modem.feed(b"\r\nAT\r\n")
        self.assertTrue(bytes(self.output).endswith(b"\r\nERROR\r\n\r\nOK\r\n"))
        self.assertIn(b"ERROR", await self.command("AT+CIPSEND=3,20"))
        self.assertIn(b"ERROR", await self.command("AT+UNKNOWN=private-value"))
        self.assertNotIn("private-value", json.dumps(self.events))

    async def test_single_link_and_multiple_sends_preserve_http_inspection(self):
        await self.command("ATE0")
        await self.command("AT+CIPMUX=0")
        self.assertIn(b"CONNECT", await self.command('AT+CIPSTART="TCP","atlasapi.myacurite.com",80'))
        payload = b"GET /weatherstation/updateweatherstation?tempf=71 HTTP/1.1\r\nHost: atlasapi.myacurite.com\r\n\r\n"
        split = 45
        for part in (payload[:split], payload[split:]):
            await self.modem.feed(f"AT+CIPSEND={len(part)}\r\n".encode() + part)
        await self.wait_closed()
        self.assertEqual(self.cloud_requests, [payload])
        self.assertIn(b"+IPD,", bytes(self.output))
        self.assertNotIn(b"+IPD,0,", bytes(self.output))
        self.assertEqual(sum(e["event"] == "weather" for e in self.events), 1)


class InspectorTests(unittest.TestCase):
    def test_form_body_and_privacy_under_fragmentation(self):
        events = []
        inspector = HttpInspector(lambda kind, **data: events.append({"event": kind, **data}), 3)
        body = b"humidity=60&tempf=nan&baromin=inf&password=private&indoortempf=73.2"
        request = b"POST /secret-route-token HTTP/1.1\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        for byte in request:
            inspector.feed(bytes([byte]))
        self.assertEqual(events[-1]["values"], {"humidity": 60.0, "indoortempf": 73.2})
        serialized = json.dumps(events)
        self.assertNotIn("private", serialized)
        self.assertNotIn("secret-route-token", serialized)
        self.assertEqual(events[0]["path"], "<other>")


if __name__ == "__main__":
    unittest.main()
