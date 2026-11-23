"""Free LLM Router — лаунчер для Windows 11.

Только стандартная библиотека: работает сразу после распаковки, без установки.

Режимы:
    python launcher.py                 окно с меню (двойной клик по ярлыку)
    python launcher.py --open          запустить (если нужно) и открыть чат
    python launcher.py --stop          остановить сервер
    python launcher.py --restart       перезапустить
    python launcher.py --status        показать состояние провайдеров
    python launcher.py --keys          вписать API-ключи в .env
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_ID = "free-llm-router"
ROOT = Path(__file__).resolve().parents[2]          # ...\FreeLLMRouter
APP_DIR = ROOT / "app" if (ROOT / "app" / "server.py").exists() else ROOT
DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "FreeLLMRouter" / "data"

PROVIDER_KEYS = [
    ("GROQ_API_KEY", "Groq      ", "console.groq.com/keys"),
    ("CEREBRAS_API_KEY", "Cerebras  ", "cloud.cerebras.ai"),
    ("GEMINI_API_KEY", "Gemini    ", "aistudio.google.com/apikey"),
    ("OPENROUTER_API_KEY", "OpenRouter", "openrouter.ai/keys"),
    ("MISTRAL_API_KEY", "Mistral   ", "console.mistral.ai/api-keys"),
]


# --------------------------------------------------------------------------
# Консоль
# --------------------------------------------------------------------------

def setup_console() -> None:
    if os.name == "nt":
        try:
            os.system("chcp 65001 >nul")
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def resolve_python() -> Path | None:
    """Где взять интерпретатор для дочерних процессов (server.py, doctor.py).

    Порядок: portable-runtime → подсказка установщика (FREELMR_PYTHON) →
    папка python/ рядом с программой → текущий интерпретатор.
    """
    runtime = ROOT / "runtime" / "python.exe"
    if runtime.exists():
        return runtime
    hint = os.environ.get("FREELMR_PYTHON", "").strip().strip('"')
    if hint and Path(hint).exists():
        return Path(hint)
    local = ROOT / "python" / "python.exe"
    if local.exists():
        return local
    exe = Path(sys.executable)
    if exe.exists():
        return exe
    return None


def python_exe() -> Path:
    exe = resolve_python()
    if exe is None:
        raise SystemExit("Python не найден. Запустите установщик Free LLM Router ещё раз.")
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return cand
    return exe


# --------------------------------------------------------------------------
# Сеть / порт
# --------------------------------------------------------------------------

def http_json(url: str, timeout: float = 1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def port_file() -> Path:
    return DATA / "port.txt"


def read_port() -> int:
    try:
        return int(port_file().read_text(encoding="utf-8").strip())
    except Exception:
        return 8000


def save_port(port: int) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        port_file().write_text(str(port), encoding="utf-8")
    except Exception:
        pass


def health_at(port: int):
    h = http_json(f"http://127.0.0.1:{port}/health")
    if h and h.get("app") == APP_ID:
        return h
    return None


def find_port():
    """Порт работающего экземпляра нашего сервера (или None)."""
    try:
        env_port = int(read_env().get("PORT") or 0)
    except ValueError:
        env_port = 0
    candidates = [read_port(), env_port] + list(range(8000, 8021))
    candidates = [p for p in candidates if p]
    for port in candidates:
        if health_at(port):
            return port
    return None


# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

def env_path() -> Path:
    return APP_DIR / ".env"


def read_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    p = env_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def set_env_value(key: str, value: str) -> None:
    p = env_path()
    lines = (p.read_text(encoding="utf-8", errors="replace").splitlines()
             if p.exists() else [])
    out, done = [], False
    for line in lines:
        if line.strip().startswith(key + "="):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def ready_count() -> tuple[int, int]:
    vals = read_env()
    return sum(1 for k, _, _ in PROVIDER_KEYS if vals.get(k)), len(PROVIDER_KEYS)


# --------------------------------------------------------------------------
# Жизненный цикл сервера
# --------------------------------------------------------------------------

def server_env() -> dict[str, str]:
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    return e


def start() -> int | None:
    """Запускает сервер в фоне (без окна) и ждёт готовности. Возвращает порт."""
    port = find_port()
    if port:
        return port

    DATA.mkdir(parents=True, exist_ok=True)
    log = DATA / "server.log"
    env_vals = read_env()
    try:
        want_port = int(env_vals.get("PORT") or 8000)
    except ValueError:
        want_port = 8000
    args = [str(python_exe()), str(APP_DIR / "server.py"), "--port", str(want_port)]
    if env_vals.get("HOST"):
        args += ["--host", env_vals["HOST"]]

    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        with open(log, "ab") as f:
            proc = subprocess.Popen(args, cwd=str(APP_DIR), env=server_env(),
                                    stdout=f, stderr=subprocess.STDOUT, creationflags=flags)
    else:
        with open(log, "ab") as f:
            proc = subprocess.Popen(args, cwd=str(APP_DIR), env=server_env(),
                                    stdout=f, stderr=subprocess.STDOUT,
                                    start_new_session=True)

    for _ in range(320):                       # до ~80 секунд (первый старт на HDD)
        time.sleep(0.25)
        port = find_port()
        if port:
            save_port(port)
            return port
        if proc.poll() is not None:
            break
    port = find_port()
    if not port:
        explain_failure(log)
    return port


def log_tail(log: Path, lines: int = 25) -> str:
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "(журнал недоступен)"
    return "\n".join(text.splitlines()[-lines:]) or "(журнал пуст)"


def explain_failure(log: Path) -> None:
    """Показывает хвост журнала и переводит типовые ошибки на человеческий."""
    tail = log_tail(log)
    print("\n  --- последние строки журнала ---")
    print("  " + tail.replace("\n", "\n  "))
    print("  --------------------------------\n")

    hints: list[str] = []
    low = tail.lower()
    if "modulenotfounderror" in low:
        m = re.search(r"no module named '?([\w.]+)'?", low)
        name = m.group(1) if m else "?"
        if name in ("fastapi", "uvicorn", "httpx", "dotenv", "starlette", "pydantic", "anyio"):
            hints.append("Не установлены Python-пакеты. Запустите установщик Free LLM Router "
                         "ещё раз (он поставит их из офлайн-копии).")
        else:
            hints.append(f"Не найден модуль «{name}» — похоже, файлы программы повреждены. "
                         "Переустановите Free LLM Router.")
    if "permissionerror" in low or "winerror 5" in low:
        hints.append("Нет доступа к файлу. Возможно, сервер ещё останавливается — "
                     "подождите пару секунд и попробуйте снова.")
    if "address already in use" in low or "errno 10048" in low:
        hints.append("Порт занят другой программой — среда должна была взять следующий. "
                     "Перезапустите: меню → 3.")
    if "no such file or directory" in low and ".env" in low:
        hints.append("Повреждён .env — удалите его, он создастся заново из шаблона.")
    hints.append(f"Полный журнал: {log}")
    for h in hints:
        print(f"  • {h}")
    print()


def stop() -> bool:
    h = None
    port = find_port()
    if port:
        h = health_at(port)
    pid = int(h["pid"]) if h and h.get("pid") else None
    if not pid:
        return False
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.kill(pid, 15)
        except Exception:
            return False
    for _ in range(40):
        time.sleep(0.15)
        if not find_port():
            break
    return True


def open_chat(port: int | None = None) -> None:
    port = port or find_port()
    if port:
        webbrowser.open(f"http://127.0.0.1:{port}")
    else:
        print("  Сервер ещё не запущен — сначала пункт 1.")


def status_block() -> None:
    port = find_port()
    if not port:
        print("  Сервер не запущен.")
        ready, total = ready_count()
        print(f"  Ключей в .env: {ready} из {total}")
        return
    h = health_at(port) or {}
    print(f"  Сервер: http://127.0.0.1:{port}   (PID {h.get('pid')}, uptime {h.get('uptime')} c)")
    print(f"  OpenAI-шлюз для агента: http://127.0.0.1:{port}/v1")
    st = http_json(f"http://127.0.0.1:{port}/api/status") or {}
    for p in st.get("providers", []):
        key = "есть " if p["configured"] else "нет  "
        pause = f"  пауза {p['cooldown']} c" if p.get("cooldown") else ""
        print(f"    {p['title']:<14} ключ: {key} {p['limits']}{pause}")
        if p["configured"]:
            print(f"      модели: {', '.join(p['models'])}")


def ask_index_db() -> None:
    """Выбор базы SQLite: диалог Windows или ручной ввод пути."""
    chosen = ""
    if os.name == "nt":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
            "$d = New-Object System.Windows.Forms.OpenFileDialog;"
            "$d.Title = 'Укажите базу SQLite с индексом PDF';"
            "$d.Filter = 'SQLite (*.sqlite3;*.db)|*.sqlite3;*.db|Все файлы (*.*)|*.*';"
            "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.FileName }"
        )
        try:
            import subprocess
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                               capture_output=True, text=True, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            chosen = (r.stdout or "").strip().splitlines()
            chosen = chosen[-1].strip() if chosen else ""
        except Exception:
            chosen = ""

    if not chosen:
        try:
            chosen = input("  Путь к .sqlite3 (Enter — отмена): ").strip().strip('"')
        except (EOFError, KeyboardInterrupt):
            print()
            return
    if not chosen:
        return

    from pathlib import Path as _P
    if not _P(chosen).exists():
        print(f"  Файл не найден: {chosen}")
        return
    set_env_value("INDEX_DB", chosen)
    print(f"  Сохранено в .env: INDEX_DB={chosen}")
    port = find_port()
    if port:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/reload", method="POST")
            urllib.request.urlopen(req, timeout=5).read()
            print("  Сервер перечитал настройки.")
        except Exception:
            pass


def ask_keys() -> None:
    vals = read_env()
    missing = [(k, t, u) for k, t, u in PROVIDER_KEYS if not vals.get(k)]
    if not missing:
        print("  Все пять ключей уже вписаны.")
        return
    print("\n  Впишите ключ(и). Enter — пропустить; карта не нужна, регистрация — минута.\n")
    added = 0
    for key, title, url in missing:
        print(f"    {title}  где взять: {url}")
        try:
            value = input(f"    {key} = ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if value:
            set_env_value(key, value)
            added += 1
            print("    сохранено\n")
        else:
            print()
    port = find_port() if added else None
    if port:                                   # сервер работает — перечитаем .env
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/reload", method="POST")
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
    print(f"  Готово: добавлено ключей — {added}."
          + (" Сервер перечитал .env." if added else ""))


# --------------------------------------------------------------------------
# Меню
# --------------------------------------------------------------------------

BANNER = r"""
   _______                        _        _          ____             _
  |  ____|                       | |      | |        |  _ \           | |
  | |__ _ __ ___  ___    ______  | |      | |_ __ ___| |_) |___  _   _| |_ ___ _ __
  |  __| '__/ _ \/ _ \  |______| | |      | | '_ ` _ \  _ < / _ \| | | | __/ _ \ '__|
  | |  | | |  __/  __/           | |____  | | | | | | |_) | (_) | |_| | ||  __/ |
  |_|  |_|  \___|\___|           |______| |_|_| |_| |____/ \___/ \__,_|\__\___|_|

  бесплатные нейросети: Groq · Cerebras · Gemini · OpenRouter · Mistral
"""


def menu() -> None:
    while True:
        print()
        port = find_port()
        state = f"запущен на порту {port}" if port else "остановлен"
        ready, total = ready_count()
        print(f"  Статус: {state} · ключей: {ready}/{total}")
        print()
        print("    1. Запустить и открыть чат")
        print("    2. Остановить сервер")
        print("    3. Перезапустить")
        print("    4. Открыть чат в браузере")
        print("    5. Проверить подключение (живой запрос)")
        print("    6. Вписать API-ключи")
        print("    8. Указать базу SQLite с индексом PDF")
        print("    7. Подробный статус")
        print("    0. Выход")
        print()
        try:
            choice = input("  Выбор: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice == "0":
            return
        elif choice == "1":
            print("\n  Запускаю...")
            port = start()
            if port:
                print(f"  Готово: http://127.0.0.1:{port}")
                open_chat(port)
                return
            print("  Не удалось запустить. Лог: " + str(DATA / "server.log"))
        elif choice == "2":
            print("\n  Останавливаю..." + ("" if stop() else " сервер и так не работал."))
        elif choice == "3":
            print("\n  Перезапускаю...")
            stop()
            port = start()
            print(f"  Готово: http://127.0.0.1:{port}" if port else "  Не удалось запустить.")
        elif choice == "4":
            port = start()
            open_chat(port)
        elif choice == "5":
            print()
            subprocess.call([str(python_exe()), str(APP_DIR / "doctor.py")], cwd=str(APP_DIR))
        elif choice == "6":
            ask_keys()
        elif choice == "8":
            ask_index_db()
        elif choice == "7":
            print()
            status_block()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    setup_console()
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--open", action="store_true", help="запустить и открыть чат")
    ap.add_argument("--background", action="store_true", help="запустить без меню")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--keys", action="store_true")
    args = ap.parse_args()

    if args.stop:
        print("  Сервер остановлен." if stop() else "  Сервер и так не работал.")
        return 0   # для установщика: «нечего останавливать» — это не ошибка
    if args.status:
        status_block()
        return 0
    if args.keys:
        ask_keys()
        return 0
    if args.restart:
        stop()
        port = start()
        if port:
            print(f"  Готово: http://127.0.0.1:{port}")
            return 0
        print("  Не удалось запустить.")
        return 1
    if args.open or args.background:
        print(BANNER)
        ready, total = ready_count()
        if ready == 0:
            print("  Ключей пока нет — впишите хотя бы один (иначе некому отвечать).")
            ask_keys()
            print()
        port = start()
        if not port:
            print("  Не удалось запустить. Лог: " + str(DATA / "server.log"))
            return 1
        print(f"  Сервер: http://127.0.0.1:{port}")
        print(f"  Шлюз для агента: http://127.0.0.1:{port}/v1")
        print("  Открываю чат в браузере...")
        open_chat(port)
        return 0

    print(BANNER)
    menu()
    return 0


if __name__ == "__main__":
    sys.exit(main())
