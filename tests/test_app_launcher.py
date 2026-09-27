import base64
from types import SimpleNamespace
import unittest

from acurite_bridge.app_launcher import SetupError, discover, validate_options


class AppSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_and_metadata_are_read_without_claiming_uart(self):
        key = base64.b64encode(bytes(range(32))).decode()
        calls = []

        class Client:
            async def connect(self, **kwargs):
                calls.append(("connect", kwargs))

            async def device_info(self):
                return SimpleNamespace(mac_address="02:00:00:00:00:10")

            async def list_entities_services(self):
                entities = [SimpleNamespace(key=k, name=n) for k, n in enumerate(
                    ["WiFi SSID", "WiFi BSSID", "WiFi Channel", "WiFi RSSI"])]
                return entities, [SimpleNamespace(name="publish_upload")]

            async def subscribe_states(self, callback):
                for k, v in enumerate(["test-network", "02:00:00:00:00:01", 11, -60]):
                    callback(SimpleNamespace(key=k, state=v, missing_state=False))

            async def disconnect(self, **kwargs):
                calls.append(("disconnect", kwargs))

        def factory(*args, **kwargs):
            calls.append(("factory", args, kwargs))
            return Client()

        options = validate_options({"device_host": "test.local", "api_encryption_key": key})
        result = await discover(options, client_factory=factory,
                                resolver=lambda h: [(None, None, None, None, ("192.0.2.10", 6638))])
        self.assertEqual(result["ssid"], "test-network")
        self.assertEqual(result["mac"], "02:00:00:00:00:10")
        self.assertEqual(result["channel"], 11)
        self.assertEqual(calls[0][1], ("192.0.2.10", 6053))
        self.assertEqual(calls[0][2]["noise_psk"], key)
        self.assertEqual(calls[-1][0], "disconnect")

    async def test_non_bridge_firmware_is_rejected_and_disconnected(self):
        closed = []
        class Client:
            async def connect(self, **kwargs): pass
            async def device_info(self):
                return SimpleNamespace(mac_address="02:00:00:00:00:10")
            async def list_entities_services(self): return [], []
            async def disconnect(self, **kwargs): closed.append(True)
        with self.assertRaisesRegex(SetupError, "firmware first"):
            await discover(validate_options({"device_host": "192.0.2.10"}),
                           client_factory=lambda *a, **k: Client(),
                           resolver=lambda h: [(None, None, None, None, (h, 6638))])
        self.assertEqual(closed, [True])

    async def test_bad_api_key_is_not_echoed(self):
        marker = "private-invalid-key"
        with self.assertRaises(SetupError) as raised:
            validate_options({"device_host": "test.local", "api_encryption_key": marker})
        self.assertNotIn(marker, str(raised.exception))

    async def test_config_cannot_inject_command_line_or_serial_control(self):
        for host in ["--help", "x\nAT+RESET", "x;touch /tmp/file", ""]:
            with self.subTest(host=host), self.assertRaises(SetupError):
                validate_options({"device_host": host})
        with self.assertRaises(SetupError):
            validate_options({"device_host": "test.local", "wifi_ssid": "test\r\nAT+RESET"})


if __name__ == "__main__":
    unittest.main()
