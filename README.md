# AcuRite Atlas Bridge

Bring an **AcuRite Atlas 06099M Wi-Fi display** into Home Assistant using its
existing ESP8266. No separate RF receiver, RTL-SDR, phone app, MQTT broker, or
HACS integration is required.

The bridge publishes **18 numeric weather measurements and two battery statuses**
through Home Assistant's built-in ESPHome integration, approximately every five
minutes. It has been tested on one 06099M, including automatic updates, a host
service restart, and stale-data expiration/recovery. This is an experimental
release; complete display/host cold-boot recovery is still being tested. See the
[validation record](docs/validation.md) for the exact tested deployment and limits.

## What you need

- An Atlas **06099M**, with the ESP-WROOM-02 Wi-Fi module.
- ESPHome firmware from this project installed on that module.
- Home Assistant OS with the app below, or another always-on host running the
  Python bridge.
- Wi-Fi/network access between the display, bridge host, and Home Assistant,
  plus internet access to AcuRite's services.

**First installation on a stock display requires opening the case and using the
ESP8266 serial programming interface. The display's USB socket provides power;
it is not a USB weather-data or ESP flashing interface.** Once ESPHome is
installed, subsequent firmware updates can use OTA. See [installation](docs/install.md).
Do not use this firmware on another AcuRite model without verifying its hardware
and serial protocol.

## Install in Home Assistant

1. [Install the ESPHome firmware](docs/install.md#display-firmware).
2. Add this app repository:
   [![Add repository](https://my.home-assistant.io/badges/supervisor_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjackbaca%2Facurite-atlas-bridge)
3. In **Settings → Apps → App store**, install **AcuRite Atlas Bridge**.
   Older HA versions call Apps "Add-ons".
4. Configure the receiver's IP/hostname and its ESPHome API encryption key.
   With the current firmware, Wi-Fi metadata is read automatically. For older
   diagnostic firmware, also enter its Wi-Fi network name; no Wi-Fi password
   is needed by the app.
5. Start the app and enable **Start on boot**. Its log should say
   `Receiver verified`, then `publisher_connected`. Allow five minutes for
   the next weather upload.
6. In **Settings → Devices & services**, accept the discovered ESPHome device.
   If discovery does not cross your VLANs, add the ESPHome integration manually
   using the receiver's IP and the same API key.

The entities belong to the **ESPHome device**, so removing the bridge app does
not delete your HA device or history. It stops new readings. Run only one bridge
controller per display.

## Measurements

Outdoor temperature, humidity, pressure, dew point, wind speed, gust speed,
wind direction, gust direction, average wind speed, rain today, rain past hour,
UV index, illuminance, measured-light duration, feels-like temperature, heat
index, wind chill, and sensor reception quality. Sensor and hub battery statuses
are reported separately.

Numeric readings become unknown after **11 minutes without an update**. Check
**Weather Upload Fresh** and **Last Upload**; battery strings retain their last
known value when stale. Pressure preserves the uploaded value and may differ
from the display's adjusted pressure. See [field coverage](docs/coverage.md).

**Indoor temperature and indoor humidity are not currently captured.** A display
showing them does not mean they are present on the ESP-facing upload interface.
This release cannot yet serve as a rack-temperature sensor. Forecast values,
lightning observations, and stored display history are also outside the verified
entity set. Home Assistant can retain rain history from the available rain inputs;
this project does not preconfigure historical-total helpers.

## How it works

```text
Display helper → ESP UART bridge → Home Assistant app → vendor HTTPS services
                                           ↓ captured observations
                                 ESPHome publish_upload → Home Assistant
```

The Python process emulates the ESP-AT modem and preserves real vendor responses.
It captures observations before sending them over HTTPS, then publishes them
through the receiver's native ESPHome API. The current implementation depends
on an always-on host and AcuRite's servers. The raw UART bridge is an exclusive,
unencrypted TCP service on port 6638: use a trusted LAN and restrict network
access to your bridge host. Native ESPHome API encryption does not encrypt that
raw bridge port.

## Research and development

The public RX v020 helper firmware can be decoded and inspected without changing
the receiver. The decoder, additive checksum, C-Sky architecture evidence, UART
separation, DATA word layout, and forecast path are documented here:

- [Firmware decoding](docs/public-firmware-strings-20260926.md)
- [DATA interface and word map](docs/rx-data-interface.md)
- [WEATHER forecast interface](docs/firmware-weather-path-20260926.md)

Manufacturer firmware binaries, device-specific compiled images, raw captures,
and credentials are not distributed. The analysis scripts verify exact input
hashes and write only caller-selected output. Keep generated output outside
the source tree.

Run the Python tests with `python -m unittest discover -s tests`. The standalone
C++ tests cover the bounded byte queue and exact query-key parsing.

## Credits and license

[CStoEE/pslawinski's 06088-RX ESPHome project](https://github.com/pslawinski/acurite_wx_display_esphome)
established the display Wi-Fi replacement approach. Its older eight-value tuple
parser and configuration are not included here; this display uses a different
modem protocol. [ScienceABC123's Atlas RF-module project](https://github.com/ScienceABC123/Acurite-Atlas-RF-Module-Weather-Display)
documents another internal receiver interface.
[Acuparse](https://github.com/acuparse/acuparse) and
[weewx-interceptor](https://github.com/matthewwall/weewx-interceptor) informed
upload-field interpretation. [APT's SDK](https://github.com/APT-AEteam/APT32F102x_std)
and [GNU binutils](https://www.gnu.org/software/binutils/) supplied architecture
references and disassembly tooling. ESPHome and aioesphomeapi provide the native
Home Assistant connection. Their software remains under its respective licenses.

Original bridge code and documentation: [MIT](LICENSE). This license does not
cover AcuRite firmware or third-party projects. This is an independent project,
unaffiliated with AcuRite.
