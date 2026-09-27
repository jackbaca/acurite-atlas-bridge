# Installation

## Display firmware

The stock display does not accept this firmware through its ordinary USB cable.
Only the **ESP-WROOM-02** Wi-Fi module is replaced. Leave the main display and
APT helper firmware alone. This initial hardware installation has not been
validated as a beginner procedure on a second unit; the working unit already
had ESPHome installed when this bridge was tested.

1. Verify the module is ESP-WROOM-02 and the display is 06099M. On the known board,
   `WIFI_PROG` is labeled `VCC`, `RST`, `TXD`, `RXD`, `MOD`, `GND`. `APT_Debug`
   is a different interface and is not used by this installer.
2. Use a **3.3 V logic** USB-to-serial adapter. Connect common ground and crossed
   RX/TX after verifying the board's labels and voltage. When powering the display
   normally, leave the adapter's VCC disconnected. Never apply 5 V logic to ESP
   pins. Hold ESP GPIO0 (`MOD`, verify continuity first) low during reset to enter
   its serial bootloader. Release it afterward.
3. Before writing, read and preserve the original **2 MB ESP flash** with esptool:
   `esptool --port YOUR_SERIAL_PORT read-flash 0 0x200000 factory-backup.bin`.
   Check that the backup is exactly 2,097,152 bytes and keep its SHA-256. It may
   contain credentials; never upload it publicly.
4. In ESPHome Device Builder, create a new configuration containing:

   ```yaml
   substitutions:
     device_name: acurite-display
     friendly_name: AcuRite Display

   packages:
     acurite: github://jackbaca/acurite-atlas-bridge/firmware/acurite-atlas.yaml@main
   ```

   The package fetches its own headers; there are no C++ files to copy. Add the
   four keys below to your existing `secrets.yaml` without replacing other secrets:

   ```yaml
   wifi_ssid: "YOUR_NETWORK"
   wifi_password: "YOUR_WIFI_PASSWORD"
   acurite_api_key: "YOUR_BASE64_32_BYTE_KEY"
   acurite_ota_password: "YOUR_OTA_PASSWORD"
   ```

   Generate a fresh API key with `openssl rand -base64 32`. Keep the same key
   for the HA app and the ESPHome integration.
5. In ESPHome Device Builder, validate and install your new configuration. For first
   installation use the serial adapter; for an existing compatible ESPHome device,
   use its established OTA method. ESPHome 2026.5.3 or newer is required; the
   initial target compile was tested with 2026.5.3.
6. Return the module to normal boot and the display to its weather page. Keep
   its existing Wi-Fi setup configured to the same network as the new ESPHome
   firmware; the modem's scan/join replies must match it.

You can set `device_name` and `friendly_name` using the substitutions at the top
of the YAML. Keep names stable after adding the HA integration to preserve IDs.

## Home Assistant app

Use the repository button in [README](../README.md). The app needs no privileged
mode, Docker access, Supervisor token, USB mapping, or HA long-lived token.

Options:

| Option | Value |
| --- | --- |
| `device_host` | Receiver IPv4 address or hostname; use a DHCP reservation if mDNS does not cross VLANs. |
| `api_encryption_key` | Same key as `acurite_api_key`; leave blank only for older firmware with encryption disabled. |
| `wifi_ssid` | Usually blank: current firmware reports its connected SSID. Set explicitly for older diagnostic firmware. |

The app verifies the native API and discovers the device MAC and AP metadata
before claiming the UART connection. It retries a temporarily offline receiver.
If it exits with a setup error, correct configuration and start again. Firmware
and app should agree on the network name; the app does not change Wi-Fi settings.

## Verify and troubleshoot

- `publisher_connected`: native API is connected; this alone is not a weather update.
- `weather` followed by `publisher_submitted`: a real upload was captured and sent.
- HA **Last Upload** should advance and **Weather Upload Fresh** should be on.
  Allow one five-minute upload cycle; after a reboot allow two cycles before
  diagnosing a failure. Do not mistake retained readings for new observations.
- If the bridge connects but receives no data, ensure only one controller is
  running, the display is on its normal weather page, and its configured SSID
  matches ESPHome/app metadata.
- If API connection fails, verify the IP, key, TCP 6053, and network routing.
  The bridge also needs receiver TCP 6638 and outbound vendor HTTPS TCP 443.
- If discovery fails, manually add **ESPHome** in Devices & services. No HACS
  integration or MQTT discovery is used.

The display's USB cable remains a power cable after installation. Moving power
to the HA box does not change the Wi-Fi connection. Wait for the first fresh
upload after moving it. **Do not assign the outdoor Temperature entity to rack
temperature: indoor readings are not supported by this release.**

## Other always-on hosts

Run the same runtime directly with Python 3.11+ and
`pip install aioesphomeapi==46.6.0`. Then use
`acurite_bridge/bridge/modem_host.py --help`. Supply the receiver IP/MAC, SSID,
AP metadata, and these working compatibility flags:
`--initial-status 4 --ack-closed-links --announce-ready --publish-esphome --quiet-polls`.
Pass an API encryption key through `ACURITE_API_KEY`, never a command-line option.
Use your platform's service manager and keep exactly one controller running.
