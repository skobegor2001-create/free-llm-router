"""Открывает страницы выдачи бесплатных API-ключей и печатает памятку.

    python get_keys.py            # открыть все 5 страниц в браузере
    python get_keys.py groq       # только одну
    python get_keys.py --print    # ничего не открывать, только памятка
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

PROVIDERS = [
    {
        "key": "gemini",
        "title": "Google Gemini",
        "url": "https://aistudio.google.com/apikey",
        "env": "GEMINI_API_KEY",
        "prefix": "AIza…",
        "need": "только аккаунт Google",
        "steps": ["Create API key (принять условия, если спросит — Create new project)",
                  "скопировать ключ из списка"],
        "limits": "~5–15 запр./мин · 20–1 500/день",
    },
    {
        "key": "groq",
        "title": "Groq",
        "url": "https://console.groq.com/keys",
        "env": "GROQ_API_KEY",
        "prefix": "gsk_…",
        "need": "Google / GitHub / email, без карты и телефона",
        "steps": ["Sign Up → слева API Keys",
                  "Create API Key → имя → скопировать сразу (второй раз не покажут)"],
        "limits": "30 запр./мин · 1 000/день · 200K ток./день · очень быстро",
    },
    {
        "key": "cerebras",
        "title": "Cerebras",
        "url": "https://cloud.cerebras.ai",
        "env": "CEREBRAS_API_KEY",
        "prefix": "csk-…",
        "need": "email или GitHub, без карты и телефона",
        "steps": ["Sign up → в консоли слева API Keys",
                  "Create / Generate API key → скопировать"],
        "limits": "30 запр./мин · 14 400/день · 1 000 000 ток./день",
    },
    {
        "key": "openrouter",
        "title": "OpenRouter",
        "url": "https://openrouter.ai/keys",
        "env": "OPENROUTER_API_KEY",
        "prefix": "sk-or-v1-…",
        "need": "Google / GitHub / email",
        "steps": ["войти → Create Key → имя", "скопировать sk-or-v1-…"],
        "limits": "20 запр./мин · 50/день на :free (1 000 после разовой покупки $10)",
    },
    {
        "key": "mistral",
        "title": "Mistral",
        "url": "https://console.mistral.ai/api-keys",
        "env": "MISTRAL_API_KEY",
        "prefix": "(32 символа)",
        "need": "email + подтверждение телефона; план Experiment, без карты",
        "steps": ["зарегистрироваться, подтвердить email и телефон",
                  "убедиться, что план Experiment (карту не привязывать)",
                  "API Keys → Create new key"],
        "limits": "~1 запр./сек · 500K ток./мин · до ~1 млрд ток./мес",
    },
]

BOLD, DIM, MINT, RESET = "\033[1m", "\033[2m", "\033[38;5;79m", "\033[0m"


def show(items: list[dict]) -> None:
    for p in items:
        print(f"\n{BOLD}{p['title']}{RESET}  {MINT}{p['url']}{RESET}")
        print(f"  нужно: {p['need']}")
        print(f"  ключ:  {p['prefix']}   →  в .env: {BOLD}{p['env']}{RESET}")
        print(f"  лимиты: {DIM}{p['limits']}{RESET}")
        for i, s in enumerate(p["steps"], 1):
            print(f"    {i}. {s}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Где взять бесплатные API-ключи")
    ap.add_argument("provider", nargs="?", help="gemini | groq | cerebras | openrouter | mistral")
    ap.add_argument("--print", dest="noopen", action="store_true",
                    help="не открывать браузер")
    args = ap.parse_args()

    if args.provider:
        items = [p for p in PROVIDERS if p["key"] == args.provider.lower()]
        if not items:
            ap.error("неизвестный провайдер; варианты: "
                     + ", ".join(p["key"] for p in PROVIDERS))
    else:
        items = PROVIDERS

    print(f"\n{BOLD}Бесплатные API-ключи{RESET} — карта не нужна, "
          f"быстрее всего начать с Gemini или Groq (ключ за минуту).\n"
          f"Достаточно любого одного ключа, чтобы чат заработал.")
    show(items)

    if not args.noopen:
        opened = 0
        for p in items:
            try:
                if webbrowser.open(p["url"]):
                    opened += 1
            except Exception:
                pass
        print(f"\n{MINT}Открыто в браузере: {opened}{RESET}. "
              f"Скопированные ключи впишите так:")
    else:
        print(f"\n{MINT}Вписать ключи:{RESET}")

    print("  Windows:  launcher.cmd --keys    (сервер перечитает .env сам)")
    print("  вручную:  строки GROQ_API_KEY=... в файле .env")
    print(f"  проверить: {BOLD}python doctor.py{RESET}   "
          f"или {BOLD}python cli.py --status{RESET}")
    print(f"\n{DIM}Подробная шпаргалка и разбор ошибок — GET-KEYS.md{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
