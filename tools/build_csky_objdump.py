#!/usr/bin/env python3
"""Build GNU's C-Sky disassembler in registered scratch; never run device code.

Purpose: inspect the decoded public helper firmware. No device effect.
Keep this source and compact findings; retire the downloaded source/build tree
through dread-storage after the investigation, retaining the tool if still used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

URL = "https://ftp.gnu.org/gnu/binutils/binutils-2.45.tar.xz"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    archive = root / "binutils-2.45.tar.xz"
    with urllib.request.urlopen(URL, timeout=30) as response, archive.open("xb") as dest:
        remaining = 30_000_000
        while chunk := response.read(min(65536, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                raise ValueError("source archive exceeds expected limit")
            dest.write(chunk)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as source:
        source.extractall(root, filter="data")
    build = root / "build"
    build.mkdir()
    with (root / "build.log").open("w") as log:
        subprocess.run([str(root / "binutils-2.45" / "configure"), "--target=csky-elf",
                        "--disable-nls", "--disable-werror", "--disable-shared",
                        "--disable-gdb", "--disable-gdbserver", "--disable-gprof",
                        "--disable-gprofng", "--disable-gas", "--disable-ld",
                        "--without-debuginfod", "CFLAGS=-O2"], cwd=build,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(["make", "-j12", "all-binutils"], cwd=build,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    receipt = {"source": URL, "source_sha256": digest, "target": "csky-elf",
               "objdump": str(build / "binutils" / "objdump")}
    (root / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
