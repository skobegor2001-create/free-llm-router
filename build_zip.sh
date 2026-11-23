#!/usr/bin/env bash
# Сборка «тонкого» ZIP-установщика: код + установщик, Python подтянется при установке.
# Результат: releases/FreeLLMRouter-win64.zip
set -euo pipefail
cd "$(dirname "$0")"

STAGE="releases/FreeLLMRouter"
rm -rf "$STAGE"
mkdir -p "$STAGE"/{app,windows/runtime,windows/assets}

# код приложения
cp -r router.py server.py cli.py doctor.py indexdb.py filetext.py chatstore.py \
      requirements.txt .env.example .gitignore \
      install.cmd uninstall.cmd launcher.cmd \
      static examples tests README.md GET-KEYS.md get_keys.py "$STAGE/app/"
cp windows/runtime/launcher.py "$STAGE/windows/runtime/"
cp windows/setup_shortcuts.ps1 "$STAGE/windows/"
cp windows/assets/app.ico "$STAGE/windows/assets/" 2>/dev/null || true
cp windows/WIN-INSTALL.md "$STAGE/" 2>/dev/null || true

# избавиться от мусора Python
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name "*.pyc" -delete 2>/dev/null || true

mkdir -p releases
(cd releases && zip -qr FreeLLMRouter-win64.zip FreeLLMRouter)
rm -rf "$STAGE"
echo "✓ releases/FreeLLMRouter-win64.zip ($(du -h releases/FreeLLMRouter-win64.zip | cut -f1))"
