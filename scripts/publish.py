#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Publish a checked candidate to AUR and prepare files for commit-action."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("PKGBUILD", ".SRCINFO")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class PublishError(RuntimeError):
    pass


def git(directory: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(("git", *args), cwd=directory, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if result.returncode:
        raise PublishError("git " + " ".join(args) + ": " + result.stderr.strip())
    return result.stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_ancestor(directory: Path, older: str, newer: str) -> bool:
    return subprocess.run(("git", "merge-base", "--is-ancestor", older, newer),
                          cwd=directory, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode == 0


def value(text: str, key: str) -> str:
    found = re.search(rf"^{re.escape(key)}=(.+)$", text, re.MULTILINE)
    if not found:
        raise PublishError(f"PKGBUILD lacks {key}")
    return found.group(1).strip().strip("'\"")


def src_value(text: str, key: str) -> str:
    found = re.search(rf"^\t?{re.escape(key)} = (.+)$", text, re.MULTILINE)
    if not found:
        raise PublishError(f".SRCINFO lacks {key}")
    return found.group(1)


def changed_only(before: str, after: str, allowed: set[str]) -> bool:
    left, right = before.splitlines(), after.splitlines()
    if len(left) != len(right):
        return False
    for old, new in zip(left, right):
        if old == new:
            continue
        if not any(old.startswith(item) and new.startswith(item) for item in allowed):
            return False
    return True


def normalized_srcinfo(text: str, old: str, new: str) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        if (line.startswith("\tpkgver = ") or line.startswith("\tpkgrel = ")
                or line.startswith("\tsha256sums_x86_64 = ")
                or line.startswith("\tsha256sums_aarch64 = ")):
            continue
        lines.append(line.replace(new, old))
    return "".join(lines)


def validate(candidate: Path) -> tuple[str, str, str]:
    if not candidate.is_dir() or set(path.name for path in candidate.iterdir()) != {
            "PKGBUILD", ".SRCINFO", "manifest.json"}:
        raise PublishError("candidate must contain exactly PKGBUILD, .SRCINFO, and manifest.json")
    try:
        manifest = json.loads((candidate / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PublishError(f"invalid candidate manifest: {error}") from error
    required = {"base_commit", "version", "sha256"}
    if set(manifest) != required or not isinstance(manifest["sha256"], dict):
        raise PublishError("manifest has unexpected fields")
    if set(manifest["sha256"]) != set(FILES) or not all(
            isinstance(manifest["sha256"].get(name), str) for name in FILES):
        raise PublishError("manifest must contain both file digests")
    if not isinstance(manifest["base_commit"], str) or not COMMIT.fullmatch(manifest["base_commit"]):
        raise PublishError("manifest base_commit is invalid")
    if any(sha256(candidate / name) != manifest["sha256"][name] for name in FILES):
        raise PublishError("candidate file digest does not match manifest")
    head = git(ROOT, "rev-parse", "HEAD")
    retry = all((ROOT / name).read_bytes() == (candidate / name).read_bytes() for name in FILES)
    if manifest["base_commit"] != head and not (retry and is_ancestor(ROOT, manifest["base_commit"], head)):
        raise PublishError("candidate was not made from this GitHub commit")
    new = manifest["version"]
    if not isinstance(new, str) or not VERSION.fullmatch(new):
        raise PublishError("candidate version must be X.Y.Z")
    original = (ROOT / "PKGBUILD").read_text()
    proposed = (candidate / "PKGBUILD").read_text()
    original_srcinfo = (ROOT / ".SRCINFO").read_text()
    proposed_srcinfo = (candidate / ".SRCINFO").read_text()
    old = value(original, "pkgver")
    if value(proposed, "pkgver") != new or value(proposed, "pkgname") != "manggo":
        raise PublishError("PKGBUILD package name or version is invalid")
    if not changed_only(original, proposed, {"pkgver=", "pkgrel=", "sha256sums_x86_64=", "sha256sums_aarch64="}):
        raise PublishError("candidate PKGBUILD changes fields outside the release metadata")
    if any(not DIGEST.fullmatch(value(proposed, key).strip("()'\"")) for key in
           ("sha256sums_x86_64", "sha256sums_aarch64")):
        raise PublishError("candidate checksums must be lowercase SHA-256 hashes")
    old_tuple, new_tuple = tuple(map(int, old.split("."))), tuple(map(int, new.split(".")))
    if new_tuple < old_tuple or (new_tuple > old_tuple and value(proposed, "pkgrel") != "1"):
        raise PublishError("candidate version/pkgrel is not a valid release update")
    if new_tuple == old_tuple and proposed != original:
        raise PublishError("same-version candidate must match the current PKGBUILD")
    if src_value(proposed_srcinfo, "pkgname") != "manggo" or src_value(proposed_srcinfo, "pkgver") != new:
        raise PublishError(".SRCINFO package name or version disagrees with PKGBUILD")
    if src_value(proposed_srcinfo, "pkgrel") != value(proposed, "pkgrel"):
        raise PublishError(".SRCINFO pkgrel disagrees with PKGBUILD")
    if normalized_srcinfo(original_srcinfo, old, old) != normalized_srcinfo(proposed_srcinfo, old, new):
        raise PublishError("candidate .SRCINFO changes metadata outside release fields")
    for arch in ("x86_64", "aarch64"):
        if src_value(proposed_srcinfo, f"sha256sums_{arch}") != value(proposed, f"sha256sums_{arch}").strip("()'\""):
            raise PublishError(".SRCINFO checksum disagrees with PKGBUILD")
    return new, old, manifest["base_commit"]


def pair(directory: Path) -> tuple[bytes, bytes]:
    try:
        return tuple((directory / name).read_bytes() for name in FILES)  # type: ignore[return-value]
    except OSError as error:
        raise PublishError(f"missing package files in {directory}: {error}") from error


def copy_pair(source: Path, destination: Path) -> None:
    for name in FILES:
        shutil.copyfile(source / name, destination / name)


def validation_report(version: str, base: str) -> bytes:
    repository = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not repository or not run_id:
        raise PublishError("record-validation requires GITHUB_REPOSITORY and GITHUB_RUN_ID")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    report = {
        "base_commit": base,
        "run_url": f"{server}/{repository}/actions/runs/{run_id}",
        "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "version": version,
    }
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def publish(candidate: Path, aur: Path, branch: str, record_validation: bool = False) -> None:
    version, _, _ = validate(candidate)
    if branch != "main" or git(ROOT, "branch", "--show-current") != branch:
        raise PublishError("GitHub checkout must be on main")
    git(ROOT, "diff", "--quiet")
    git(ROOT, "diff", "--cached", "--quiet")
    base = git(ROOT, "rev-parse", "HEAD")
    git(ROOT, "fetch", "origin", branch)
    if git(ROOT, "rev-parse", f"origin/{branch}") != base:
        raise PublishError("GitHub main moved since the candidate was created")
    git(aur, "diff", "--quiet")
    git(aur, "diff", "--cached", "--quiet")
    report = validation_report(version, base) if record_validation else None
    expected, proposed = pair(ROOT), pair(candidate)
    actual = pair(aur)
    if actual not in (expected, proposed):
        raise PublishError("AUR checkout has divergent package metadata")
    if actual != proposed:
        copy_pair(candidate, aur)
        git(aur, "add", *FILES)
        git(aur, "config", "user.name", "github-actions[bot]")
        git(aur, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        git(aur, "commit", "-m", f"Update manggo to {version}")
        push_env = os.environ.copy()
        push_env["GIT_LFS_SKIP_PUSH"] = "1"
        git(aur, "push", "origin", "HEAD:master", env=push_env)
    if expected != proposed:
        copy_pair(candidate, ROOT)
    if report is not None:
        (ROOT / "automation-validation.json").write_bytes(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--aur-checkout", type=Path, required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--record-validation", action="store_true",
                        help="prepare automation-validation.json for the subsequent commit-action step")
    args = parser.parse_args()
    try:
        publish(args.candidate.resolve(), args.aur_checkout.resolve(), args.branch,
                args.record_validation)
    except PublishError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
