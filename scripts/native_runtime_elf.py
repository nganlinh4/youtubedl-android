"""Minimal ELF dependency analysis for native runtime packaging."""

from __future__ import annotations

import posixpath
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol


PT_LOAD = 1
PT_DYNAMIC = 2
DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
DT_STRSZ = 10
DT_SONAME = 14
SYSTEM_LIBRARIES = {
    "libEGL.so",
    "libGLESv2.so",
    "libOpenSLES.so",
    "libaaudio.so",
    "libandroid.so",
    "libcamera2ndk.so",
    "libc.so",
    "libdl.so",
    "libjnigraphics.so",
    "liblog.so",
    "libm.so",
    "libmediandk.so",
    "libnativewindow.so",
    "libvulkan.so",
}


class Entry(Protocol):
    data: bytes
    symlink: bool


@dataclass(frozen=True)
class ElfMetadata:
    soname: str | None
    needed: tuple[str, ...]


def _cstring(data: bytes, offset: int, limit: int) -> str:
    if offset < 0 or offset >= limit:
        raise ValueError("ELF string offset exceeds the dynamic string table")
    end = data.find(b"\0", offset, limit)
    if end < 0:
        raise ValueError("ELF dynamic string is unterminated")
    return data[offset:end].decode("utf-8")


def metadata(data: bytes) -> ElfMetadata | None:
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[5] != 1:
        return None
    elf_class = data[4]
    if elf_class == 2:
        if len(data) < 64:
            return None
        ph_offset = struct.unpack_from("<Q", data, 32)[0]
        ph_size = struct.unpack_from("<H", data, 54)[0]
        ph_count = struct.unpack_from("<H", data, 56)[0]
        dynamic_entry = "<qQ"
        dynamic_size = 16
        load_fields = "<IIQQQQQQ"
    elif elf_class == 1:
        ph_offset = struct.unpack_from("<I", data, 28)[0]
        ph_size = struct.unpack_from("<H", data, 42)[0]
        ph_count = struct.unpack_from("<H", data, 44)[0]
        dynamic_entry = "<iI"
        dynamic_size = 8
        load_fields = "<IIIIIIII"
    else:
        return None
    loads: list[tuple[int, int, int]] = []
    dynamic: tuple[int, int] | None = None
    for index in range(ph_count):
        start = ph_offset + index * ph_size
        if start + ph_size > len(data):
            raise ValueError("ELF program header table exceeds file size")
        fields = struct.unpack_from(load_fields, data, start)
        segment_type = fields[0]
        if elf_class == 2:
            file_offset, virtual_address, file_size = fields[2], fields[3], fields[5]
        else:
            file_offset, virtual_address, file_size = fields[1], fields[2], fields[4]
        if segment_type == PT_LOAD:
            loads.append((virtual_address, file_offset, file_size))
        elif segment_type == PT_DYNAMIC:
            dynamic = (file_offset, file_size)
    if dynamic is None:
        return ElfMetadata(None, ())
    values: dict[int, list[int]] = {}
    dynamic_offset, dynamic_length = dynamic
    for offset in range(dynamic_offset, dynamic_offset + dynamic_length, dynamic_size):
        if offset + dynamic_size > len(data):
            raise ValueError("ELF dynamic table exceeds file size")
        tag, value = struct.unpack_from(dynamic_entry, data, offset)
        if tag == DT_NULL:
            break
        values.setdefault(tag, []).append(value)
    if DT_STRTAB not in values:
        return ElfMetadata(None, ())
    string_address = values[DT_STRTAB][0]
    string_size = values.get(DT_STRSZ, [0])[0]
    string_offset = next(
        (
            file_offset + string_address - virtual_address
            for virtual_address, file_offset, file_size in loads
            if virtual_address <= string_address < virtual_address + file_size
        ),
        None,
    )
    if string_offset is None:
        raise ValueError("ELF dynamic string table is outside LOAD segments")
    string_limit = min(len(data), string_offset + string_size) if string_size else len(data)
    needed = tuple(_cstring(data, string_offset + value, string_limit) for value in values.get(DT_NEEDED, []))
    soname_values = values.get(DT_SONAME, [])
    soname = _cstring(data, string_offset + soname_values[0], string_limit) if soname_values else None
    return ElfMetadata(soname, needed)


def _symlink_target(name: str, target: str) -> str:
    if target.startswith("/"):
        normalized = target.removeprefix("/")
    else:
        normalized = posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent), target))
    if normalized.startswith("../") or normalized == "..":
        raise ValueError(f"Runtime symlink escapes the archive: {name} -> {target}")
    return normalized


def dependency_closure(
    entries: Mapping[str, Entry], root_names: list[str], external_roots: list[bytes] | None = None
) -> set[str]:
    by_basename: dict[str, list[str]] = {}
    for name in entries:
        by_basename.setdefault(PurePosixPath(name).name, []).append(name)
    kept: set[str] = set()
    queue: list[bytes] = list(external_roots or [])

    def keep(name: str) -> None:
        if name in kept:
            return
        entry = entries[name]
        kept.add(name)
        if entry.symlink:
            target = _symlink_target(name, entry.data.decode("utf-8"))
            if target not in entries:
                raise ValueError(f"Runtime symlink target is missing: {name} -> {target}")
            keep(target)
        else:
            queue.append(entry.data)

    for root in root_names:
        if root not in entries:
            raise ValueError(f"Runtime dependency root is missing: {root}")
        keep(root)
    while queue:
        details = metadata(queue.pop())
        if details is None:
            continue
        for needed in details.needed:
            candidates = by_basename.get(needed, [])
            if not candidates:
                if needed in SYSTEM_LIBRARIES:
                    continue
                raise ValueError(f"Runtime dependency is unavailable: {needed}")
            preferred = next((name for name in candidates if name == f"usr/lib/{needed}"), candidates[0])
            keep(preferred)
    return kept
