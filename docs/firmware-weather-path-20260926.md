# Static trace: WEATHER is the forecast interface

The decoded public RX v020 firmware's `AT+WEATHER` vocabulary handles downloaded
**forecast data**, not the receiver's live observation packet. Its command parser
uses a different serial peripheral and receive buffer from the ESP-facing modem
interface. The normal paths traced here do not route ESP replies to that parser.

This is a static result for the public v020 image. It is not proof of the exact
controller version currently installed, a physical pin mapping, or an exhaustive
proof about every possible abnormal execution path. No hardware commands were
sent and no firmware was changed during this analysis.

## Reproducible input and address convention

The [image receipt](public-firmware-strings-20260926.md) records the original and
decoded hashes, transformation, architecture evidence, and source URLs.

- Disassembly:
  `<registered-analysis-output>/atlas-rx-v020.disasm.txt`.
- Synthetic ELF beside it: `analysis-only-csky.elf`.
- Objdump:
  `<registered-tool-build>/build/binutils/objdump`.
- ELF VMA is `0x800`; `e_flags=0x20000010` selects the correct C-Sky ABI v2
  decoding, including 32-bit instructions. Startup was checked against the APT
  SDK. All instruction and string addresses below are **runtime addresses**;
  subtract `0x800` for offsets in the decoded binary.

Literal pools and embedded data also appear in objdump output. Apparent
instructions inside those areas were not treated as executable control flow.

## Forecast request, response, and query

| Code address | Traced behavior |
| --- | --- |
| `0x1884` | Command parser; examines character 3 for `?` and then checks the requested name. |
| `0x18ca`–`0x18fe` | `AT+?WEATHER` path: checks the cached string at `config + 0x2c0` and state bit 14. Returns `AT+WEATHER=%s` if available, otherwise `AT+WEATHER=NULL`. |
| `0x1ab8`–`0x1ad2` | `AT+WEATHER` command path acknowledges the command and calls `0x1850`. It does not parse a submitted weather payload. |
| `0x1850`–`0x187a` | Clears cached-forecast-valid bit 14. If country and ZIP fields (`config + 0x280`, `config + 0x2a0`) are populated, sets request bit 11. |
| `0x3d46`–`0x3dac` | Forecast branch, selected by state bit 25, formats `GET /forecast/%s/%s` and the `display.myacurite.com` host, then sends it on link 3. The alternative branch at `0x3dde` formats the live weather POST. |
| `0x4ee0`–`0x4efa` | After finding `200 OK`, the bit-25 forecast branch calls response handler `0x4868`; the other branch calls a different handler at `0x4a24`. |
| `0x4868`–`0x493e` | Forecast handler clears request bit 11, parses `Content-Length`, finds the body beginning with `{`, removes selected whitespace, and checks its length. |
| `0x4948`–`0x4992` | Copies that body to the same `config + 0x2c0` storage used by the query, updates configuration storage, and sets valid bit 14. |

The shared destination and validity flag establish the data's origin. It is
not merely a conclusion drawn from the word `WEATHER` or from the nearby URL.
The generic reply path appends `\r\nOK\r\n` and sends through `0x15c8`.

## Separate serial input paths

Startup at `0x1018` copies 32 bytes of initialized data from flash `0x7e24` to
RAM `0x20000000`. The first two words were independently read from the decoded
image: `0x40081000`, then `0x40080000`.

| Role established by control flow | Peripheral pointer | Receive path | Transmit path |
| --- | --- | --- | --- |
| Main-display command side | RAM `0x20000000` → `0x40081000` (UART1) | ISR `0x12b8` reads register `+0`, appends to `0x200000d0`, increments count at `0x200002d0`, caps at 95 bytes. | `0x15c8` writes register `+0` on this same peripheral, polls completion, and clears the command buffer/count. |
| ESP modem side | RAM `0x20000004` → `0x40080000` (UART0) | ISR `0x122c` reads register `+0x28`, appends to `0x20000300`, increments count at `0x200000be`. | Separate modem transmit routine `0x1f10`; the command reply routine above does not use this peripheral. |

The main loop calls `0x1df4`. At `0x1e00`–`0x1e18`, this routine searches the
**main-side buffer** for `AT+`, checks for CRLF, and passes the located command to
`0x1884` at `0x1e1a`. That is the only direct call to this command parser in the
disassembly. If an incomplete command persists, the code briefly waits before
processing it; a missing CRLF is not a strict rejection guarantee.

The ESP-side buffer instead feeds modem-response and HTTP handling. Its HTTP
forecast handler can update the cached forecast, but that is not a route into
the main-side command parser. No normal forwarding/copy path from that buffer
to the `AT+WEATHER` parser was identified. The independent DATA analysis found
the same peripheral and buffer separation.

## Consequence for the remaining readings

Sending `AT+?WEATHER` into the current host-to-ESP bridge is not supported by
these paths as a way to query the display. Even on its intended command side,
the response is the downloaded forecast, not indoor readings or stored rain
totals. Investigation of the separate `AT+DATA` handler and its binary payload
is the relevant next static path for live observations. Physical access to that
other interface, or a separately demonstrated software route to it, remains a
distinct question; these strings and traces alone do not establish one.
