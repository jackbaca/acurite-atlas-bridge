# Public display RX firmware: decoding and protocol strings

Static analysis on September 26, 2026 found a usable public firmware image and
decoded its simple word transformation. This record covers file evidence only:
no firmware was installed and no commands from this image were sent to hardware.

## Source and integrity

- Public [manifest](https://atlasdisp-firmware.myacurite.com/) returned HTTP 200
  with 146 bytes of JSON: version `020`, checksum string `250130783`, and the
  [binary URL](https://atlasdisp-firmware.myacurite.com/bin/altasdisp_rx_firmware_v020.bin).
  The filename really is **altasdisp**, with that spelling.
- HEAD reported 30,720 bytes, `application/octet-stream`, last modification
  `Wed, 06 Mar 2024 16:18:57 GMT`, and ETag
  `ad826864ad6a5abf9289fb1eeadd8374`. A first read retrieved only bytes 0–511 and
  returned HTTP 206; root subsequently downloaded the complete image once.
- Registered artifact:
  `<registered-analysis-output>/altasdisp_rx_firmware_v020.bin`.
- Original SHA-256:
  `eb6b5ffe039e77e22ccedbd5f7b6c4ae7239bffe87ec642c1d312177d4d6288b`.
- The manifest checksum is the sum of the **little-endian 32-bit encoded words
  modulo 2^32**, exactly `250130783`. It is not CRC32; encoded CRC32 is
  `1444209615`.

The official [Wi-Fi setup instructions](https://support.acurite.com/hc/en-us/articles/360050596233-Direct-to-Wi-Fi-Weather-Station-Setup)
describe firmware downloads after saving setup settings, but do not document
this manifest or the image's target processor. A boot request to this host and
a small response alone had not established that an image was downloaded.

## Transformation and likely architecture

For every little-endian 32-bit encoded word, independently reproduced decoding is:

```text
plain = ((encoded XOR 0xa35c9a96) - 0x20201905) modulo 2^32
```

The decoded image remains 30,720 bytes and has SHA-256
`6bc96f7e29a434e1bb3744ef48915e531f22fc0826f04faac5528d87e5213915`.
It contains `JL1905V` at file offset `0x104` and again at `0x6317`, readable HTML
and AT command strings, and 444 bytes of trailing `FF` padding. The independent
decoder implementation is [tools/decode_rx_firmware.py](../tools/decode_rx_firmware.py).
Derived binaries belong in registered scratch storage, not the source checkout.

The first vector is `0x910`, repeated default-handler vectors are `0x988`, and
startup instructions begin at file offset `0x110`, consistent with an image
loaded at `0x800`. Vector ordering and startup instructions match the
[APT32F102 SDK startup source](https://github.com/APT-AEteam/APT32F102x_std/blob/master/Source/arch/crt0.S)
and its [reference binary](https://github.com/APT-AEteam/APT32F102x_std/blob/master/Source/Obj/Release_APT32F102_0x0.bin),
supporting an **APT/C-Sky CK80x-family controller**. The initial vector is a
reset-handler address, not an ARM Cortex stack pointer. The exact chip and its
physical placement are not established by these bytes alone.

## Two command vocabularies

Offsets below are decoded file offsets, not runtime addresses. These are public
firmware format strings, not captured private credentials or station values.

The image contains the ESP modem choreography observed on the live UART:
`CWMODE_DEF`, `CWLAPOPT`, `CWSAP_DEF`, `CIPSERVER`, `CIPSTO`, `CIPMUX`,
`CIPSNTPCFG`, `CWAUTOCONN`, `CWJAP_DEF`, `CIPSEND`, `CIPCLOSE`, `SNISERVER`,
and `CIPSTART`. It includes the real AcuRite SSL hosts and slot 3; Weather
Underground uses slot 4. This firmware participates in controlling the ESP
modem. It is not identified as the ESP8266 firmware itself.

A separate group provides a concrete lead for a display/controller interface:

| Offset | Literal or group |
| --- | --- |
| `0x6324` | `AT+RSSI=%d` |
| `0x632f`, `0x633d` | `AT+WEATHER=%s`, `AT+WEATHER=NULL` |
| `0x634d`, `0x6357` | `AT+DATA=1`, `AT+DATA=0` |
| `0x6366` | `AT+WIFI=%d` |
| `0x6371`–`0x639a` | `AT+START=1/0`, `AT+UPDATE=1/0` |
| `0x63af`–`0x63df` | `AT+TIMEZONE=%s/NULL`, `AT+TIME=%s/NULL` |
| `0x63fb`, `0x6408` | `AT+SETTING30`, `AT+SETTINGOFF` |
| `0x641e`–`0x6493` | `AT+SLEEP`, `AT+TXMODE`, `AT+WEATHER`, `AT+START`, `AT+RESET`, `AT+DATA`, `AT+DCON`, `AT+DCOFF`, `AT+CLEARMAC`, `AT+TEST`, `AT+CHECKVER` |

Subsequent [WEATHER control-flow analysis](firmware-weather-path-20260926.md)
traced this parser to a separate serial peripheral and established that
`AT+WEATHER` handles downloaded forecasts. It is not a query for live sensor
observations. No normal path from ESP-facing input to this command parser was
identified, so sending those commands into the existing bridge is not supported
as a way to retrieve the missing fields. The separate
[DATA payload](rx-data-interface.md) is the observation-data path.

## HTTP and weather-schema evidence

- `0x7097`: boot manifest request template `GET /%s/rx_firmware.json`.
  The file also contains the string `atlas` at `0x6747`; its use as this format
  argument has not yet been traced. The public root manifest already supplies
  an image, so guessing further endpoints is unnecessary.
- `0x714d`: `User-Agent:AtlasDisp/20`.
- `0x7169`, `0x7170`: binary request `GET %s`, `Range:bytes=%d-%d`.
- `0x71c8`: forecast request `GET /forecast/%s/%s`.
- `0x71f8`: the live weather POST endpoint
  `/weatherstation/updateweatherstation`, followed by station-identification
  format fields. Its numeric and battery keys match the current capture parser.
- `0x7383`–`0x73d4`: additional conditional-looking field strings include
  `strikecount`, `last_strike_distance`, `interference`, and `last_strike_ts`.
  Their presence in firmware is not evidence those values have been transmitted
  by this receiver during the live experiment.
- Setup routes at `0x6d83`–`0x6dac`: `/SetFrame`, `/set.js`, `/favicon.ico`,
  `/logo.png`, `/config.cgi`; update-related literal at `0x6e0f`:
  `/softwareupdate/data`. No request to that update route was made.

The complete setup HTML and `settingsCallback` agree with the independently
inspected setup page. No explicit indoor-temperature/humidity or week/month/year
rain upload keys were found among the readable strings. That is a bounded
negative string-search result, not proof that those values are absent from binary
messages, runtime structures, or the other processor. The subsequent
[DATA word map](rx-data-interface.md) records the binary observation layout and
its separate UART. This image has not been identified as the main processor's
display application.

## Reproducing the disassembly

The decoder and [GNU tool builder](../tools/build_csky_objdump.py) keep their
outputs in caller-supplied registered scratch jobs. The GNU binutils 2.45 source
archive used for the tool build was 27,868,232 bytes, SHA-256
`c50c0e7f9cb188980e2cc97e4537626b1672441815587f1eab69d2a1bfbef5d2`.
The resulting tools are under
`<registered-tool-build>/build/binutils/`.

Raw `objdump -b binary -m csky:ck802` did **not** select ABI v2 and incorrectly
split 32-bit instructions. Correct analysis used `objcopy` to wrap the decoded
binary as `elf32-csky-little`, machine `csky:ck802`, with `.data` at VMA `0x800`
and section flags `alloc,load,readonly,code,contents`. The synthetic ELF's
little-endian 32-bit `e_flags` at byte offset 36 was then set to `0x20000010`
(ABI v2 plus CK802). `objdump -D -EL` on that ELF correctly decoded the startup
`mtcr`, `mfcr`, and `lrw` instructions. The wrapper is for analysis only and is
**never an image to flash**.

Retained analysis artifacts:

- Decoded binary: `<registered-analysis-output>/atlas-rx-v020-decoded.bin`.
- Synthetic ELF and disassembly: `<registered-analysis-output>/analysis-only-csky.elf` and `atlas-rx-v020.disasm.txt`.

Keep these source instructions, hashes, and the focused traces. The registered
download/build/disassembly jobs may expire through the storage lifecycle; they
are reproducible and must not be confused with device recovery images.
