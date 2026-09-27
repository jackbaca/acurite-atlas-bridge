# Initial release validation

Tested September 26, 2026 on one AcuRite Atlas 06099M and Home Assistant OS
18.3 / Core 2026.9.3 / Supervisor 2026.09.2, amd64.

- **40 Python tests passed:** real loopback protocol forwarding, request framing,
  safe field extraction, publisher retries/identity checks, firmware decoding,
  and app setup/metadata/error handling.
- **Two standalone C++ checks passed:** bounded queue behavior and exact form-key
  parsing; compiled with `-Wall -Wextra -Werror`.
- **Public firmware target compile passed** with ESPHome 2026.5.3, ESP-WROOM-02.
  This compile used dummy credentials and was not flashed. Its OTA image SHA-256
  was `2fbbb530f988cb09222d8ab7f55b292864e207b1ef1a38c4de063512d702017b`.
- **GitHub CI passed**, including a Docker build of the HA app on amd64.
- **Real HA repository installation passed:** Supervisor found the public app,
  built and installed 0.1.0, and started it with protection mode enabled, start
  on boot, and watchdog enabled. No privileged mode or Supervisor/HA token is
  requested by the app.
- **Actual host handoff passed:** the previous standalone controller stopped;
  the app verified the receiver and connected both interfaces at 02:16:53 UTC
  September 27. Its first real upload arrived at **02:17:07 UTC**, with 18
  numeric observations and two `normal` battery statuses. Independent HA REST
  readback confirmed Last Upload advanced to that time and Fresh was on.
- **Receiver move and power-cycle recovery passed:** after moving the display
  to the rack and reconnecting USB power on the HA box, ESPHome reported an
  external-system reset. The bridge reconnected automatically. All 18 numeric
  fields and both battery statuses received fresh HA timestamps at **02:21:27
  UTC**; Last Upload advanced and Fresh was on.

The handoff uses the working diagnostic ESPHome firmware installed earlier that
day, with native API encryption disabled. The public firmware removes its legacy
fallback and adds API encryption/SSID reporting. The encrypted public firmware
has target-compile proof; **its exact live installation is not yet verified**.
The app's encrypted connection configuration is covered by its setup tests.

The earlier live prototype also passed recurring HA delivery, a host-service
restart, and controlled expiration/recovery: all 18 numeric values became unknown
after 11 minutes without publication and returned on the next delivered packet.

Still open: HA-host cold boot, aarch64 runtime, nonzero rain/reset behavior,
and indoor-temperature/humidity extraction. Neither a high indoor value on the
display nor a registered empty entity is proof of usable rack-temperature data.
