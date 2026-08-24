#!/usr/bin/env python3
"""Build deterministic Android downloader runtimes from pinned Termux packages."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from native_runtime_elf import dependency_closure, metadata


REPOSITORY = "https://termux.net"
ARCH_BY_ABI = {
    "arm64-v8a": "aarch64",
    "armeabi-v7a": "arm",
    "x86_64": "x86_64",
}
ROOT_PACKAGES = {
    "python": ("python", "python-pycryptodomex", "ca-certificates"),
    "ffmpeg": ("ffmpeg",),
    "aria2c": ("aria2", "ca-certificates"),
}
MUTAGEN = {
    "version": "1.48.1",
    "url": (
        "https://files.pythonhosted.org/packages/47/d8/"
        "a29e4e3991765e7ce4ed1f7e4074fe1ba9da03e0048639734de60f9cadb9/"
        "mutagen-1.48.1-py3-none-any.whl"
    ),
    "sha256": "4f077fe87d3fc7fba259aa63d8c026b18382ca6a42ef37c61e16f1b1b5b82fe7",
}
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    filename: str
    sha256: str
    depends: str
    provides: tuple[str, ...]

    def lock_record(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "filename": self.filename,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RuntimeEntry:
    data: bytes
    mode: int
    symlink: bool = False
    package: str = ""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "youtubedl-android-builder/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def parse_control(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for paragraph in text.split("\n\n"):
        fields: dict[str, str] = {}
        current = ""
        for line in paragraph.splitlines():
            if line.startswith((" ", "\t")) and current:
                fields[current] += " " + line.strip()
            elif ":" in line:
                current, value = line.split(":", 1)
                fields[current] = value.strip()
        if fields:
            records.append(fields)
    return records


def package_catalog(arch: str) -> tuple[dict[str, Package], dict[str, str], str]:
    index_url = f"{REPOSITORY}/dists/stable/main/binary-{arch}/Packages.xz"
    compressed = fetch(index_url)
    import lzma

    records = parse_control(lzma.decompress(compressed).decode("utf-8"))
    packages: dict[str, Package] = {}
    providers: dict[str, str] = {}
    for record in records:
        if not all(key in record for key in ("Package", "Version", "Filename", "SHA256")):
            continue
        provided = tuple(
            item.strip().split()[0]
            for item in record.get("Provides", "").split(",")
            if item.strip()
        )
        package = Package(
            name=record["Package"],
            version=record["Version"],
            filename=record["Filename"],
            sha256=record["SHA256"].lower(),
            depends=" ".join(
                value for value in (record.get("Pre-Depends", ""), record.get("Depends", "")) if value
            ),
            provides=provided,
        )
        packages[package.name] = package
        for virtual in provided:
            providers.setdefault(virtual, package.name)
    return packages, providers, sha256(compressed)


def dependency_names(value: str) -> list[list[str]]:
    groups: list[list[str]] = []
    for group in value.split(","):
        choices: list[str] = []
        for choice in group.split("|"):
            name = re.sub(r"\s*\([^)]*\)", "", choice)
            name = re.sub(r"\s*\[[^]]*\]", "", name).strip().split(":", 1)[0]
            if name:
                choices.append(name)
        if choices:
            groups.append(choices)
    return groups


def resolve_packages(
    roots: tuple[str, ...], packages: dict[str, Package], providers: dict[str, str]
) -> list[Package]:
    resolved: dict[str, Package] = {}
    visiting: set[str] = set()

    def visit(requested: str) -> None:
        name = requested if requested in packages else providers.get(requested, "")
        if not name:
            raise ValueError(f"Termux package dependency is unavailable: {requested}")
        if name in resolved or name in visiting:
            return
        visiting.add(name)
        package = packages[name]
        for alternatives in dependency_names(package.depends):
            selected = next(
                (candidate for candidate in alternatives if candidate in packages or candidate in providers),
                None,
            )
            if selected is None:
                raise ValueError(f"No available alternative for {name}: {' | '.join(alternatives)}")
            visit(selected)
        visiting.remove(name)
        resolved[name] = package

    for root in roots:
        visit(root)
    return list(resolved.values())


def refresh_lock(path: Path, abis: list[str]) -> None:
    architectures: dict[str, object] = {}
    for abi in abis:
        arch = ARCH_BY_ABI[abi]
        packages, providers, index_hash = package_catalog(arch)
        groups = {
            role: [package.lock_record() for package in resolve_packages(roots, packages, providers)]
            for role, roots in ROOT_PACKAGES.items()
        }
        architectures[abi] = {
            "termuxArchitecture": arch,
            "packageIndexSha256": index_hash,
            "groups": groups,
        }
    document = {
        "schemaVersion": 1,
        "repository": REPOSITORY,
        "mutagen": MUTAGEN,
        "architectures": architectures,
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ar_member(archive: bytes, prefix: bytes) -> bytes:
    if not archive.startswith(b"!<arch>\n"):
        raise ValueError("Invalid Debian ar archive")
    offset = 8
    while offset + 60 <= len(archive):
        header = archive[offset : offset + 60]
        size = int(header[48:58].decode("ascii").strip())
        name = header[:16].rstrip().rstrip(b"/")
        data = archive[offset + 60 : offset + 60 + size]
        if name.startswith(prefix):
            return data
        offset += 60 + size + (size & 1)
    raise ValueError(f"Missing {prefix.decode()} member in Debian package")


def normalized_runtime_path(name: str) -> str | None:
    name = name.removeprefix("./")
    marker = "data/data/com.termux/files/"
    if not name.startswith(marker):
        return None
    relative = name[len(marker) :]
    path = PurePosixPath(relative)
    if not relative.startswith("usr/") or ".." in path.parts:
        return None
    return path.as_posix()


def selected_path(role: str, name: str) -> bool:
    if role == "python":
        selected = name.startswith("usr/lib/") or name.startswith("usr/etc/tls/")
    elif role == "ffmpeg":
        selected = name.startswith("usr/lib/") or name.startswith("usr/share/ffmpeg/")
    else:
        selected = name.startswith("usr/lib/") or name.startswith("usr/etc/tls/")
    if not selected:
        return False
    return not (
        name.endswith((".a", ".la", ".o", ".pyc"))
        or "/pkgconfig/" in name
        or "/cmake/" in name
        or "/__pycache__/" in name
    )


def normalize_link(member_name: str, link_name: str) -> str:
    marker = "data/data/com.termux/files/"
    target = link_name.removeprefix("./")
    if target.startswith("/"):
        target = target.removeprefix("/")
    if target.startswith(marker):
        target = target[len(marker) :]
        return os.path.relpath(target, PurePosixPath(member_name).parent).replace("\\", "/")
    return link_name


def merge_package(
    entries: dict[str, RuntimeEntry], package_data: bytes, role: str, package: str
) -> None:
    payload = ar_member(package_data, b"data.tar")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        for member in archive.getmembers():
            name = normalized_runtime_path(member.name)
            if name is None or not selected_path(role, name) or member.isdir():
                continue
            if member.issym() or member.islnk():
                link = normalize_link(name, member.linkname).encode("utf-8")
                entries[name] = RuntimeEntry(link, stat.S_IFLNK | 0o777, True, package)
            elif member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Could not read {member.name}")
                entries[name] = RuntimeEntry(
                    source.read(), stat.S_IFREG | (member.mode & 0o777), package=package
                )


def package_bytes(record: dict[str, str], cache: Path, repository: str) -> bytes:
    destination = cache / Path(record["filename"]).name
    if destination.is_file():
        data = destination.read_bytes()
        if sha256(data) == record["sha256"]:
            return data
    data = fetch(f"{repository}/{record['filename']}")
    actual = sha256(data)
    if actual != record["sha256"]:
        raise ValueError(f"Hash mismatch for {record['name']}: expected {record['sha256']}, got {actual}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return data


def merge_mutagen(entries: dict[str, RuntimeEntry], python_version: str, cache: Path) -> None:
    wheel = cache / Path(MUTAGEN["url"]).name
    if wheel.is_file() and sha256(wheel.read_bytes()) == MUTAGEN["sha256"]:
        data = wheel.read_bytes()
    else:
        data = fetch(MUTAGEN["url"])
        if sha256(data) != MUTAGEN["sha256"]:
            raise ValueError("Mutagen wheel hash mismatch")
        wheel.write_bytes(data)
    prefix = f"usr/lib/python{python_version}/site-packages/"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir() or not (info.filename.startswith("mutagen/") or ".dist-info/" in info.filename):
                continue
            entries[prefix + info.filename] = RuntimeEntry(
                archive.read(info), stat.S_IFREG | 0o644
            )


def write_zip(path: Path, entries: dict[str, RuntimeEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name in sorted(entries):
            entry = entries[name]
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.create_system = 3
            info.external_attr = entry.mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, entry.data)


def prune_runtime(
    entries: dict[str, RuntimeEntry],
    root_names: list[str],
    external_roots: list[bytes] | None = None,
    keep_non_elf: Callable[[str], bool] | None = None,
) -> dict[str, RuntimeEntry]:
    preliminary = dependency_closure(entries, root_names, external_roots)
    active_packages = {entries[name].package for name in preliminary if name in entries}
    # DT_NEEDED cannot describe modules discovered at runtime. Preserve nested
    # ELF modules only from packages already proven reachable, then close over
    # their dependencies. This retains plug-ins without reviving unrelated
    # language runtimes or optional toolchains from the package graph.
    module_roots = [
        name
        for name, entry in entries.items()
        if name.startswith("usr/lib/")
        and PurePosixPath(name).parent != PurePosixPath("usr/lib")
        and metadata(entry.data) is not None
        and entry.package in active_packages
    ]
    closure = dependency_closure(entries, [*root_names, *module_roots], external_roots)
    return {
        name: entry
        for name, entry in entries.items()
        if name in closure
        or (
            keep_non_elf is not None
            and keep_non_elf(name)
            and not entry.symlink
            and metadata(entry.data) is None
        )
    }


def python_version(entries: dict[str, RuntimeEntry]) -> str:
    versions = {
        match.group(1)
        for name in entries
        if (match := re.match(r"usr/lib/python(\d+\.\d+)/", name))
    }
    if len(versions) != 1:
        raise ValueError(f"Expected one Python runtime version, found {sorted(versions)}")
    return versions.pop()


def build_abi(
    repo: Path,
    lock: dict[str, object],
    abi: str,
    cache: Path,
    output: Path,
    ffmpeg_frontends: Path,
    native_overrides: Path,
) -> None:
    architecture = lock["architectures"][abi]
    repository = str(lock["repository"])
    built: dict[str, dict[str, RuntimeEntry]] = {}
    raw_packages: dict[str, bytes] = {}
    for role, records in architecture["groups"].items():
        entries: dict[str, RuntimeEntry] = {}
        for record in records:
            data = package_bytes(record, cache / abi, repository)
            raw_packages[record["name"]] = data
            merge_package(entries, data, role, record["name"])
        built[role] = entries

    libxml2_package = native_overrides / abi / "libxml2.deb"
    if not libxml2_package.is_file():
        raise ValueError(f"Missing ICU-free libxml2 package: {libxml2_package}")
    for role in ("ffmpeg", "aria2c"):
        built[role] = {
            name: entry
            for name, entry in built[role].items()
            if not PurePosixPath(name).name.startswith("libxml2.so")
        }
        merge_package(built[role], libxml2_package.read_bytes(), role, "libxml2")

    version = python_version(built["python"])
    merge_mutagen(built["python"], version, cache / abi)
    python_roots = [
        name
        for name, entry in built["python"].items()
        if not entry.symlink
        and metadata(entry.data) is not None
        and (name.startswith(f"usr/lib/python{version}/") or name.startswith("usr/lib/libpython"))
    ]
    built["python"] = prune_runtime(built["python"], python_roots, keep_non_elf=lambda _name: True)
    for binary in ("ffmpeg", "ffprobe"):
        frontend = ffmpeg_frontends / abi / binary
        if not frontend.is_file():
            raise ValueError(f"Missing -rdynamic FFmpeg frontend: {frontend}")
        verify_dynamic_main(frontend)
        built["ffmpeg"][f"usr/lib/lib{binary}_real.so"] = RuntimeEntry(
            frontend.read_bytes(), stat.S_IFREG | 0o755, package="ffmpeg"
        )

    built["ffmpeg"] = prune_runtime(
        built["ffmpeg"],
        ["usr/lib/libffmpeg_real.so", "usr/lib/libffprobe_real.so"],
    )

    runtime_root = output / abi
    write_zip(runtime_root / "libpython.zip.so", built["python"])
    write_zip(runtime_root / "libffmpeg.zip.so", built["ffmpeg"])
    aria_binary = find_regular_from_deb(raw_packages["aria2"], "/usr/bin/aria2c")
    built["aria2c"] = prune_runtime(
        built["aria2c"], [], [aria_binary], keep_non_elf=lambda name: name.startswith("usr/etc/tls/")
    )
    write_zip(runtime_root / "libaria2c.zip.so", built["aria2c"])
    (runtime_root / "libaria2c.so").write_bytes(aria_binary)
    compile_launchers(repo, abi, runtime_root)
    print(
        f"{abi}: Python {version}, "
        f"{len(built['python'])} Python entries, {len(built['ffmpeg'])} FFmpeg entries, "
        f"{len(built['aria2c'])} aria2 entries"
    )


def find_regular_from_deb(package_data: bytes, suffix: str) -> bytes:
    payload = ar_member(package_data, b"data.tar")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        matches = [item for item in archive.getmembers() if item.isfile() and item.name.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f"Expected one {suffix}, found {len(matches)}")
        source = archive.extractfile(matches[0])
        if source is None:
            raise ValueError(f"Could not read {suffix}")
        return source.read()


def ndk_root() -> Path:
    configured = os.environ.get("ANDROID_NDK_HOME") or os.environ.get("ANDROID_NDK_ROOT")
    if configured:
        return Path(configured)
    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if android_home:
        candidates = sorted((Path(android_home) / "ndk").glob("*"), reverse=True)
        if candidates:
            return candidates[0]
    raise ValueError("ANDROID_NDK_HOME, ANDROID_NDK_ROOT, or an installed Android NDK is required")


def ndk_tool(name: str) -> Path:
    host = "windows-x86_64" if os.name == "nt" else "linux-x86_64"
    suffix = ".exe" if os.name == "nt" else ""
    return ndk_root() / "toolchains" / "llvm" / "prebuilt" / host / "bin" / f"{name}{suffix}"


def verify_dynamic_main(binary: Path) -> None:
    result = subprocess.run(
        [str(ndk_tool("llvm-readelf")), "--dyn-syms", str(binary)],
        check=True,
        capture_output=True,
        text=True,
    )
    if not re.search(r"\bmain\s*$", result.stdout, re.MULTILINE):
        raise ValueError(f"FFmpeg frontend does not export main: {binary}")


def compile_launchers(repo: Path, abi: str, output: Path) -> None:
    host = "windows-x86_64" if os.name == "nt" else "linux-x86_64"
    triple = {
        "arm64-v8a": "aarch64-linux-android24",
        "armeabi-v7a": "armv7a-linux-androideabi24",
        "x86_64": "x86_64-linux-android24",
    }[abi]
    compiler = ndk_root() / "toolchains" / "llvm" / "prebuilt" / host / "bin" / f"{triple}-clang"
    if os.name == "nt":
        compiler = compiler.with_suffix(".cmd")
    common = [
        str(compiler), "-fPIE", "-pie", "-O2", "-s", "-ldl",
        "-Wl,-z,max-page-size=16384", "-Wl,-z,common-page-size=16384",
    ]
    sources = {
        "libpython.so": repo / "library/src/main/jniLibs/python_launcher.c",
        "libffmpeg.so": repo / "ffmpeg/src/main/jniLibs/ffmpeg_wrapper.c",
        "libffprobe.so": repo / "ffmpeg/src/main/jniLibs/ffprobe_wrapper.c",
    }
    for name, source in sources.items():
        subprocess.run(common + [str(source), "-o", str(output / name)], check=True)


def install_outputs(repo: Path, output: Path, abis: list[str]) -> None:
    destinations = {
        "libpython.so": repo / "library/src/main/jniLibs",
        "libpython.zip.so": repo / "library/src/main/jniLibs",
        "libffmpeg.so": repo / "ffmpeg/src/main/jniLibs",
        "libffprobe.so": repo / "ffmpeg/src/main/jniLibs",
        "libffmpeg.zip.so": repo / "ffmpeg/src/main/jniLibs",
        "libaria2c.so": repo / "aria2c/src/main/jniLibs",
        "libaria2c.zip.so": repo / "aria2c/src/main/jniLibs",
    }
    for abi in abis:
        for source in (output / abi).iterdir():
            destination = destinations[source.name] / abi / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--abi", action="append", choices=sorted(ARCH_BY_ABI))
    parser.add_argument("--refresh-lock", action="store_true")
    parser.add_argument("--refresh-lock-only", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--ffmpeg-frontends", type=Path)
    parser.add_argument("--native-overrides", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    lock_path = repo / "native-runtime.lock.json"
    abis = args.abi or list(ARCH_BY_ABI)
    if args.refresh_lock or args.refresh_lock_only:
        refresh_lock(lock_path, abis)
    if args.refresh_lock_only:
        return
    if args.ffmpeg_frontends is None or args.native_overrides is None:
        parser.error("--ffmpeg-frontends and --native-overrides are required when building")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    cache = repo / "build/native-runtime-cache"
    output = repo / "build/native-runtime"
    cache.mkdir(parents=True, exist_ok=True)
    for abi in abis:
        build_abi(
            repo,
            lock,
            abi,
            cache,
            output,
            args.ffmpeg_frontends.resolve(),
            args.native_overrides.resolve(),
        )
    if args.install:
        install_outputs(repo, output, abis)


if __name__ == "__main__":
    main()
