#!/usr/bin/env bash
# Сборка «полного» ZIP-установщика: Python 3.12 embedded внутри, офлайн-зависимости.
# Пользователю не нужен ни Python, ни интернет для установки.
# Результат: releases/FreeLLMRouter-win64-portable.zip
#
# Для офлайн-зависимостей заранее выполните на Linux:
#   pip download --only-binary=:all: --python-version 3.12 --platform win_amd64 \
#       --implementation cp -d build/wheels -r requirements.txt
set -euo pipefail
cd "$(dirname "$0")"

PY_VER="3.12.8"
PY_ZIP="python-${PY_VER}-embed-amd64.zip"
PY_URL="https://www.python.org/ftp/python/${PY_VER}/${PY_ZIP}"
CACHE="build"
STAGE="releases/FreeLLMRouter"

mkdir -p "$CACHE"
[ -f "$CACHE/$PY_ZIP" ] || { echo "→ скачиваю $PY_ZIP"; curl -fsSL "$PY_URL" -o "$CACHE/$PY_ZIP"; }

rm -rf "$STAGE"
mkdir -p "$STAGE"/{app,windows/runtime,windows/assets}

# --- код приложения ---
cp -r router.py server.py cli.py doctor.py indexdb.py filetext.py chatstore.py \
      requirements.txt .env.example .gitignore \
      install.cmd uninstall.cmd launcher.cmd \
      static examples tests README.md README-full.md GET-KEYS.md get_keys.py "$STAGE/app/"
cp windows/runtime/launcher.py "$STAGE/windows/runtime/"
cp windows/setup_shortcuts.ps1 "$STAGE/windows/"
cp windows/assets/app.ico "$STAGE/windows/assets/" 2>/dev/null || true
cp windows/WIN-INSTALL.md "$STAGE/" 2>/dev/null || true

# --- встроенный Python ---
echo "→ распаковываю embedded Python ${PY_VER}"
unzip -qo "$CACHE/$PY_ZIP" -d "$STAGE/runtime"

# разрешаем import site (в embedded он выключен) и добавляем пути
cat > "$STAGE/runtime/python312._pth" <<'PTH'
python312.zip
.
Lib\site-packages
PTH

# --- зависимости: офлайн-wheels, если есть; иначе установщик поставит онлайн ---
if ls "$CACHE"/wheels/*.whl >/dev/null 2>&1; then
  echo "→ ставлю офлайн-зависимости из build/wheels"
  python3 -m pip install -q --no-index --find-links "$CACHE/wheels" \
      --only-binary=:all: --python-version 3.12 --platform win_amd64 --implementation cp \
      --target "$STAGE/runtime/Lib/site-packages" -r requirements.txt
else
  echo "→ build/wheels не найден: зависимости будут установлены при запуске install.cmd (нужен интернет)"
fi

# --- проверка, что embedded Python собран правильно ---
if command -v unzip >/dev/null; then
  unzip -l "$CACHE/$PY_ZIP" | grep -q "python312.zip" && echo "→ python312.zip на месте"
fi

find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

mkdir -p releases
(cd releases && zip -qr FreeLLMRouter-win64-portable.zip FreeLLMRouter)
rm -rf "$STAGE"
echo "✓ releases/FreeLLMRouter-win64-portable.zip ($(du -h releases/FreeLLMRouter-win64-portable.zip | cut -f1))"
