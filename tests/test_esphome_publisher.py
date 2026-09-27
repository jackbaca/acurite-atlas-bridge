"""Publisher lifecycle tests with fake API clients; no network or devices."""

import asyncio
from datetime import datetime, timedelta, timezone
from enum import IntEnum
import json
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs

from acurite_bridge.bridge.esphome_publisher import ESPHomePublisher


class ArgType(IntEnum):
    STRING = 3


class FakeClient:
    def __init__(self, *, connect_gate=None, connect_error=None, send_error=None,
                 mac="02:00:00:00:00:10", service_name="publish_upload"):
        self.connect_gate = connect_gate
        self.connect_error = connect_error
        self.send_error = send_error
        self.mac = mac
        self.service = SimpleNamespace(name=service_name, args=[SimpleNamespace(name="query", type=ArgType.STRING)])
        self.connect_calls = []
        self.sent = []
        self.disconnect_calls = []
        self.on_stop = None
        self.started = asyncio.Event()

    async def connect(self, *, on_stop, login, log_errors):
        self.connect_calls.append((login, log_errors))
        self.on_stop = on_stop
        self.started.set()
        if self.connect_gate is not None:
            await self.connect_gate.wait()
        if self.connect_error:
            raise self.connect_error

    async def device_info(self):
        return SimpleNamespace(mac_address=self.mac)

    async def list_entities_services(self):
        return [], [self.service]

    async def execute_service(self, service, data):
        if service is not self.service:
            raise AssertionError("service must be rediscovered after reconnect")
        if self.send_error:
            raise self.send_error
        self.sent.append(data.copy())

    async def disconnect(self, *, force):
        self.disconnect_calls.append(force)
        if self.on_stop:
            await self.on_stop(True)

    async def lose_connection(self, expected=False):
        await self.on_stop(expected)


class FakeFactory:
    def __init__(self, *clients):
        self.clients = list(clients)
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.clients[len(self.calls) - 1]


