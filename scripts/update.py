#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Safely check for, and update to, official Manggo Debian releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


API = "https://manggo.pylogmon.cn/api/v1/public/homepage?locale=en"
RELEASE = "https://gitcode.com/Pylogmon/Manggo/releases/download/{}/manggo_{}_{}.deb"
ROOT = Path(__file__).resolve().parents[1]
PKGBUILD = ROOT / "PKGBUILD"
SRCINFO = ROOT / ".SRCINFO"
ARCHES = {"x86_64": ("x64", "amd64"), "aarch64": ("arm64", "arm64")}


class UpdateError(RuntimeError):
    pass


def command(*args: str, input: bytes | None = None, cwd: Path | None = None) -> bytes:
    try:
        result = subprocess.run(args, input=input, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False, cwd=cwd)
    except OSError as error:
        raise UpdateError(f"could not run {args[0]}: {error}") from error
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise UpdateError(f"{' '.join(args)} failed: {detail}")
    return result.stdout


def package_value(text: str, name: str) -> str:
    found = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    if not found:
        raise UpdateError(f"PKGBUILD does not define {name}")
    return found.group(1).strip().strip("'\"")


def version_cmp(left: str, right: str) -> int:
    for version in (left, right):
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise UpdateError(f"unsupported stable version: {version}")
    first = tuple(map(int, left.split(".")))
    second = tuple(map(int, right.split(".")))
    return (first > second) - (first < second)


def releases() -> tuple[str, dict[str, str]]:
    request = urllib.request.Request(API, headers={"User-Agent": "manggo-aur-update/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            document = json.load(response)
    except (OSError, json.JSONDecodeError) as error:
        raise UpdateError(f"could not retrieve Manggo homepage data: {error}") from error
    try:
        sections = document["data"]["sections"]
        if not isinstance(sections, list) or not all(isinstance(s, dict) for s in sections):
            raise UpdateError("unexpected official homepage sections")
        download = next(section["download"] for section in sections
                        if section.get("type") == "download")
        items = download["items"]
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise UpdateError("unexpected official download items")
    except (KeyError, StopIteration, TypeError) as error:
        raise UpdateError("unexpected official homepage API schema") from error

    selected: dict[str, tuple[str, str]] = {}
    for arch, (site_arch, deb_arch) in ARCHES.items():
        matches = [item for item in items if item.get("platform") == "Linux"
                   and item.get("architecture") == site_arch
                   and item.get("systemRequirements") == "deb"]
        if len(matches) != 1:
            raise UpdateError(f"expected exactly one Linux/{site_arch}/deb release")
        item = matches[0]
        version = str(item.get("version", "")).removeprefix("v")
        target = item.get("target")
        if not isinstance(target, dict):
            raise UpdateError(f"invalid {site_arch} download target")
        url = target.get("externalUrl")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or not isinstance(url, str):
            raise UpdateError(f"invalid {site_arch} release metadata")
        expected = RELEASE.format(version, version, deb_arch)
        if url != expected:
            raise UpdateError(f"official {site_arch} URL differs from the expected Gitcode release URL")
        selected[arch] = (version, url)
    versions = {item[0] for item in selected.values()}
    if len(versions) != 1:
        raise UpdateError("official x64 and arm64 Debian releases have different versions")
    return versions.pop(), {arch: item[1] for arch, item in selected.items()}


def download_and_verify(url: str, arch: str, version: str, directory: Path) -> str:
    path = directory / f"manggo_{arch}.deb"
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as response, path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
    except OSError as error:
        raise UpdateError(f"could not download {url}: {error}") from error
    files = command("bsdtar", "-tf", str(path)).decode().splitlines()
    control_archive = next((name for name in files
                            if name.removeprefix("./").startswith("control.tar.")), None)
    if not control_archive:
        raise UpdateError(f"{path.name} is not a Debian archive with control metadata")
    control_tar = command("bsdtar", "-xOf", str(path), control_archive)
    control = command("bsdtar", "-xOf", "-", "./control", input=control_tar).decode()
    fields = {key: value.strip() for key, value in
              (line.split(":", 1) for line in control.splitlines()
               if ":" in line and not line[0].isspace())}
    wanted = ARCHES[arch][1]
    if (fields.get("Package") != "manggo" or fields.get("Architecture") != wanted
            or fields.get("Version") != version):
        raise UpdateError(f"{path.name} control metadata is not {wanted} version {version}")
    return digest.hexdigest()


def replace_line(text: str, name: str, value: str) -> str:
    updated, count = re.subn(rf"^{re.escape(name)}=.*$", f"{name}={value}", text,
                             count=1, flags=re.MULTILINE)
    if count != 1:
        raise UpdateError(f"PKGBUILD does not contain a replaceable {name}")
    return updated


def atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            temporary.chmod(path.stat().st_mode & 0o777)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def update(current: str, latest: str, urls: dict[str, str], original: str) -> None:
    with tempfile.TemporaryDirectory(prefix="manggo-aur-") as temporary:
        hashes = {arch: download_and_verify(url, arch, latest, Path(temporary))
                  for arch, url in urls.items()}
    current_hashes = {arch: package_value(original, f"sha256sums_{arch}")
                      .removeprefix("(").removesuffix(")").strip("'\"")
                      for arch in ARCHES}
    if latest == current:
        if hashes != current_hashes:
            raise UpdateError("same-version artifacts differ from pinned hashes; refusing to repin")
        print(f"Manggo {current} is already current; checksums match.")
        return
    changed = replace_line(replace_line(original, "pkgver", latest), "pkgrel", "1")
    for arch, digest in hashes.items():
        changed = replace_line(changed, f"sha256sums_{arch}", f"('{digest}')")
    previous_srcinfo = SRCINFO.read_text()
    atomic_write(PKGBUILD, changed)
    try:
        generated = command("makepkg", "--printsrcinfo", cwd=ROOT).decode()
        atomic_write(SRCINFO, generated)
    except Exception:
        atomic_write(PKGBUILD, original)
        atomic_write(SRCINFO, previous_srcinfo)
        raise
    print(f"Updated Manggo {current} -> {latest}; regenerated .SRCINFO.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="only query and validate release metadata")
    mode.add_argument("--update", action="store_true", help="download, validate, and update package metadata")
    parser.add_argument("--github-output", type=Path,
                        help="append changed/latest outputs for GitHub Actions")
    args = parser.parse_args()
    try:
        original = PKGBUILD.read_text()
        current = package_value(original, "pkgver")
        latest, urls = releases()
        comparison = version_cmp(latest, current)
        print(f"current: {current}\nlatest:  {latest}")
        if comparison < 0:
            raise UpdateError("official release is older than the packaged version")
        if not args.update:
            print("new release available" if comparison > 0 else "already current")
        else:
            update(current, latest, urls, original)
        if args.github_output:
            with args.github_output.open("a") as output:
                output.write(f"changed={'true' if comparison > 0 else 'false'}\nlatest={latest}\n")
        return 0
    except (UpdateError, OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
