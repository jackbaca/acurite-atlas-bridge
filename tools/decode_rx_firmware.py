#!/usr/bin/env python3
"""Decode the public Atlas receiver helper image without executing it.

Purpose: static protocol/architecture analysis. The caller must place derived
output in a registered storage job. No device interfaces are accessed. Original
and decoded images remain research artifacts, never installation instructions.
Retain source and evidence; dispose of derived output with its registered job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct


V020_SHA256 = "eb6b5ffe039e77e22ccedbd5f7b6c4ae7239bffe87ec642c1d312177d4d6288b"
V020_CHECKSUM = 250130783
V020_LENGTH = 30720
XOR_MASK = 0xA35C9A96
ADDEND = 0x20201905
FLASH_BASE = 0x800  # Inferred from reset vector 0x910 and startup at file 0x110.


def words(data: bytes):
    if not data or len(data) % 4:
        raise ValueError("image must contain complete little-endian 32-bit words")
    return struct.iter_unpack("<I", data)


def additive_checksum(data: bytes) -> int:
    return sum(word for (word,) in words(data)) & 0xFFFFFFFF


def decode_words(data: bytes) -> bytes:
    """Inverse of encoded_word = (plain_word + ADDEND) XOR XOR_MASK."""
    return b"".join(struct.pack("<I", ((word ^ XOR_MASK) - ADDEND) & 0xFFFFFFFF)
                    for (word,) in words(data))


def validate_v020(data: bytes) -> None:
    if len(data) != V020_LENGTH or hashlib.sha256(data).hexdigest() != V020_SHA256:
        raise ValueError("input does not match the verified public v020 image")
    if additive_checksum(data) != V020_CHECKSUM:
        raise ValueError("input does not match the manifest additive checksum")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # Bound reads before hash validation. A different image requires a separate,
    # reviewed identity rather than silently applying this v020-specific decode.
    with args.input.open("rb") as source:
        data = source.read(V020_LENGTH + 1)
    validate_v020(data)
    decoded = decode_words(data)
    with args.output.open("xb") as destination:
        destination.write(decoded)
    print(json.dumps({"input_sha256": V020_SHA256, "input_bytes": len(data),
                      "input_additive_checksum": additive_checksum(data),
                      "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
                      "inferred_flash_base": FLASH_BASE,
                      "reset_vector": struct.unpack_from("<I", decoded)[0]}, sort_keys=True))


if __name__ == "__main__":
    main()
