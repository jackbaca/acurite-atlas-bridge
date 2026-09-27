# Configuration

Install the project's ESPHome firmware on the 06099M display first.

Set **device_host** to its IPv4 address or hostname, and **api_encryption_key**
to its ESPHome API key. Leave **wifi_ssid** blank with current firmware; enter
the network name for older diagnostic firmware. No Wi-Fi password is required
by this app.

Enable Start on boot and start the app. Check its log for `Receiver verified`,
`publisher_connected`, and a weather upload within about five minutes. Accept
the discovered ESPHome device in Settings → Devices & services, or add ESPHome
manually if mDNS does not cross your VLANs.

Run only one bridge controller per receiver. Stop an old host service before
starting this app. API encryption covers native publication; the raw UART bridge
on TCP 6638 still requires a trusted/restricted local network.

Indoor temperature and humidity are not captured. USB supplies power, and the
data connection remains Wi-Fi. Weather entities expire after 11 minutes without
updates; check Fresh before relying on retained battery status.
