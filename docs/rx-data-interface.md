# Public RX v020: DATA interface and uploaded fields

The decoded public RX firmware accepts live observations through a binary
`AT+DATA=` command on its main-display serial interface. It then converts that
buffer into the existing weather uploads. This command parser uses a different
UART and receive buffer from the ESP modem connection currently bridged to the
host. The strings `AT+DATA=1` and `AT+DATA=0` are status-query replies; they do
not establish a command for enabling additional data on the ESP connection.

Every payload word from 0 through 26 has an identified outdoor, status, or time
use in the upload builder. No indoor temperature/humidity or week/month/year
rain total was identified. Later words are not interpreted by these upload
builders, and the final counted word is a checksum. This is a static finding
about the public v020 image, not a capture of the currently installed
controller's main-side packets or proof that all available words are populated.

## Scope and reproducibility

Purpose: determine whether the existing observation buffer contains additional
screen readings and whether the ESP bridge can request it. Device effect: none;
only the previously downloaded public image and disassembly were read. Keep this
source report and the image receipt; the registered derived artifacts can be
retired after the reverse-engineering work is closed.

The [public-image receipt](public-firmware-strings-20260926.md) records the source,
checksum, decoding transform, and architecture evidence. Original SHA-256:
`eb6b5ffe039e77e22ccedbd5f7b6c4ae7239bffe87ec642c1d312177d4d6288b`.
Decoded SHA-256:
`6bc96f7e29a434e1bb3744ef48915e531f22fc0826f04faac5528d87e5213915`.

The disassembly is
`<registered-analysis-output>/atlas-rx-v020.disasm.txt`.
Its synthetic ELF uses VMA `0x800` and C-Sky ABI-v2/CK802 flags
`0x20000010`, necessary for correct mixed 16/32-bit instruction decoding. All
addresses below are runtime addresses; subtract `0x800` for decoded-file
offsets. Literal pools and embedded data were not treated as instructions.
The disassembler flags do not independently identify the exact silicon.

## Separate serial paths

Startup at `0x1018–0x103a` copies initialized pointers from flash `0x7e24`
to RAM `0x20000000`. The first two are `0x40081000` and `0x40080000`.

| Role | Peripheral and receive path | Transmit path |
| --- | --- | --- |
| Main-display command interface | Pointer at `0x20000000` → `0x40081000`; ISR `0x12b8` reads register `+0`, stores in `0x200000d0`, and maintains a 16-bit count at `0x200002d0`. Input is capped at 95 bytes. | Routine `0x15c8` writes bytes to register `+0` on this peripheral, then clears the input buffer/count. |
| ESP modem interface | Pointer at `0x20000004` → `0x40080000`; ISR `0x122c` reads register `+0x28`, stores in `0x20000300`, and maintains count at `0x200000be`. | Separate routine `0x1f10` writes to register `+0x2c` on this peripheral. |

