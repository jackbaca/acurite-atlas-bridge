import json
import unittest

from acurite_bridge.bridge.forecast_inspector import ForecastInspector, MAX_RESPONSE, parse_forecast


FORECAST = {
    "current": {
        "temperature": {"high": "94", "low": 68},
        "precipitation": {"probability": "20%"},
        "icon": "3",
    },
    "tomorrow": {
        "temperature": {"high": 89.5, "low": "65"},
        "precipitation": {"probability": "5.5%"},
        "icon": 4,
    },
    "location": "PRIVATE LOCATION",
    "postal_code": "PRIVATE POSTAL CODE",
    "token": "PRIVATE TOKEN",
}
VALUES = {
    "current_temperature_high": 94.0,
    "current_temperature_low": 68.0,
    "current_precipitation_probability": 20.0,
    "current_icon": 3,
    "tomorrow_temperature_high": 89.5,
    "tomorrow_temperature_low": 65.0,
    "tomorrow_precipitation_probability": 5.5,
    "tomorrow_icon": 4,
}
BODY = json.dumps(FORECAST).encode()


def response(body=BODY):
    return (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\n\r\n" + body)


class ForecastTests(unittest.TestCase):
    def inspector(self):
        events = []
        inspector = ForecastInspector(lambda event, **data: events.append({"event": event, **data}), 3)
        return inspector, events

    def test_all_http_fragment_boundaries_emit_once_without_private_fields(self):
        data = response()
        for split in range(len(data) + 1):
            inspector, events = self.inspector()
            inspector.feed(data[:split])
            inspector.feed(data[split:])
            inspector.eof()
            inspector.feed(data)
            self.assertEqual(events, [{"event": "forecast", "link": 3,
                                       "source": "cloud_forecast", "values": VALUES}])
            self.assertNotIn("PRIVATE", json.dumps(events))
            self.assertEqual(inspector.buffer, bytearray())

    def test_chunked_bytewise_with_extensions_and_trailers(self):
        inspector, events = self.inspector()
        data = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        for chunk in (BODY[:20], BODY[20:]):
            data += f"{len(chunk):x};opaque=PRIVATE\r\n".encode() + chunk + b"\r\n"
        data += b"0\r\nX-Location: PRIVATE\r\n\r\n"
        for byte in data:
            inspector.feed(bytes([byte]))
        self.assertEqual(events[0]["values"], VALUES)
        self.assertEqual(len(events), 1)
        self.assertNotIn("PRIVATE", json.dumps(events))

    def test_close_delimited_body_waits_for_eof(self):
        inspector, events = self.inspector()
        inspector.feed(b"HTTP/1.0 200 OK\r\n\r\n" + BODY)
        self.assertEqual(events, [])
        inspector.eof()
        self.assertEqual(events[0]["values"], VALUES)

    def test_invalid_schema_and_numeric_values_are_not_coerced(self):
        body = json.dumps({
            "current": {
                "temperature": {"high": True, "low": float("nan")},
                "precipitation": {"probability": "101%"},
                "icon": "clear-day",
            },
            "tomorrow": {
                "temperature": {"high": "PRIVATE", "low": 1e100},
                "precipitation": {"probability": "-1%"},
                "icon": 2.5,
            },
        }).encode()
        for value in (body, b"null", b"[]", b"not JSON", b"\xff", b"{}", b"[" * 2000):
            with self.subTest(value=value[:20]):
                self.assertEqual(parse_forecast(value), {})
        self.assertEqual(parse_forecast(json.dumps({"current": {
            "temperature": {"high": 90}, "icon": 256}}).encode()),
            {"current_temperature_high": 90.0})

    def test_invalid_and_truncated_framing_never_emits_forecast(self):
        cases = [
            b"HTTP/1.1 404 Missing\r\n\r\n" + BODY,
            b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\n" + BODY,
            b"HTTP/1.1 200 OK\r\nContent-Length: 10000\r\n\r\n" + BODY,
            b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n" + BODY,
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 1\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n" + BODY,
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nGG\r\n",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nabc",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nxZZ",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n",
            b"PRIVATE INVALID HEADER\r\n\r\n" + BODY,
        ]
        for data in cases:
            with self.subTest(data=data[:80]):
                inspector, events = self.inspector()
                inspector.feed(data)
                inspector.eof()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event"], "forecast_skip")
                self.assertNotIn("PRIVATE", json.dumps(events))
                self.assertEqual(inspector.buffer, bytearray())

    def test_response_memory_bound_and_unknown_json_are_discarded(self):
        for data in (b"x" * (MAX_RESPONSE + 1),
                     b"HTTP/1.1 200 OK\r\nContent-Length: 999999\r\n\r\n",
                     response(b'{"location":"PRIVATE"}')):
            inspector, events = self.inspector()
            inspector.feed(data)
            inspector.eof()
            self.assertTrue(inspector.done)
            self.assertEqual(inspector.buffer, bytearray())
            self.assertEqual(events[0]["event"], "forecast_skip")
            self.assertNotIn("PRIVATE", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
