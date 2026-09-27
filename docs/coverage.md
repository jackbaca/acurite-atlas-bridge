# Verified field coverage

The initial 06099M trial independently verified these native ESPHome entities in
Home Assistant across recurring uploads and a host-service restart. A controlled
publication pause made all 18 numeric states unknown after 11 minutes; restored
publication returned them and set Fresh on.

| Reading | Unit / limit |
| --- | --- |
| Outdoor temperature; dew point; feels like; heat index; wind chill | °F |
| Humidity | % |
| Pressure | Uploaded inHg; differs from display-adjusted pressure |
| Wind speed, gust, average | mph; screen aggregation-window equivalence not proved |
| Wind direction, gust direction | degrees |
| Rain today, rain past hour | inches; nonzero-rain semantics still need observation |
| UV index | dimensionless |
| Illuminance | lux |
| Measured light | seconds; reset boundary not proved; not labeled “today” |
| Sensor signal quality | raw 0–4; separate from Wi-Fi RSSI in dBm |
| Sensor and hub battery | normal/low; retained when weather is stale |

Last Upload uses host capture time, not the station's observation timestamp.
No weekly/monthly/yearly rain helpers are automatically configured.

Indoor temperature/humidity, forecast values, and lightning observations have
not been captured into HA by this release. Static firmware identifies conditional
lightning fields, but that is not live entity proof. Stored display history,
trend arrows, and exact wind-history windows are also unverified. The normal
ESP route has not been shown to provide indoor observations; a separate hardware
interface may be necessary. See the [DATA map](rx-data-interface.md).

The initial working prototype and public firmware are different source snapshots.
The public package removes the legacy tuple emulator, adds native API encryption
and SSID reporting, and keeps the field publisher and bounded raw bridge. Target
compile and HA app readback are distinct release checks.
