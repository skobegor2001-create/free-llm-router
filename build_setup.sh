#!/usr/bin/env bash
# Сборка настоящего установщика: releases/FreeLLMRouter-Setup-<версия>.exe (NSIS).
# Payload: код + встроенный Python 3.12 + офлайн-зависимости.
# Python для поиска компилятора не нужен — используется всё из build/.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="$(grep -oP 'VERSION = "\K[0-9.]+' server.py | head -1)"
[ -n "$VERSION" ] || VERSION="1.0.0"

PY_VER="3.12.8"
PY_ZIP="python-${PY_VER}-embed-amd64.zip"
PY_URL="https://www.python.org/ftp/python/${PY_VER}/${PY_ZIP}"
CACHE="build"
STAGE="build/payload"
NSIS_HOME="${NSIS_HOME:-/tmp/nsisroot/root}"

# ---------- NSIS ----------
if command -v makensis >/dev/null 2>&1; then
  MAKENSIS="$(command -v makensis)"; export NSISDIR="${NSISDIR:-$(dirname "$(dirname "$MAKENSIS")")/share/nsis}"
else
  MAKENSIS="$NSIS_HOME/usr/bin/makensis"
  export NSISDIR="$NSIS_HOME/usr/share/nsis"
fi
[ -x "$MAKENSIS" ] || { echo "нет makensis: см. build/README или установите nsis"; exit 1; }
echo "→ NSIS: $($MAKENSIS -VERSION) ($NSISDIR)"

# ---------- кэш ----------
mkdir -p "$CACHE"
[ -f "$CACHE/$PY_ZIP" ] || { echo "→ скачиваю $PY_ZIP"; curl -fsSL "$PY_URL" -o "$CACHE/$PY_ZIP"; }
if ! ls "$CACHE"/wheels/*.whl >/dev/null 2>&1; then
  echo "→ скачиваю Windows-колёса зависимостей"
  mkdir -p "$CACHE/wheels"
  python3 -m pip download -q --only-binary=:all: --python-version 3.12 \
      --platform win_amd64 --implementation cp -d "$CACHE/wheels" -r requirements.txt
fi

# ---------- payload ----------
echo "→ собираю payload"
rm -rf "$STAGE"
mkdir -p "$STAGE"/{windows/runtime,windows/assets,wheels}

cp -a router.py server.py cli.py doctor.py get_keys.py indexdb.py filetext.py chatstore.py \
     requirements.txt .env.example install.cmd uninstall.cmd launcher.cmd \
     GET-KEYS.md README.md README-full.md static examples tests \
     "$STAGE/"
cp windows/runtime/launcher.py windows/uninstaller.py "$STAGE/windows/runtime/"
cp windows/setup_shortcuts.ps1 "$STAGE/windows/"
cp windows/assets/app.ico "$STAGE/windows/assets/"

echo "→ распаковываю встроенный Python ${PY_VER}"
unzip -qo "$CACHE/$PY_ZIP" -d "$STAGE/runtime"
cat > "$STAGE/runtime/python312._pth" <<'PTH'
python312.zip
.
Lib\site-packages
PTH

echo "→ ставлю зависимости в payload (офлайн-колёса)"
python3 -m pip install -q --no-index --find-links "$CACHE/wheels" \
    --only-binary=:all: --python-version 3.12 --platform win_amd64 --implementation cp \
    --target "$STAGE/runtime/Lib/site-packages" -r requirements.txt

# ---------- компиляция установщика ----------
mkdir -p dist
echo "→ компилирую Setup.exe"
"$MAKENSIS" -V2 \
  -DSOURCE_DIR="$(pwd)/$STAGE" \
  -DWHEELS_DIR="$(pwd)/$CACHE/wheels" \
  -DAPP_VERSION="$VERSION" \
  -DICON_FILE="$(pwd)/windows/assets/app.ico" \
  -DPYINST_DIR="" \
  windows/installer.nsi

ls -lh "releases/FreeLLMRouter-Setup-${VERSION}.exe"