class RetryGate:
    def __init__(self):
        self.calls = []
        self.release = asyncio.Event()

    async def __call__(self, seconds):
        self.calls.append(seconds)
        await self.release.wait()


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_battery_strings_use_explicit_status_allowlist(self):
        client = FakeClient()
        publisher = ESPHomePublisher(self.events.append,
            allowed_keys={'tempf', 'sensorbattery', 'hubbattery'},
            client_factory=FakeFactory(client), clock=lambda: self.now)
        self.publishers.append(publisher)
        publisher.submit(self.weather(texts={'sensorbattery': 'normal', 'hubbattery': 'private'}))
        await self.until(lambda: len(client.sent) == 1)
        values = parse_qs(client.sent[0]['query'])
        self.assertEqual(values['sensorbattery'], ['normal'])
        self.assertNotIn('hubbattery', values)
        self.assertNotIn('private', json.dumps(self.events))

    async def asyncSetUp(self):
        self.now = datetime(2026, 9, 27, 1, 0, 46, tzinfo=timezone.utc)
        self.events = []
        self.publishers = []

    async def asyncTearDown(self):
        for publisher in self.publishers:
            await publisher.close()

    def publisher(self, factory, **kwargs):
        publisher = ESPHomePublisher(self.events.append, allowed_keys={"tempf", "baromin", "humidity"},
                                     client_factory=factory, clock=lambda: self.now, **kwargs)
        self.publishers.append(publisher)
        return publisher

    def weather(self, value=90.8, **kwargs):
        return {"event": "weather", "source": "uart_upload", "time": self.now.isoformat(),
                "values": {"tempf": value}, **kwargs}

    async def until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0)
        await asyncio.wait_for(wait(), 1)

    async def test_connecting_never_blocks_submit_latest_wins_and_data_is_sanitized(self):
        gate = asyncio.Event()
        client = FakeClient(connect_gate=gate)
        factory = FakeFactory(client)
        publisher = self.publisher(factory)
        self.assertTrue(publisher.submit(self.weather(80)))
        await client.started.wait()
        latest = self.weather(values={"tempf": 90.8, "humidity": 33, "baromin": float("nan"),
                                      "password": "private", "arbitrary": 44})
        latest["time"] = "2026-09-26T18:00:45-07:00"
        self.assertTrue(publisher.submit(latest))
        latest["values"]["tempf"] = 999  # Queue owns its sanitized snapshot.
        self.assertFalse(client.sent)
        gate.set()
        await self.until(lambda: len(client.sent) == 1)
        values = parse_qs(client.sent[0]["query"])
        self.assertEqual(values, {"humidity": ["33"], "tempf": ["90.8"],
                                  "dateutc": ["2026-09-27 01:00:45"]})
        self.assertEqual(client.connect_calls, [(True, False)])
        self.assertEqual(factory.calls[0][0], ("192.0.2.10", 6053))
        self.assertEqual(factory.calls[0][1]["expected_mac"], "020000000010")
        self.assertIsNone(factory.calls[0][1]["password"])
        self.assertEqual(len(factory.calls), 1)
        self.assertNotIn("private", json.dumps(self.events))
        for record in self.events:
            self.assertLessEqual(set(record), {"event", "time", "count", "error"})

    async def test_failed_send_retries_latest_with_rediscovered_service(self):
        first = FakeClient(send_error=ConnectionError("private query must never be logged"))
        second = FakeClient()
        factory = FakeFactory(first, second)
        retry = RetryGate()
        publisher = self.publisher(factory, sleep=retry)
        publisher.submit(self.weather(80))
        await self.until(lambda: retry.calls)
        publisher.submit(self.weather(81))
        publisher.submit(self.weather(82))
        retry.release.set()
        await self.until(lambda: len(second.sent) == 1)
        self.assertEqual(parse_qs(second.sent[0]["query"])["tempf"], ["82"])
        self.assertEqual(first.disconnect_calls, [True])
        self.assertEqual(retry.calls, [1])
        self.assertNotIn("private", json.dumps(self.events))
        self.assertTrue(any(e.get("error") == "ConnectionError" for e in self.events))

    async def test_disconnect_reconnects_even_when_expected_and_idle(self):
        first, second = FakeClient(), FakeClient()
        retry = RetryGate()
        factory = FakeFactory(first, second)
        publisher = self.publisher(factory, sleep=retry)
        publisher.submit(self.weather())
        await self.until(lambda: len(first.sent) == 1)
        await first.lose_connection(expected=True)
        await self.until(lambda: retry.calls)
        retry.release.set()
        await self.until(lambda: len(second.connect_calls) == 1)
        publisher.submit(self.weather(91))
        await self.until(lambda: len(second.sent) == 1)
        self.assertEqual(parse_qs(second.sent[0]["query"])["tempf"], ["91"])
        self.assertEqual(len(first.sent), 1)
        await publisher.close()
        self.assertEqual(second.disconnect_calls, [True])
        self.assertFalse(publisher.submit(self.weather(92)))
        self.assertEqual(len(factory.calls), 2)

    async def test_stale_at_submit_and_after_connect_is_dropped(self):
        gate = asyncio.Event()
        client = FakeClient(connect_gate=gate)
        factory = FakeFactory(client)
        publisher = self.publisher(factory)
        old = self.weather(time=(self.now - timedelta(seconds=301)).isoformat())
        self.assertFalse(publisher.submit(old))
        self.assertFalse(factory.calls)
        publisher.submit(self.weather())
        await client.started.wait()
        self.now += timedelta(seconds=301)
        gate.set()
        await self.until(lambda: sum(e["event"] == "publisher_stale" for e in self.events) == 2)
        self.assertFalse(client.sent)

    async def test_stale_failed_send_is_not_replayed_after_retry(self):
        first, second = FakeClient(send_error=ConnectionError()), FakeClient()
        retry = RetryGate()
        publisher = self.publisher(FakeFactory(first, second), sleep=retry)
        publisher.submit(self.weather())
        await self.until(lambda: retry.calls)
        self.now += timedelta(seconds=301)
        retry.release.set()
        await self.until(lambda: len(second.connect_calls) == 1)
        await self.until(lambda: any(e["event"] == "publisher_stale" for e in self.events))
        self.assertFalse(second.sent)

    async def test_identity_and_service_validation_prevent_execution(self):
        for bad_client, error in ((FakeClient(mac="00:00:00:00:00:01"), "IdentityMismatch"),
                                  (FakeClient(service_name="set_forecast"), "PublishServiceMissing")):
            retry = RetryGate()
            publisher = self.publisher(FakeFactory(bad_client), sleep=retry)
            publisher.submit(self.weather())
            await self.until(lambda: retry.calls)
            self.assertFalse(bad_client.sent)
            self.assertTrue(any(e.get("error") == error for e in self.events))
            await publisher.close()

    async def test_retry_backoff_is_bounded_and_close_cancels_pending_connection(self):
        calls = []
        async def fast_sleep(seconds):
            calls.append(seconds)
            await asyncio.sleep(0)
        gate = asyncio.Event()
        good = FakeClient(connect_gate=gate)
        clients = [FakeClient(connect_error=OSError("private")) for _ in range(4)] + [good]
        factory = FakeFactory(*clients)
        publisher = self.publisher(factory, sleep=fast_sleep)
        publisher.submit(self.weather())
        await good.started.wait()
        self.assertEqual(calls, [1, 2, 4, 5])
        await asyncio.wait_for(publisher.close(), 1)
        self.assertEqual(good.disconnect_calls, [True])
        self.assertFalse(good.sent)
        self.assertIsNone(publisher._pending)

    async def test_invalid_events_values_and_oversized_query_never_start_api(self):
        factory = FakeFactory(FakeClient())
        publisher = self.publisher(factory)
        invalid = [None, {"event": "at"}, self.weather(time="private-invalid-date"),
                   self.weather(time="2026-09-27T01:00:46"), self.weather(value=True),
                   self.weather(value="90.8"), self.weather(value=float("inf")),
                   self.weather(value=10 ** 400), self.weather(values={"password": "private"})]
        for record in invalid:
            self.assertFalse(publisher.submit(record))
        oversized = ESPHomePublisher(self.events.append, allowed_keys={"x" + str(i) for i in range(500)},
                                     client_factory=factory, clock=lambda: self.now)
        self.publishers.append(oversized)
        self.assertFalse(oversized.submit(self.weather(values={"x" + str(i): 123456 for i in range(500)})))
        self.assertFalse(factory.calls)
        self.assertNotIn("private", json.dumps(self.events))


if __name__ == "__main__":
    unittest.main()
