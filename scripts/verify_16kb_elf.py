#!/usr/bin/env python3
"""Verify 64-bit ELF LOAD alignment, including ELF files hidden in runtime ZIPs."""

from __future__ import annotations

import argparse
import io
import struct
import zipfile
from pathlib import Path


ELF_MAGIC = b"\x7fELF"
PT_LOAD = 1
SUPPORTED_64_BIT_MACHINES = {62, 183}  # x86_64, AArch64


def load_alignments(data: bytes) -> list[int] | None:
    if len(data) < 64 or data[:4] != ELF_MAGIC or data[5] != 1:
        return None
    elf_class = data[4]
    if elf_class == 2:
        machine = struct.unpack_from("<H", data, 18)[0]
        if machine not in SUPPORTED_64_BIT_MACHINES:
            return None
        offset = struct.unpack_from("<Q", data, 32)[0]
        entry_size = struct.unpack_from("<H", data, 54)[0]
        entry_count = struct.unpack_from("<H", data, 56)[0]
        align_offset, align_format = 48, "<Q"
    else:
        return None
    alignments: list[int] = []
    for index in range(entry_count):
        start = offset + index * entry_size
        if start + entry_size > len(data):
            raise ValueError("ELF program header table exceeds file size")
        if struct.unpack_from("<I", data, start)[0] == PT_LOAD:
            alignments.append(struct.unpack_from(align_format, data, start + align_offset)[0])
    return alignments


def verify_elf(label: str, data: bytes, failures: list[str]) -> int:
    alignments = load_alignments(data)
    if alignments is None:
        return 0
    if not alignments or min(alignments) < 16384:
        failures.append(f"{label}: LOAD alignments {alignments or 'missing'}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    roots = args.paths or [
        repo / "library/src/main/jniLibs",
        repo / "ffmpeg/src/main/jniLibs",
        repo / "aria2c/src/main/jniLibs",
    ]
    failures: list[str] = []
    checked = 0
    paths = sorted(
        path
        for root in roots
        for path in ([root] if root.is_file() else root.rglob("*.so"))
    )
    for path in paths:
        relative = path.resolve().relative_to(repo).as_posix() if path.resolve().is_relative_to(repo) else str(path)
        if path.name.endswith(".zip.so"):
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    checked += verify_elf(f"{relative}!/{info.filename}", archive.read(info), failures)
        else:
            checked += verify_elf(relative, path.read_bytes(), failures)
    if failures:
        print("16 KB ELF alignment failures:")
        for failure in failures:
            print(f"  {failure}")
        raise SystemExit(1)
    print(f"Verified {checked} 64-bit ELF files with 16 KB LOAD alignment")


if __name__ == "__main__":
    main()
