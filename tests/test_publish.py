"""Integration tests for the guarded AUR publisher."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from base64 import b64decode
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
        self.write_pair(self.root, PKG, SRC)
        run(self.root, "add", "PKGBUILD", ".SRCINFO")
        run(self.root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "base")
        run(self.root, "branch", "-M", "main")
        run(self.root, "push", "origin", "main")
        self.aur_seed = self.base / "aur-seed"
        subprocess.run(("git", "clone", str(self.aur_remote), str(self.aur_seed)), check=True,
                       stdout=subprocess.DEVNULL)
        self.write_pair(self.aur_seed, PKG, SRC)
        run(self.aur_seed, "add", "PKGBUILD", ".SRCINFO")
        run(self.aur_seed, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "base")
        run(self.aur_seed, "branch", "-M", "master")
        run(self.aur_seed, "push", "origin", "master")
        self.aur = self.base / "aur"
        subprocess.run(("git", "clone", str(self.aur_remote), str(self.aur)), check=True,
                       stdout=subprocess.DEVNULL)
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

    def test_publishes_aur_then_github_and_retries(self) -> None:
        publish.publish(self.candidate, self.aur, "main")
        self.assertEqual((self.root / "PKGBUILD").read_text(), (self.candidate / "PKGBUILD").read_text())
        self.assertEqual(run(self.aur, "log", "-1", "--format=%s"), "Update manggo to 1.0.1")
        self.assertEqual(run(self.root, "log", "-1", "--format=%s"), "chore: update Manggo to 1.0.1")
        publish.publish(self.candidate, self.aur, "main")

    def test_recovers_when_aur_was_already_published(self) -> None:
        self.write_pair(self.aur, *( (self.candidate / name).read_text() for name in publish.FILES))
        run(self.aur, "add", "PKGBUILD", ".SRCINFO")
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "prior")
        run(self.aur, "push", "origin", "HEAD:master")
        publish.publish(self.candidate, self.aur, "main")
        self.assertEqual((self.root / ".SRCINFO").read_text(), (self.candidate / ".SRCINFO").read_text())

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
        (other / "note").write_text("moved\n")
        run(other, "add", "note")
        run(other, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "moved")
        run(other, "push", "origin", "main")
        with self.assertRaisesRegex(publish.PublishError, "moved"):
            publish.publish(self.candidate, self.aur, "main")

    def test_record_commit_uses_temporary_branch_and_expected_files(self) -> None:
        calls: list[tuple[tuple[str, ...], dict | None]] = []

        def fake_api(args: tuple[str, ...], payload: dict | None = None) -> dict:
            calls.append((args, payload))
            if args[0] == "graphql":
                return {"data": {"createCommitOnBranch": {"commit": {
                    "oid": "e" * 40, "url": "https://example.invalid/commit"}}}}
            if args[0].startswith("repos/") and args[0].endswith("/git/commits/" + "e" * 40):
                return {"verification": {"verified": True}}
            return {}

        git_calls: list[tuple[str, ...]] = []
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42",
                                          "GITHUB_RUN_ATTEMPT": "3", "GITHUB_GIT_SSH_COMMAND": "ssh -i key"}), \
                mock.patch.object(publish, "gh_api", side_effect=fake_api), \
                mock.patch.object(publish, "git", side_effect=lambda _root, *args, **_kw: git_calls.append(args) or ""):
            publish.api_commit("main", "f" * 40, "1.0.1", self.candidate, True)
        mutation = calls[1][1]["variables"]["input"]
        self.assertEqual(mutation["branch"]["branchName"], "aur-automation/42-3")
        self.assertEqual(mutation["expectedHeadOid"], "f" * 40)
        additions = mutation["fileChanges"]["additions"]
        self.assertEqual([item["path"] for item in additions],
                         ["PKGBUILD", ".SRCINFO", "automation-validation.json"])
        report = json.loads(b64decode(additions[-1]["contents"]))
        self.assertEqual(report["base_commit"], "f" * 40)
        self.assertEqual(report["run_url"], "https://github.com/owner/repo/actions/runs/42")
        self.assertIn(("fetch", "origin", "aur-automation/42-3"), git_calls)
        self.assertIn(("push", "git@github.com:owner/repo.git", "e" * 40 + ":refs/heads/main"), git_calls)
        self.assertEqual(calls[-1][0], ("--method", "DELETE", "repos/owner/repo/git/refs/heads/aur-automation/42-3"))

    def test_record_commit_rejects_unsigned_result(self) -> None:
        def fake_api(args: tuple[str, ...], payload: dict | None = None) -> dict:
            if args[0] == "graphql":
                return {"data": {"createCommitOnBranch": {"commit": {"oid": "e" * 40}}}}
            if args[0].startswith("repos/") and "/git/commits/" in args[0]:
                return {"verification": {"verified": False}}
            return {}

        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42",
                                          "GITHUB_RUN_ATTEMPT": "3", "GITHUB_GIT_SSH_COMMAND": "ssh -i key"}), \
                mock.patch.object(publish, "gh_api", side_effect=fake_api), \
                mock.patch.object(publish, "git") as git_mock:
            with self.assertRaisesRegex(publish.PublishError, "not verified"):
                publish.api_commit("main", "f" * 40, "1.0.1", self.candidate, False)
        git_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
