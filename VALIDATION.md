# Validation: Manggo 1.1.2-1

Checked on 2026-10-17, using the official vendor downloads linked from
[Manggo's website](https://manggo.pylogmon.cn/).

Published [manggo 1.1.2-1](https://aur.archlinux.org/packages/manggo) under AUR account
`liblaf`, commit `1dde24e`. The public PKGBUILD was downloaded after submission and
matched the tested recipe exactly. Only `PKGBUILD` and `.SRCINFO` were submitted.

| Artifact | SHA-256 |
| --- | --- |
| `manggo_1.1.2_amd64.deb` | `e87ddf5fa75323bfcffae6786616a89f18fff09f4d9ff5156e2447d63eb82343` |
| `manggo_1.1.2_arm64.deb` | `7dec16e6cdaebc3f3bc50b9142f651e0290cd00911f59a41663574fc58bcdc6e` |

## x86_64

Built as an unprivileged user in a fresh official `archlinux:base` Docker
container, image digest
`sha256:3c64b538588416593f69a1aebd2f2ec0f2541b29159f32556fa7def2167147a0`.
The container was updated before building and used the dependencies declared by
the PKGBUILD, plus validation tools.

- `makepkg --cleanbuild --force --noconfirm --log`: passed.
- Installed the resulting package using `pacman -U`.
- `pacman -Qk manggo`: 209 total files, 0 missing files.
- `namcap PKGBUILD`: no findings. `namcap` on the package: zero errors.
- All packaged ELF libraries and executables resolved their linked libraries;
  no `ldd` output reported `not found`.
- `desktop-file-validate` and `update-mime-database`: passed.
- Regenerated `.SRCINFO` matched the committed metadata.
- Started `Manggo` as the unprivileged container user in Xvfb with a private D-Bus
  session. Its General Settings window rendered correctly; a startup screenshot
  is provided alongside this directory.
- `manggo --version` returned `Manggo 1.1.2`.
- The speech worker `ManggoAsrWorker --help` exited successfully.

The vendor's sherpa-onnx library included a CI build directory in its RUNPATH.
The package removes that entry using `patchelf`, preserving
`$ORIGIN:$ORIGIN/..`. Both architectures were checked after patching.

Remaining `namcap` warnings concern upstream binaries lacking FULL RELRO, retained
debug symbols, the vendor's `/usr/translations` directory, QML references in
bundled Qt libraries, unused shared-library links, and dependencies satisfied
through Arch's `gcc-libs` compatibility package. No system Qt packages were added
to conceal those bundled-library warnings.

The minimal container had no PipeWire service/library, producing a Qt multimedia
notice while the GUI still opened; `libpipewire` is an optional dependency.
This test did not validate live translation providers, desktop global shortcuts,
Wayland capture/clipboard behavior, microphone input, OCR accuracy, or Pro features.

## ARM64

Downloaded and verified the official `arm64` Debian artifact. Inspected its ELF
dependencies and added its GStreamer, Brotli, ICE, and SM dependencies separately.

An architecture-independent repackaging check passed in the x86_64 container using
an aarch64 `makepkg` configuration with dependency and host-architecture checks
disabled. The resulting `.PKGINFO` declares `aarch64` and the appropriate extra
dependencies. This establishes successful extraction, patching, and packaging;
it is not an ARM64 execution test. Native ARM64 installation and runtime validation
remain outstanding.

## Maintenance helper

- A live `--check` query reported both architectures at version 1.1.2.
- A live `--update` downloaded both artifacts and confirmed that their checksums
  match the pinned metadata, without changing files.
- Scratch fixtures simulated a newer release and ran the actual
  `makepkg --printsrcinfo` from an unrelated caller directory.
- Failure fixtures verified rollback when metadata generation fails, refusal of
  changed same-version artifacts, and refusal of malformed or divergent release
  metadata. Production files were untouched by fixture tests.
- ShellCheck passed on the PKGBUILD. Markdown formatting checks passed.
