#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# shellcheck disable=SC2154
# Run inside a disposable official Arch container; never pass secrets to it.
set -euo pipefail

pacman -Syu --needed --noconfirm \
  base-devel git python patchelf namcap shellcheck desktop-file-utils \
  xorg-server-xvfb xorg-xwininfo ttf-dejavu > /tmp/setup.log 2>&1 || {
  tail -60 /tmp/setup.log
  exit 1
}

useradd --create-home builder
project=/home/builder/project
install -d "$project/scripts" "$project/tests"
cp /workspace/PKGBUILD /workspace/.SRCINFO "$project/"
cp /workspace/scripts/*.py "$project/scripts/"
cp /workspace/tests/*.py "$project/tests/"
chown -R builder:builder "$project"

as_builder() {
  runuser -u builder -- bash -c 'cd /home/builder/project && exec "$@"' bash "$@"
}

as_builder python3 -m unittest discover -s tests -v
as_builder python3 scripts/update.py --update
cd "$project"
shellcheck PKGBUILD
as_builder makepkg --printsrcinfo > /tmp/generated.SRCINFO
cmp .SRCINFO /tmp/generated.SRCINFO

# The recipe is trusted repository code; the updater changes only version/hashes.
# shellcheck source=/dev/null
source ./PKGBUILD
pacman -S --needed --noconfirm "${depends[@]}" "${makedepends[@]}" \
  > /tmp/dependencies.log 2>&1 || {
  tail -60 /tmp/dependencies.log
  exit 1
}
as_builder makepkg --cleanbuild --force --noconfirm > /tmp/build.log 2>&1 || {
  tail -80 /tmp/build.log
  exit 1
}
namcap PKGBUILD > /tmp/namcap-pkgbuild.log
package=$(as_builder makepkg --packagelist)
namcap "$package" > /tmp/namcap-package.log
if grep -E ' E: ' /tmp/namcap-pkgbuild.log /tmp/namcap-package.log; then
  exit 1
fi
pacman -U --noconfirm "$package" > /tmp/install.log 2>&1
pacman -Qk manggo
desktop-file-validate /usr/share/applications/com.pylogmon.Manggo.desktop
update-mime-database /usr/share/mime
while IFS= read -r binary; do
  result=$(ldd "$binary" 2>&1 || true)
  if [[ "$result" == *'not found'* ]]; then
    printf '%s\n%s\n' "$binary" "$result"
    exit 1
  fi
done < <(find /usr/lib/manggo -type f \( -name '*.so*' -o -name Manggo -o -name ManggoAsrWorker \))

# Exercise an installed desktop app without accessing a user's desktop or files.
Xvfb :99 -screen 0 1440x1000x24 -nolisten tcp -ac > /tmp/xvfb.log 2>&1 &
xvfb_pid=$!
for ((attempt = 0; attempt < 20; attempt++)); do
  if DISPLAY=:99 xwininfo -root > /dev/null 2>&1; then
    break
  fi
  sleep 1
done
DISPLAY=:99 xwininfo -root > /dev/null
install -d -o builder -g builder -m 700 /tmp/manggo-runtime
runuser -u builder -- env DISPLAY=:99 XDG_RUNTIME_DIR=/tmp/manggo-runtime \
  QT_QPA_PLATFORM=xcb dbus-run-session -- Manggo > /tmp/manggo.log 2>&1 &
gui_pid=$!
trap 'kill "$gui_pid" "$xvfb_pid" 2>/dev/null || true' EXIT
window_ready=false
for ((attempt = 0; attempt < 20; attempt++)); do
  if DISPLAY=:99 xwininfo -root -tree > /tmp/windows.txt 2>/dev/null; then
    if python3 - <<'PY'
import re
from pathlib import Path
windows = Path('/tmp/windows.txt').read_text()
matches = re.findall(r'"[^"\n]*Manggo[^"\n]*".*?\s(\d+)x(\d+)\+', windows)
raise SystemExit(0 if any(int(w) > 100 and int(h) > 100 for w, h in matches) else 1)
PY
    then
      window_ready=true
      break
    fi
  fi
  sleep 1
done
if [[ "$window_ready" != true ]]; then
  cat /tmp/manggo.log /tmp/windows.txt
  exit 1
fi
actual_version=$(runuser -u builder -- env DISPLAY=:99 XDG_RUNTIME_DIR=/tmp/manggo-runtime \
  QT_QPA_PLATFORM=xcb timeout 10 manggo --version)
[[ "$actual_version" == "Manggo $pkgver" ]]
kill "$gui_pid" "$xvfb_pid" 2>/dev/null || true
trap - EXIT

# ARM64 repackaging is architecture-independent; this is not a native runtime test.
arm=/home/builder/arm
install -d -o builder -g builder "$arm"
cp PKGBUILD "$arm/"
cp /etc/makepkg.conf "$arm/makepkg.conf"
printf '\nCARCH=aarch64\nCHOST=aarch64-linux-gnu\n' >> "$arm/makepkg.conf"
chown builder:builder "$arm/PKGBUILD" "$arm/makepkg.conf"
runuser -u builder -- bash -c 'cd /home/builder/arm && makepkg --config ./makepkg.conf --nodeps --ignorearch --cleanbuild --force --noconfirm' \
  > /tmp/build-arm.log 2>&1 || {
  tail -80 /tmp/build-arm.log
  exit 1
}

install -m 644 PKGBUILD .SRCINFO /candidate/
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path
root = Path('/candidate')
pkgver = next(line.split('=', 1)[1] for line in (root / 'PKGBUILD').read_text().splitlines()
              if line.startswith('pkgver='))
manifest = {'base_commit': os.environ['BASE_COMMIT'], 'version': pkgver,
            'sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                       for name in ('PKGBUILD', '.SRCINFO')}}
(root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(f'Validated Manggo {pkgver}: x86_64 install/GUI, ARM64 packaging, metadata only.')
PY
