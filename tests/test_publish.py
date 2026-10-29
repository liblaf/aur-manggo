"""Integration tests for the guarded AUR publisher."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "publish.py"
SPEC = importlib.util.spec_from_file_location("publish", SCRIPT)
publish = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(publish)


PKG = """pkgname=manggo
pkgver=1.0.0
pkgrel=1
sha256sums_x86_64=('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
sha256sums_aarch64=('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')
"""
SRC = """pkgbase = manggo
\tpkgver = 1.0.0
\tpkgrel = 1
\tsha256sums_x86_64 = aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
\tsha256sums_aarch64 = bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb

pkgname = manggo
"""


def run(directory: Path, *args: str) -> str:
    result = subprocess.run(("git", *args), cwd=directory, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class PublishTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.remote = self.base / "github.git"
        self.aur_remote = self.base / "aur.git"
        for remote, branch in ((self.remote, "main"), (self.aur_remote, "master")):
            subprocess.run(("git", "init", "--bare", f"--initial-branch={branch}", str(remote)), check=True,
                           stdout=subprocess.DEVNULL)
        self.root = self.base / "github"
        subprocess.run(("git", "clone", str(self.remote), str(self.root)), check=True,
                       stdout=subprocess.DEVNULL)
        run(self.root, "config", "core.hooksPath", "/dev/null")
        self.write_pair(self.root, PKG, SRC)
        run(self.root, "add", "PKGBUILD", ".SRCINFO")
        run(self.root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "base")
        run(self.root, "branch", "-M", "main")
        run(self.root, "push", "origin", "main")
        self.aur_seed = self.base / "aur-seed"
        subprocess.run(("git", "clone", str(self.aur_remote), str(self.aur_seed)), check=True,
                       stdout=subprocess.DEVNULL)
        run(self.aur_seed, "config", "core.hooksPath", "/dev/null")
        self.write_pair(self.aur_seed, PKG, SRC)
        run(self.aur_seed, "add", "PKGBUILD", ".SRCINFO")
        run(self.aur_seed, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "base")
        run(self.aur_seed, "branch", "-M", "master")
        run(self.aur_seed, "push", "origin", "master")
        self.aur = self.base / "aur"
        subprocess.run(("git", "clone", str(self.aur_remote), str(self.aur)), check=True,
                       stdout=subprocess.DEVNULL)
        run(self.aur, "config", "core.hooksPath", "/dev/null")
        run(self.aur, "checkout", "-B", "master", "origin/master")
        self.candidate = self.base / "candidate"
        self.make_candidate()
        self.old_root = publish.ROOT
        publish.ROOT = self.root

    def tearDown(self) -> None:
        publish.ROOT = self.old_root
        self.temp.cleanup()

    @staticmethod
    def write_pair(directory: Path, pkg: str, src: str) -> None:
        (directory / "PKGBUILD").write_text(pkg)
        (directory / ".SRCINFO").write_text(src)

    def make_candidate(self) -> None:
        self.candidate.mkdir()
        pkg = PKG.replace("1.0.0", "1.0.1").replace("aaaaaaaa", "cccccccc").replace("bbbbbbbb", "dddddddd")
        src = SRC.replace("1.0.0", "1.0.1").replace("aaaaaaaa", "cccccccc").replace("bbbbbbbb", "dddddddd")
        self.write_pair(self.candidate, pkg, src)
        hashes = {name: hashlib.sha256((self.candidate / name).read_bytes()).hexdigest()
                  for name in publish.FILES}
        (self.candidate / "manifest.json").write_text(json.dumps({
            "base_commit": run(self.root, "rev-parse", "HEAD"), "version": "1.0.1", "sha256": hashes}))

    def refresh_manifest(self) -> None:
        manifest = json.loads((self.candidate / "manifest.json").read_text())
        manifest["sha256"] = {name: hashlib.sha256((self.candidate / name).read_bytes()).hexdigest()
                              for name in publish.FILES}
        (self.candidate / "manifest.json").write_text(json.dumps(manifest))

    def assert_github_uncommitted(self, head: str) -> None:
        self.assertEqual(run(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual(run(self.remote, "rev-parse", "refs/heads/main"), head)
        self.assertEqual(run(self.root, "diff", "--cached", "--name-only"), "")
        for name in publish.FILES:
            self.assertEqual((self.root / name).read_bytes(), (self.candidate / name).read_bytes())
        self.assertFalse((self.root / "manifest.json").exists())

    def commit_prepared_files(self) -> None:
        run(self.root, "add", *publish.FILES, "automation-validation.json")
        run(self.root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "emulated commit-action")
        run(self.root, "push", "origin", "main")

    def test_publishes_aur_and_prepares_github_metadata(self) -> None:
        head = run(self.root, "rev-parse", "HEAD")
        publish.publish(self.candidate, self.aur, "main")
        self.assert_github_uncommitted(head)
        self.assertFalse((self.root / "automation-validation.json").exists())
        self.assertEqual(run(self.aur, "log", "-1", "--format=%s"), "Update manggo to 1.0.1")
        for name in publish.FILES:
            self.assertEqual(run(self.aur_remote, "show", f"master:{name}"),
                             (self.candidate / name).read_text().strip())

    def test_publishes_aur_before_preparing_files_and_report(self) -> None:
        head = run(self.root, "rev-parse", "HEAD")
        original = publish.pair(self.root)
        real_git = publish.git
        pushed = False

        def inspect_git(directory: Path, *args: str, **kwargs: object) -> str:
            nonlocal pushed
            if directory == self.aur and args[0] == "push":
                self.assertEqual(publish.pair(self.root), original)
                self.assertFalse((self.root / "automation-validation.json").exists())
                result = real_git(directory, *args, **kwargs)
                pushed = True
                return result
            return real_git(directory, *args, **kwargs)

        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42"}), \
                mock.patch.object(publish, "git", side_effect=inspect_git):
            publish.publish(self.candidate, self.aur, "main", record_validation=True)
        self.assertTrue(pushed)
        self.assert_github_uncommitted(head)
        report = json.loads((self.root / "automation-validation.json").read_bytes())
        self.assertEqual(report["base_commit"], head)
        self.assertEqual(report["run_url"], "https://github.com/owner/repo/actions/runs/42")
        self.assertEqual(report["version"], "1.0.1")
        self.assertRegex(report["validated_at"], r"^\d{4}-\d\d-\d\dT.*Z$")
        self.assertEqual(set(report), {"base_commit", "run_url", "validated_at", "version"})

    def test_replays_after_successful_github_commit(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42"}):
            publish.publish(self.candidate, self.aur, "main", record_validation=True)
            self.commit_prepared_files()
            head = run(self.root, "rev-parse", "HEAD")
            aur_head = run(self.aur_remote, "rev-parse", "master")
            publish.publish(self.candidate, self.aur, "main", record_validation=True)
        self.assert_github_uncommitted(head)
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)
        report = json.loads((self.root / "automation-validation.json").read_text())
        self.assertEqual(report["base_commit"], head)

    def test_recovers_when_aur_was_already_published(self) -> None:
        head = run(self.root, "rev-parse", "HEAD")
        self.write_pair(self.aur, *((self.candidate / name).read_text() for name in publish.FILES))
        run(self.aur, "add", "PKGBUILD", ".SRCINFO")
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "prior")
        run(self.aur, "push", "origin", "HEAD:master")
        aur_head = run(self.aur_remote, "rev-parse", "master")
        publish.publish(self.candidate, self.aur, "main")
        self.assert_github_uncommitted(head)
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)

    def test_same_version_prepares_only_validation_report(self) -> None:
        self.write_pair(self.candidate, PKG, SRC)
        manifest = json.loads((self.candidate / "manifest.json").read_text())
        manifest["version"] = "1.0.0"
        (self.candidate / "manifest.json").write_text(json.dumps(manifest))
        self.refresh_manifest()
        head = run(self.root, "rev-parse", "HEAD")
        aur_head = run(self.aur_remote, "rev-parse", "master")
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42"}):
            publish.publish(self.candidate, self.aur, "main", record_validation=True)
        self.assert_github_uncommitted(head)
        self.assertEqual(run(self.root, "diff", "--name-only"), "")
        self.assertEqual(run(self.root, "ls-files", "--others", "--exclude-standard"), "automation-validation.json")
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)
        report = json.loads((self.root / "automation-validation.json").read_text())
        self.assertEqual(report["version"], "1.0.0")

    def test_missing_actions_context_fails_before_aur_mutation(self) -> None:
        aur_head = run(self.aur, "rev-parse", "HEAD")
        original = publish.pair(self.root)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(publish.PublishError, "GITHUB_REPOSITORY and GITHUB_RUN_ID"):
                publish.publish(self.candidate, self.aur, "main", record_validation=True)
        self.assertEqual(run(self.aur, "rev-parse", "HEAD"), aur_head)
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)
        self.assertEqual(publish.pair(self.aur), original)
        self.assertEqual(publish.pair(self.root), original)
        self.assertFalse((self.root / "automation-validation.json").exists())

    def test_rejects_a_candidate_that_changes_the_baseline(self) -> None:
        path = self.candidate / "PKGBUILD"
        path.write_text(path.read_text() + "depends=('surprise')\n")
        self.refresh_manifest()
        with self.assertRaisesRegex(publish.PublishError, "release metadata"):
            publish.publish(self.candidate, self.aur, "main")

    def test_rejects_divergent_aur_checkout(self) -> None:
        (self.aur / "PKGBUILD").write_text("pkgname=other\n")
        run(self.aur, "add", "PKGBUILD")
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "manual")
        with self.assertRaisesRegex(publish.PublishError, "divergent"):
            publish.publish(self.candidate, self.aur, "main")

    def test_rejects_moved_github_main(self) -> None:
        other = self.base / "other"
        subprocess.run(("git", "clone", str(self.remote), str(other)), check=True,
                       stdout=subprocess.DEVNULL)
        run(other, "config", "core.hooksPath", "/dev/null")
        (other / "note").write_text("moved\n")
        run(other, "add", "note")
        run(other, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "moved")
        run(other, "push", "origin", "main")
        with self.assertRaisesRegex(publish.PublishError, "moved"):
            publish.publish(self.candidate, self.aur, "main")


if __name__ == "__main__":
    unittest.main()
