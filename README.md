# Manggo for Arch Linux

[![Validate and update Manggo](https://github.com/liblaf/aur-manggo/actions/workflows/update.yml/badge.svg)](https://github.com/liblaf/aur-manggo/actions/workflows/update.yml)

[Manggo](https://manggo.pylogmon.cn/) is a desktop translation and screenshot OCR
assistant. This repository packages its official Debian downloads for Arch Linux.
It contains packaging code, release maintenance tools, and validation notes.

Architectures: **x86_64** and **aarch64**. See the AUR listing for the current version.
Published as [manggo on the AUR](https://aur.archlinux.org/packages/manggo),
maintained by `liblaf`.

Source: [liblaf/aur-manggo](https://github.com/liblaf/aur-manggo).
Local project: `~/Projects/liblaf/aur-manggo`.

---

## Install locally

Install Arch's build tools, then run these commands from this directory as your
normal user:

```bash
sudo pacman -S --needed base-devel
makepkg --syncdeps --install
```

Launch **Manggo** from the application menu, or run `manggo`. The upstream `Manggo`
command, desktop actions, icon, and `.mplugin` file association are preserved.

An AUR helper can install it with `yay -S manggo`
and update it during normal AUR upgrades. Use the package manager for updates;
upstream's Debian/RPM updater does not manage the Arch package database.

## Package choices

- The name is `manggo`: Arch's [nonfree package naming guidance](https://wiki.archlinux.org/title/Nonfree_applications_package_guidelines#Package_naming)
  omits `-bin` when source code is unavailable.
- Each architecture downloads the corresponding official Gitcode Debian package
  using a versioned URL and a pinned SHA-256 checksum.
- Bundled Qt, FFmpeg, ICU, OCR models, and speech components stay together under
  `/usr/lib/manggo`. Replacing these with system libraries risks ABI mismatches.
- The bundled sherpa-onnx library's CI build directory is removed from its RUNPATH;
  its relative paths remain intact. Application executables are kept unstripped.
- ARM64 has additional GStreamer, Brotli, ICE, and SM runtime dependencies.
- Wayland integration can require `wl-clipboard`, `xdg-desktop-portal`, and the
  portal backend for your desktop. Global shortcuts, selection, and capture depend
  on compositor support. Install optional packages according to the features used.

## Maintain a release

The helper uses Python's standard library. Updating metadata also requires Arch's
`makepkg` and `bsdtar`. Check releases without changing files:

```bash
python3 scripts/update.py --check
```

To prepare the latest stable release:

```bash
python3 scripts/update.py --update
git diff -- PKGBUILD .SRCINFO
makepkg --cleanbuild --syncdeps --force
```

The updater checks the vendor website's download metadata, requires matching x64
and ARM64 versions, downloads both artifacts, and checks their Debian package
names, architectures, and versions. It updates both checksums, resets `pkgrel=1`,
and regenerates `.SRCINFO`. It refuses downgrades, changed same-version downloads,
unexpected release URLs, and unsupported version formats. A changed upstream
layout requires maintainer review. Downloads are verified against the pinned
hashes by `makepkg`; the vendor download page currently provides no signed checksum.

For packaging changes to an existing release, increment `pkgrel` and run:

```bash
makepkg --printsrcinfo > .SRCINFO
```

Before publishing, validate the package in a clean Arch environment:

```bash
sudo pacman -S --needed devtools namcap shellcheck desktop-file-utils
shellcheck PKGBUILD
extra-x86_64-build
namcap PKGBUILD
namcap manggo-*.pkg.tar.zst
```

Review lint findings rather than treating exit status alone as success. The
validation notes record the upstream warnings observed for this release. Run GUI
checks on the intended desktop; validate ARM64 on ARM64 hardware before claiming
runtime support there.

## Automated updates

[GitHub Actions](https://github.com/liblaf/aur-manggo/actions/workflows/update.yml)
checks the official download page hourly, at minute 17. Unchanged releases skip
downloads and builds. A new release goes through these steps:

1. Require matching x86_64/ARM64 Debian releases and verify their control metadata.
2. Update version/checksums and regenerate `.SRCINFO`.
3. Run updater and publisher regression tests in a disposable Arch container.
4. Build and install x86_64, check dependencies and package metadata, and launch
   the application in Xvfb. Also validate ARM64 repackaging.
5. Upload only the validated `PKGBUILD`, `.SRCINFO`, and integrity manifest.
6. In a separate job, verify the candidate against the trusted recipe, publish
   those two metadata files to AUR, then commit them to GitHub `main`.

Downloaded vendor code runs without the AUR key or a GitHub token with write
access. Publication is restricted to this repository's `main` branch and scheduled
or manual runs. Concurrent update runs are serialized. Unexpected recipe changes,
upstream formats, missing dependencies, and divergent AUR edits stop publication.

If AUR succeeds but the GitHub push fails, the next run can complete the GitHub
commit without republishing AUR. AUR/GitHub differences also trigger validation
when the upstream version is unchanged, allowing interrupted publication to retry.

Run the complete workflow manually:

```bash
gh workflow run update.yml --repo liblaf/aur-manggo -f validate_current=true
```

Pushes and pull requests also run validation, with publication disabled. Failure
details are available on the Actions page. GitHub schedules can be delayed; public
repository schedules may be disabled after 60 days without repository activity.
If necessary, re-enable this workflow with `gh workflow enable update.yml --repo
liblaf/aur-manggo`. See [GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Automation key identity and recovery

The workflow uses a dedicated Ed25519 key registered to the AUR account `liblaf`.
Its private half is stored as the encrypted repository secret
`AUR_SSH_PRIVATE_KEY`. Its public half is tracked in
[config/aur-actions-key.pub](https://github.com/liblaf/aur-manggo/blob/main/config/aur-actions-key.pub).

| Item | Value |
| --- | --- |
| Purpose | GitHub Actions publishing `manggo` to AUR |
| AUR account | `liblaf` |
| GitHub repository | `liblaf/aur-manggo` |
| Secret | `AUR_SSH_PRIVATE_KEY` |
| Public-key fingerprint | `SHA256:E4XWAN4OEnFRSRd1S6drJS543lwVtninVPxieehc4gQ` |

aurweb removes comments, but this does not change the key or its fingerprint:

```bash
ssh-keygen -lf config/aur-actions-key.pub
```

To locate the entry in AUR settings, compare the second field (the long key data)
against the tracked public key. You can also copy the AUR public-key list into a
temporary file and run `ssh-keygen -lf` on that file to compare fingerprints.

GitHub secrets cannot be read back. If the private key is lost or needs rotating,
generate a new dedicated pair, register the new public key in AUR, update the
GitHub secret, and run the workflow successfully before removing the old AUR key.
Commit the new public file and fingerprint to keep the mapping current. Keep the
personal SSH key separate; no private key belongs in Git history.

## Publish updates to the AUR

The package is owned by the `liblaf` [AUR account](https://aur.archlinux.org/account/liblaf).
Use that account's registered SSH key. Follow the
[AUR submission guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines).

From this directory, copy only the two AUR metadata files into an AUR checkout:

```bash
aur_dir="$(mktemp -d)"
git clone ssh://aur@aur.archlinux.org/manggo.git "$aur_dir"
cp PKGBUILD .SRCINFO "$aur_dir/"
git -C "$aur_dir" add PKGBUILD .SRCINFO
git -C "$aur_dir" commit -m 'Update Manggo packaging'
GIT_LFS_SKIP_PUSH=1 git -C "$aur_dir" push origin HEAD:master
```

You can reuse `~/Projects/liblaf/aur-manggo-aur` instead of cloning again.
Keep this maintenance repository separately. The push skips Git LFS because the
AUR repository contains only text metadata; no global Git configuration is changed.
Do not commit downloaded
Debian files, built Arch packages, or build directories.

## License

The packaging code is licensed under **0BSD**; see the included `LICENSE`.
Manggo itself is **proprietary**, governed by its [user agreement](https://manggo.pylogmon.cn/terms).
The package installs the exact vendor license and agreement under
`/usr/share/licenses/manggo`; bundled third-party notices are preserved.

Upstream's license restricts redistribution. This recipe retrieves binaries
directly from the vendor; it does not grant permission to mirror them or publish
built packages. Confirm the intended public packaging arrangement with upstream
before submission if their permission is unclear. Their listed contact is
`support@pylogmon.cn`.
