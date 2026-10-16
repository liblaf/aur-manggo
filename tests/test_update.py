#!/usr/bin/env python3
"""Offline regression tests for the Manggo release updater."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import tarfile
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "scripts" / "update.py"
SPEC = importlib.util.spec_from_file_location("manggo_update_tested", SCRIPT)
assert SPEC and SPEC.loader
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


def next_version(version: str) -> str:
    """Return a newer valid three-component release without pinning a version."""
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def homepage(version: str, *, arm_version: str | None = None,
             x64_url: str | None = None, duplicate_x64: bool = False,
             invalid_target: bool = False) -> dict[str, object]:
    arm_version = arm_version or version

    def item(architecture: str, item_version: str, deb_arch: str) -> dict[str, object]:
        url = updater.RELEASE.format(item_version, item_version, deb_arch)
        if architecture == "x64" and x64_url is not None:
            url = x64_url
        target: object = "not-an-object" if invalid_target else {"externalUrl": url}
        return {
            "platform": "Linux",
            "architecture": architecture,
            "systemRequirements": "deb",
            "version": f"v{item_version}",
            "target": target,
        }

    items = [item("x64", version, "amd64"), item("arm64", arm_version, "arm64")]
    if duplicate_x64:
        items.append(item("x64", version, "amd64"))
    return {"data": {"sections": [{"type": "download", "download": {"items": items}}]}}


def ar_member(name: str, content: bytes) -> bytes:
    """Build one System V ar member; enough for a synthetic Debian archive."""
    header = (
        f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{0o100644:<8}{len(content):<10}`\n"
    ).encode("ascii")
    return header + content + (b"\n" if len(content) % 2 else b"")


def control_tar(control: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        content = control.encode()
        info = tarfile.TarInfo("./control")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def deb(control: str) -> bytes:
    empty_data = io.BytesIO()
    with tarfile.open(fileobj=empty_data, mode="w:gz"):
        pass
    return b"!<arch>\n" + b"".join((
        ar_member("debian-binary", b"2.0\n"),
        ar_member("control.tar.gz", control_tar(control)),
        ar_member("data.tar.gz", empty_data.getvalue()),
    ))


@contextmanager
def package_tree():
    """Use a temporary package tree while preserving the imported updater globals."""
    with tempfile.TemporaryDirectory(prefix="manggo-update-test-") as temporary:
        root = Path(temporary)
        for name in ("PKGBUILD", ".SRCINFO"):
            shutil.copy2(PACKAGE / name, root / name)
        previous = updater.ROOT, updater.PKGBUILD, updater.SRCINFO
        updater.ROOT, updater.PKGBUILD, updater.SRCINFO = root, root / "PKGBUILD", root / ".SRCINFO"
        try:
            yield root
        finally:
            updater.ROOT, updater.PKGBUILD, updater.SRCINFO = previous


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_comparison_is_semantic_for_three_components(self) -> None:
        self.assertLess(updater.version_cmp("1.2.9", "1.2.10"), 0)
        self.assertEqual(updater.version_cmp("7.4.3", "7.4.3"), 0)
        self.assertGreater(updater.version_cmp("10.0.0", "9.999.999"), 0)

    def test_releases_accepts_expected_metadata(self) -> None:
        version = next_version(updater.package_value((PACKAGE / "PKGBUILD").read_text(), "pkgver"))
        payload = json.dumps(homepage(version)).encode()
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(payload)):
            actual, urls = updater.releases()
        self.assertEqual(actual, version)
        self.assertEqual(urls["x86_64"], updater.RELEASE.format(version, version, "amd64"))
        self.assertEqual(urls["aarch64"], updater.RELEASE.format(version, version, "arm64"))

    def test_releases_rejects_invalid_target_divergence_url_and_duplicate(self) -> None:
        current = updater.package_value((PACKAGE / "PKGBUILD").read_text(), "pkgver")
        future = next_version(current)
        payloads = (
            homepage(future, invalid_target=True),
            homepage(future, arm_version=next_version(future)),
            homepage(future, x64_url="https://example.invalid/manggo.deb"),
            homepage(future, duplicate_x64=True),
        )
        for payload in payloads:
            with self.subTest(payload=payload), mock.patch.object(
                updater.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())
            ):
                with self.assertRaises(updater.UpdateError):
                    updater.releases()


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = updater.package_value((PACKAGE / "PKGBUILD").read_text(), "pkgver")
        self.future = next_version(self.current)
        self.urls = {arch: f"https://example.invalid/{arch}.deb" for arch in updater.ARCHES}
        self.hashes = {"x86_64": "a" * 64, "aarch64": "b" * 64}

    def downloader(self, expected_version: str, values: dict[str, str]):
        def fake(url: str, arch: str, version: str, directory: Path) -> str:
            self.assertEqual(version, expected_version)
            self.assertEqual(url, self.urls[arch])
            return values[arch]
        return fake

    @unittest.skipUnless(shutil.which("makepkg"), "requires Arch makepkg")
    def test_newer_metadata_regenerates_srcinfo_from_an_arbitrary_cwd(self) -> None:
        with package_tree() as root, tempfile.TemporaryDirectory() as elsewhere:
            with mock.patch.object(updater, "download_and_verify", self.downloader(self.future, self.hashes)):
                previous_cwd = Path.cwd()
                try:
                    os.chdir(elsewhere)
                    updater.update(self.current, self.future, self.urls, updater.PKGBUILD.read_text())
                finally:
                    os.chdir(previous_cwd)
            pkgbuild = (root / "PKGBUILD").read_text()
            self.assertIn(f"pkgver={self.future}", pkgbuild)
            self.assertIn("pkgrel=1", pkgbuild)
            self.assertTrue(all(digest in pkgbuild for digest in self.hashes.values()))
            self.assertIn(f"pkgver = {self.future}", (root / ".SRCINFO").read_text())

    def test_metadata_generation_failure_rolls_back_both_files(self) -> None:
        with package_tree() as root:
            before = tuple((root / name).read_bytes() for name in ("PKGBUILD", ".SRCINFO"))
            with mock.patch.object(updater, "download_and_verify", self.downloader(self.future, self.hashes)), \
                 mock.patch.object(updater, "command", side_effect=updater.UpdateError("makepkg failed")):
                with self.assertRaisesRegex(updater.UpdateError, "makepkg failed"):
                    updater.update(self.current, self.future, self.urls, updater.PKGBUILD.read_text())
            self.assertEqual(before, tuple((root / name).read_bytes() for name in ("PKGBUILD", ".SRCINFO")))

    def test_same_version_checksum_drift_refuses_without_writing(self) -> None:
        drifted = {"x86_64": "c" * 64, "aarch64": "d" * 64}
        with package_tree() as root:
            before = tuple((root / name).read_bytes() for name in ("PKGBUILD", ".SRCINFO"))
            with mock.patch.object(updater, "download_and_verify", self.downloader(self.current, drifted)):
                with self.assertRaisesRegex(updater.UpdateError, "refusing to repin"):
                    updater.update(self.current, self.current, self.urls, updater.PKGBUILD.read_text())
            self.assertEqual(before, tuple((root / name).read_bytes() for name in ("PKGBUILD", ".SRCINFO")))


@unittest.skipUnless(shutil.which("bsdtar"), "requires bsdtar")
class DebianControlTests(unittest.TestCase):
    def test_control_package_version_and_architecture_are_checked(self) -> None:
        version = next_version(updater.package_value((PACKAGE / "PKGBUILD").read_text(), "pkgver"))
        cases = (
            ("x86_64", "manggo", version, "amd64", True),
            ("x86_64", "other", version, "amd64", False),
            ("x86_64", "manggo", next_version(version), "amd64", False),
            ("aarch64", "manggo", version, "amd64", False),
        )
        for arch, package, control_version, control_arch, valid in cases:
            content = deb(
                f"Package: {package}\nVersion: {control_version}\nArchitecture: {control_arch}\n"
            )
            with self.subTest(arch=arch, package=package, version=control_version, control_arch=control_arch), \
                 tempfile.TemporaryDirectory() as directory, \
                 mock.patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(content)):
                if valid:
                    self.assertEqual(
                        updater.download_and_verify("https://example.invalid/manggo.deb", arch, version, Path(directory)),
                        hashlib.sha256(content).hexdigest(),
                    )
                else:
                    with self.assertRaises(updater.UpdateError):
                        updater.download_and_verify("https://example.invalid/manggo.deb", arch, version, Path(directory))


if __name__ == "__main__":
    unittest.main(verbosity=2)
