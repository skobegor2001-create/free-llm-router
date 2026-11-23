"""CLI для Free LLM Router — проверка лимитов, чат из терминала.

Примеры:
    python cli.py --status
    python cli.py "Объясни, что такое векторная база данных"
    python cli.py "Напиши quicksort" -p groq -m openai/gpt-oss-120b
    python cli.py --repl              # интерактивный режим
    echo "2+2?" | python cli.py -     # из stdin
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

import router as rt

BOLD, DIM, MINT, YELLOW, RED, RESET = (
    "\033[1m", "\033[2m", "\033[38;5;79m", "\033[33m", "\033[31m", "\033[0m",
)


def print_status() -> None:
    st = rt.status()
    print(f"{BOLD}Провайдеры (порядок автопереключения: {' → '.join(st['order'])}){RESET}\n")
    for p in st["providers"]:
        mark = f"{MINT}✓ ключ есть{RESET}" if p["configured"] else f"{YELLOW}· ключа нет{RESET}"
        pause = f"  {RED}⏸ пауза {p['cooldown']} c{RESET}" if p["cooldown"] else ""
        print(f"  {p['title']:<15} {mark:<22}{pause}")
        print(f"  {DIM}{p['env']:<20} {p['limits']}{RESET}")
        print(f"  {DIM}ключ: {p['signup']}{RESET}\n")
    ready = [p["name"] for p in st["providers"] if p["configured"]]
    if not ready:
        print(f"{YELLOW}Ключей нет. Впишите хотя бы один в .env — и всё заработает.{RESET}")


async def ask(prompt: str, args: argparse.Namespace) -> None:
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": prompt})

    kwargs = dict(
        provider=args.provider if args.provider != "auto" else None,
        model=args.model if args.model != "auto" else None,
        temperature=args.temperature,
    )

    if args.no_stream:
        try:
            res = await rt.chat(messages, **kwargs)
        except rt.NoProviders as e:
            print(f"{RED}{e}{RESET}")
            return
        print(res["text"])
        print(f"{DIM}\n[{res['provider']} · {res['model']}]{RESET}")
        return

    printed_meta = False
    async for ev in rt.stream_chat(messages, **kwargs):
        if ev["type"] == "meta":
            print(f"{DIM}[{ev['provider']} · {ev['model']}]{RESET}")
            printed_meta = True
        elif ev["type"] == "delta":
            print(ev["text"], end="", flush=True)
        elif ev["type"] == "error":
            print(f"\n{RED}{ev['message']}{RESET}")
        elif ev["type"] == "done":
            print()
            if not printed_meta:
                print(f"{DIM}[пустой ответ]{RESET}")


async def repl(args: argparse.Namespace) -> None:
    print(f"{BOLD}Free LLM Router · интерактивный режим{RESET}  "
          f"{DIM}(exit — выход, /status — провайдеры){RESET}\n")
    while True:
        try:
            prompt = input(f"{MINT}вы ›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not prompt:
            continue
        if prompt in ("exit", "quit", "/exit"):
            break
        if prompt == "/status":
            print_status()
            continue
        await ask(prompt, args)
        print()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Free LLM Router CLI — бесплатные нейросети с автопереключением"
    )
    ap.add_argument("prompt", nargs="?", help="запрос; '-' — читать из stdin")
    ap.add_argument("-p", "--provider", default="auto",
                    help="groq | cerebras | gemini | openrouter | mistral | auto")
    ap.add_argument("-m", "--model", default="auto", help="модель (по умолчанию auto)")
    ap.add_argument("-s", "--system", default=None, help="системный промпт")
    ap.add_argument("-t", "--temperature", type=float, default=0.7)
    ap.add_argument("--repl", action="store_true", help="интерактивный чат")
    ap.add_argument("--status", action="store_true", help="показать провайдеров и ключи")
    ap.add_argument("--no-stream", action="store_true", help="без потокового вывода")
    args = ap.parse_args()

    if args.status:
        print_status()
        return

    if args.repl:
        asyncio.run(repl(args))
        return

    prompt = args.prompt
    if prompt == "-":
        prompt = sys.stdin.read()
    if not prompt:
        ap.print_help()
        return

    asyncio.run(ask(prompt, args))


if __name__ == "__main__":
    main()
