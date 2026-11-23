"""Free LLM Router — интерактивное удаление (Windows, только stdlib).

Вызывается из Uninstall.exe после остановки сервера и удаления ярлыков.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def ask(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"  {prompt} {suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not answer:
        return default
    return answer in ("y", "yes", "д", "да")


def main() -> int:
    if os.name == "nt":
        try:
            os.system("chcp 65001 >nul")
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print()
    print("  Free LLM Router — удаление")
    print()

    env_file = ROOT / ".env"
    keys_present = False
    if env_file.exists():
        try:
            text = env_file.read_text(encoding="utf-8", errors="replace")
            keys_present = any(
                line.split("=", 1)[1].strip()
                for line in text.splitlines()
                if "=" in line and line.strip().upper().endswith("_API_KEY=")
                or ("_API_KEY=" in line and line.split("=", 1)[1].strip())
            )
        except Exception:
            pass

    if keys_present:
        print("  В папке есть сохранённые API-ключи (.env).")
        if ask("Удалить ключи и настройки?", default=False):
            try:
                env_file.unlink()
                print("  Ключи удалены.")
            except Exception as exc:
                print(f"  Не удалось удалить .env: {exc}")

    data = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FreeLLMRouter" / "data"
    if data.exists():
        history = data / "chats.db"
        if history.exists():
            print("  В папке данных есть история чатов (chats.db).")
            if ask("Удалить историю чатов? (ключи и настройки это не затрагивает)", default=False):
                try:
                    for name in ("chats.db", "chats.db-wal", "chats.db-shm"):
                        (data / name).unlink(missing_ok=True)
                    print("  История чатов удалена.")
                except Exception as exc:
                    print(f"  Не удалось удалить историю: {exc}")
            else:
                print(f"  История сохранена: {history}")
        try:
            # журналы удаляем, но папку с базой истории не трогаем
            for name in ("server.log", "port"):
                target = data / name
                if target.is_file():
                    target.unlink(missing_ok=True)
            print("  Журналы и порт удалены.")
        except Exception:
            pass

    print()
    print(f"  Папка программы останется: {ROOT}")
    print("  Её можно удалить вручную в любой момент.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
