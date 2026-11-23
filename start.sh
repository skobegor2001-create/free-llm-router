#!/usr/bin/env bash
# Запуск среды: проверяет зависимости, готовит .env и поднимает веб-чат + шлюз.
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c "import fastapi, uvicorn, dotenv, httpx" 2>/dev/null; then
  echo "→ устанавливаю зависимости…"
  pip install -q -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "→ создан .env из шаблона — впишите бесплатный ключ (любой один)"
fi

PORT="${PORT:-8000}"
echo "→ Free LLM Router: http://localhost:${PORT}"
exec python3 server.py
