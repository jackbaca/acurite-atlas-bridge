# Acknowledgments and dependencies

This project builds on earlier community work. The people who documented these
displays and upload formats made this investigation possible. Their contribution
is separate from whether their code is installed by this bridge.

## Earlier AcuRite work

| Author / project | Contribution to this project | Included or required at runtime? |
| --- | --- | --- |
| [CStoEE's 06088-RX ESPHome write-up](https://community.home-assistant.io/t/acurite-weather-station-display-esphome-firmware/672058) and [pslawinski's firmware repository](https://github.com/pslawinski/acurite_wx_display_esphome) | Established replacing a display's ESP-WROOM-02 firmware with ESPHome, the programming approach, and an earlier weather/forecast interface. This was the starting point for prior attempts on this receiver. | No. The inherited eight-value tuple parser and baseline configuration are excluded from this release; that repository does not provide an explicit license. The 06099M modem bridge uses a different observed protocol. |
| [ScienceABC123's Atlas RF-module weather display](https://github.com/ScienceABC123/Acurite-Atlas-RF-Module-Weather-Display) | Documented the internal synchronous receiver interface, packet types, and separate indoor sensor board. It is an important reference for the remaining indoor-data investigation. | No. This bridge does not install its GPIO/ARM display software or implement its synchronous interface. |
| [Acuparse](https://github.com/acuparse/acuparse) ([upstream](https://gitlab.com/acuparse/acuparse)) | Informed the interpretation of upload fields, rain, battery status, and light measurements. | No. The bridge has its own framing and field extraction. |
| [Matthew Wall's weewx-interceptor](https://github.com/matthewwall/weewx-interceptor) | Provided a reference for AcuRite upload names and measurement semantics. | No. WeeWX is not required. |

## Software used by the bridge

| Project | Role |
| --- | --- |
| [ESPHome](https://github.com/esphome/esphome) | Firmware framework, UART transport, sensors, OTA, and native Home Assistant API. The package requires ESPHome 2026.5.3 or newer; initial builds used 2026.5.3. |
| [aioesphomeapi](https://github.com/esphome/aioesphomeapi) | Actual Python runtime dependency for device verification and publishing observations through the native API. Pinned to 46.6.0 in `acurite_bridge/requirements.txt`; pip installs its dependencies. |
| [Home Assistant](https://github.com/home-assistant/core) and [Supervisor](https://github.com/home-assistant/supervisor) | Built-in ESPHome integration and app installation/lifecycle. No HACS integration is needed. |
| [ESP8266 Arduino core](https://github.com/esp8266/Arduino) and [PlatformIO](https://github.com/platformio/platformio-core) | ESP8266 framework and firmware build tooling selected by ESPHome. |
| [Python](https://www.python.org/) | Bridge runtime and standard-library networking. The HA app uses the official Python 3.13.7 slim-bookworm container image. |

These projects and their dependencies retain their own licenses and notices.
Our MIT license applies only to the original code and documentation in this
repository; it does not relicense the installed software above.

## Firmware research tools and sources

[APT's APT32F102 SDK](https://github.com/APT-AEteam/APT32F102x_std) supplied
architecture, vector-table and peripheral references.
[GNU binutils](https://www.gnu.org/software/binutils/) supplied the C-Sky
disassembler. These are analysis references/tools, not bridge runtime dependencies.
The research documents link the exact sources used.

AcuRite's publicly hosted RX firmware was the input for the decoder and interface
analysis. Its binaries are not redistributed or covered by this repository's
license. This is an independent community project without vendor endorsement.
