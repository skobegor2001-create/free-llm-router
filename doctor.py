"""Проверка подключения: ключи, список моделей и живой запрос к каждому провайдеру.

    python doctor.py           # проверить всех, у кого есть ключ
    python doctor.py --force   # игнорировать паузы после лимитов
"""

from __future__ import annotations

# Запуск может идти из встроенного Python с файлом ._pth: в этом режиме папка
# скрипта не попадает в sys.path — добавляем её явно, до локальных импортов.
from pathlib import Path as _Path
import sys as _sys
_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

import argparse
import asyncio
import sys
import time

import router as rt

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[38;5;79m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m",
)


async def probe(name: str) -> tuple[str, bool, str]:
    p = rt.PROVIDERS[name]
    t0 = time.time()
    try:
        models = await rt.list_models(name, force=True)
    except Exception as exc:  # noqa: BLE001
        return name, False, f"список моделей недоступен: {exc}"

    model = next((m for m in p.models if m in models), models[0] if models else None)
    if not model and models:
        model = models[0]
    if not model:
        return name, False, "провайдер не вернул ни одной модели"

    try:
        res = await rt.chat(
            [{"role": "user", "content": "Ответь одним словом: работает"}],
            provider=name,
            model=model,
            max_tokens=24,
            allow_fallback=False,
        )
    except rt.NoProviders as exc:
        return name, False, str(exc)[:150] + f" · моделей: {len(models)}"

    dt = time.time() - t0
    snippet = (res["text"] or "").strip().replace("\n", " ")[:60]
    return name, True, f"{model} · {dt:.1f} c · «{snippet}» · моделей: {len(models)}"


async def main(force: bool) -> int:
    configured = [n for n, p in rt.PROVIDERS.items() if p.key]
    missing = [n for n, p in rt.PROVIDERS.items() if not p.key]

    print(f"{BOLD}Free LLM Router · проверка подключения{RESET}\n")
    for name in missing:
        p = rt.PROVIDERS[name]
        print(f"  {YELLOW}○{RESET} {p.title:<12} {DIM}ключа нет — {p.signup}{RESET}")

    if not configured:
        print(f"\n{YELLOW}Ни одного ключа. Впишите хотя бы один в .env{RESET}")
        return 1

    if force:
        rt._cooldown.clear()

    results = await asyncio.gather(*(probe(n) for n in configured))
    ok = 0
    for name, good, info in results:
        mark = f"{GREEN}✓{RESET}" if good else f"{RED}✗{RESET}"
        ok += good
        print(f"  {mark} {rt.PROVIDERS[name].title:<12} {info}")

    print(f"\n{BOLD}Итого:{RESET} {ok}/{len(configured)} провайдеров отвечают.")
    if ok == 0:
        print(f"{DIM}Проверьте ключи и лимиты: python cli.py --status{RESET}")
    return 0 if ok else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="игнорировать паузы после лимитов")
    code = asyncio.run(main(ap.parse_args().force))
    sys.exit(code)
