#!/usr/bin/env python3
"""Extract the pinned Android QuickJS launcher with an app-local RUNPATH."""

from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path

from build_native_runtime import ar_member

PACKAGE_URL = (
    "https://termux.net/debs/stable/aarch64/q/quickjs-ng/"
    "quickjs-ng_0.15.1_aarch64.deb"
)
PACKAGE_SHA256 = "dc2d312e17c3fb3d522e91d1122a237bee66dfd41b875379ed357156e4df7f4c"
TERMUX_RUNPATH = b"/data/data/com.termux/files/usr/lib\0"
APP_RUNPATH = b"$ORIGIN\0" + b"\0" * (len(TERMUX_RUNPATH) - len(b"$ORIGIN\0"))


def fetch_package(cache: Path) -> bytes:
    if cache.is_file():
        data = cache.read_bytes()
        if hashlib.sha256(data).hexdigest() == PACKAGE_SHA256:
            return data
    request = urllib.request.Request(PACKAGE_URL, headers={"User-Agent": "youtubedl-android-builder/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != PACKAGE_SHA256:
        raise ValueError("QuickJS package hash mismatch")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    return data


def member(archive: tarfile.TarFile, suffix: str) -> bytes:
    entry = next((item for item in archive.getmembers() if item.name.endswith(suffix)), None)
    if entry is None:
        raise ValueError(f"QuickJS package is missing {suffix}")
    source = archive.extractfile(entry)
    if source is None:
        raise ValueError(f"Could not read {entry.name}")
    return source.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    payload = ar_member(fetch_package(args.cache), b"data.tar")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        runner = member(archive, "/bin/qjs")
        if runner.count(TERMUX_RUNPATH) != 1:
            raise ValueError("QuickJS launcher has an unexpected RUNPATH")
        runner = runner.replace(TERMUX_RUNPATH, APP_RUNPATH)
        engine = member(archive, "/lib/libqjs.so")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "libqjs_runner.so").write_bytes(runner)
    (args.output / "libqjs.so").write_bytes(engine)


if __name__ == "__main__":
    main()