The ISR vector positions correspond to UART1 and UART0 respectively in the
[APT SDK startup vector table](https://github.com/APT-AEteam/APT32F102x_std/blob/master/Source/arch/crt0.S).
The peripheral and buffer separation above comes directly from the target
firmware; it does not depend on assigning those UART names or physical pins.

The main loop calls dispatcher `0x1df4`, which searches the main-side buffer
for `AT+` and calls parser `0x1884`. The ESP-side buffer feeds a separate modem
response/HTTP path. No normal forwarding path from that buffer into this
command parser was identified. The independently traced
[WEATHER forecast interface](firmware-weather-path-20260926.md) uses the same
main-side parser and confirms this separation.

## Command framing and replies

The DATA branch at `0x1b44` recognizes this layout:

```text
offset 0..7   ASCII "AT+DATA="
offset 8      one-byte word count N, capped at 32 by the receiver
offset 9      skipped byte; meaning not established
offset 10..   N little-endian 16-bit words, including final checksum word
```

The code reads only byte 8 as the count. It does not establish that bytes 8–9
form a 16-bit length. The dispatcher searches for CRLF; if absent it waits for
a short internal timer and still calls the parser. That timer's duration was
not established. Consequently, CRLF is not a strict required terminator in the
observed parser, and embedded NULs do not establish rejection of binary data.

At `0x1b60–0x1b76`, the code copies `2*N` bytes into the inactive one of two
64-byte buffers, `0x20000032` and `0x20000072`. The selector is at
`0x20000130`. At `0x1b78–0x1b92` it adds the first `N-1` little-endian
16-bit words modulo 65536 and compares the result with word `N-1`. On success
it flips the selected buffer and replies `AT+DATA\r\nOK\r\n` through the
main-side transmitter. A checksum mismatch produces `\r\nERR\r\n`.

The status-query syntax is **`AT+?DATA`**, not `AT+DATA?`. Its branch at
`0x1904` checks global state mask `0x20000060` and returns `AT+DATA=1` or
`AT+DATA=0`, followed by `\r\nOK\r\n`. These replies do not carry the
observation buffer. No packet injection or malformed-length experiment was
performed.

## Payload fields used by the uploads

The normal weather POST builder starts at `0x3d28`; the WU builder starts at
`0x4224`. Both read the selected buffer. Indexes below are zero-based 16-bit
words from the copied payload. `S(w)` means signed 16-bit interpretation and
`T(a/b)` means integer division truncated toward zero.

| Word | Byte offset | Established use | Instruction addresses |
| --- | --- | --- | --- |
| 0–2 | `0x00–05` | `dateutc`; six bytes: year suffix, month, day, hour, minute, second | `0x3d3e–0x3d42`, helper `0x3c14–0x3cfa` |
| 3 | `0x06` | `windspeedmph = T((62*S(w3)+50)/100)` | `0x3df8–0x3e10` |
| 4 | `0x08` | `winddir = S(w4)` | `0x3e1c–0x3e24` |
| 5 | `0x0a` | Incoming gust speed; WU uses `T((62*S(w5)+50)/100)` directly | `0x429c–0x42b2` |
| 6 | `0x0c` | Direction paired with word 5's gust | `0x1bb6–0x1bbc`, `0x3e72–0x3e82` |
| 7 | `0x0e` | `dailyrainin = S(w7)/100`, two decimals | `0x3e86–0x3ea0` |
| 8 | `0x10` | `rainin = S(w8)/100`, two decimals | `0x3ea4–0x3ebe` |
| 9 | `0x12` | `humidity = S(w9)` | `0x3eca–0x3ed2` |
| 10 | `0x14` | `tempf = S(w10)/10`, one decimal | `0x3ed6–0x3ef0` |
| 11 | `0x16` | Pressure input; configuration-dependent conversion described below | `0x3ef4–0x3f28` |
| 12 | `0x18` | Sensor battery in low byte, hub battery in high byte: zero → `low`, nonzero → `normal` | `0x3f2c–0x3f6c`, `0x4126–0x412c` |
| 13 | `0x1a` | `rssi`: first present bit wins; bit 0→4, bit 1→3, bit 2→2, bit 3→1, otherwise 0 | `0x3f70–0x3fa8`, `0x412e–0x413c` |
| 14 | `0x1c` | `heatindex`, integer °F serialized with `.0` in ordinary range | `0x3fae–0x3fca` |
| 15 | `0x1e` | `feelslike`, same integer °F representation | `0x3fce–0x3fea` |
| 16 | `0x20` | `windchill`, same integer °F representation | `0x3fee–0x400a` |
| 17 | `0x22` | `dewptf`, same integer °F representation | `0x400e–0x402a` |
| 18 | `0x24` | `windspeedavgmph = T((62*S(w18)+50)/100)` | `0x4036–0x404e` |
| 19 low byte | `0x26` | `uvindex`, unsigned | `0x4052–0x4064` |
| 19 high byte and 20 | `0x27–29` | `lightintensity = ((w19 & 0xff00)<<8) + w20`, unsigned 24-bit | `0x4068–0x4080` |
| 21 | `0x2a` | `measured_light_seconds`, unsigned | `0x408c–0x4092` |
| 22 | `0x2c` | `strikecount`, unsigned | `0x40aa–0x40b2` |
| 23 low byte | `0x2e` | `last_strike_distance`; `0xff` becomes an empty value | `0x40ba–0x40d4`, `0x413e–0x414a` |
| 23 high byte | `0x2f` | `interference = S(w23) >> 8`, arithmetic shift | `0x40bc`, `0x40e2–0x40e6` |
| 24–26 | `0x30–35` | `last_strike_ts`, same six-byte timestamp format as words 0–2 | `0x40b0–0x40b6`, `0x40ea–0x4100` |
| 27–31, if counted | `0x36–3f` | No reads identified in these upload builders. The final counted word is the checksum; other trailing words remain unassigned. | Receive bound/checksum `0x1b54–0x1b92` |

Additional constraints on interpreting this table:

- The timestamp helper emits `20YY-MM-DDTHH:MM:SS`, or an empty string if any
  of the three timestamp words is `0xffff`.
- The POST's gust is a maximum selected from five stored buckets, with the
  corresponding direction. DATA ingestion updates the current bucket from
  words 5/6 at `0x1baa–0x1bbc`. The buckets shift when an internal seconds
  counter crosses 60 at `0x1e64–0x1ea4`. This differs from WU's direct word-5
  use and does not establish equivalence to the display's 60-minute history.
- Words 14–17 are multiplied by ten and narrowed to signed 16-bit before the
  one-decimal formatter. The plain integer-°F description applies to ordinary
  weather values, without that intermediate overflowing.
- For pressure, let `P = helper_0x3b40(S(w11))`. The emitted `baromin` is the
  two-decimal rendering of `S(T(3*(P-10133)/10)+2992)`. The helper also reads
  a numeric configuration string at `*(0x20000024)+448`; if that parses as
  zero, it returns the input unchanged. The nonzero adjustment was not fully
  traced. Word 11 must not be labeled directly as centi-inHg or assumed equal
  to the display's adjusted pressure.
- Extended words 22–26 are emitted when global bit 30 is set. The DATA handler
  sets that bit for **count ≥26**, even though the builder reads through word
  26 and the final counted word is a checksum. This threshold alone does not
  prove that the required bytes were freshly supplied. The live main-side
  packet count and any trailing contents remain unobserved.

## Practical consequence

The existing ESP route already receives the upload fields derived from all
identified observation words. This analysis does not establish a command on
that route which exposes indoor readings, display history, or the uninterpreted
tail. Acquiring those would require separately demonstrated access to another
interface or firmware behavior. No such experiment is required to keep the
currently working live-data integration; Home Assistant can accumulate its own
rain history from the values already being published.
