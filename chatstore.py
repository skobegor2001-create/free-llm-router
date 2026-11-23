r"""История чатов — локальное хранилище в SQLite.

Где лежит база (в порядке приоритета):
    CHAT_DB=/путь/к/chats.db            — явно указано в .env
    %LOCALAPPDATA%\FreeLLMRouter\data\chats.db   — Windows-установка
    <папка приложения>/data/chats.db     — портативная версия и Linux

База лежит ВНЕ папки программы: при обновлении и переустановке история
не теряется. Удаляется только вручную или при полном удалении.

Почему SQLite, а не JSON-файл: при потоковом ответе сообщение сохраняется
много раз (по мере поступления текста), JSON пришлось бы каждый раз
перезаписывать целиком — на длинных диалогах это заметно. Здесь же
обновление одной строки.

Таблицы:
    chats(id, title, created_at, updated_at, provider, model, files)
    messages(id, chat_id, role, content, meta, created_at)

Всё только для веб-чата. Содержимое не покидает эту машину.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Текст приложенных файлов храним, чтобы «Продолжить» и вопросы по документу
# работали и в открытом позже диалоге. Но база чатов не должна пухнуть:
MAX_FILE_TEXT = 150_000        # символов на один файл
MAX_CHAT_TEXT = 400_000        # символов на весь диалог
_DB_PATH: str | None = None          # вычисляется один раз, лениво

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats(
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    provider    TEXT,
    model       TEXT,
    files       TEXT
);
CREATE TABLE IF NOT EXISTS messages(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    meta        TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT,
    chars       INTEGER DEFAULT 0,
    pages       INTEGER DEFAULT 0,
    text        TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_files_chat ON files(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_chat_upd ON chats(updated_at DESC);
"""


def _path() -> str:
    """Путь к базе: CHAT_DB из окружения иначе <приложение>/data/chats.db."""
    global _DB_PATH
    if _DB_PATH:
        return _DB_PATH
    env = (os.getenv("CHAT_DB") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir() or env.endswith(("/", "\\")):
            p = p / "chats.db"
        _DB_PATH = str(p)
    else:
        base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_DATA_HOME")
        if base and os.getenv("LOCALAPPDATA"):
            _DB_PATH = str(Path(base) / "FreeLLMRouter" / "data" / "chats.db")
        else:
            _DB_PATH = str(_HERE / "data" / "chats.db")
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(_path(), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")      # не блокирует чтение при записи
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(_SCHEMA)
    return c


def reset_path() -> None:
    """Сбросить закешированный путь (после смены CHAT_DB в .env)."""
    global _DB_PATH
    _DB_PATH = None


def _now() -> float:
    return time.time()


def _title_from(text: str, limit: int = 60) -> str:
    """Первая строка вопроса как заголовок диалога."""
    t = " ".join((text or "").split())
    if not t:
        return "Без названия"
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------- диалоги
def create(title: str = "", provider: str | None = None,
           model: str | None = None) -> dict:
    cid = uuid.uuid4().hex[:12]
    ts = _now()
    with _connect() as c:
        c.execute(
            "INSERT INTO chats(id,title,created_at,updated_at,provider,model,files) "
            "VALUES(?,?,?,?,?,?,'[]')",
            (cid, title or "Новый диалог", ts, ts, provider, model),
        )
    return {"id": cid, "title": title or "Новый диалог",
            "created_at": ts, "updated_at": ts,
            "provider": provider, "model": model, "files": []}


def list_chats(query: str = "", limit: int = 200) -> list[dict]:
    """Список диалогов для боковой панели: свежие сверху."""
    sql = ("SELECT c.id, c.title, c.created_at, c.updated_at, c.provider, c.model, "
           "c.files, (SELECT COUNT(*) FROM messages m WHERE m.chat_id=c.id) AS n, "
           "(SELECT substr(m2.content,1,120) FROM messages m2 WHERE m2.chat_id=c.id "
           "   AND m2.role='assistant' AND m2.content<>'' ORDER BY m2.id DESC LIMIT 1) AS tail "
           "FROM chats c ")
    args: list = []
    q = (query or "").strip()
    if q:
        sql += ("WHERE c.title LIKE ? OR EXISTS(SELECT 1 FROM messages m3 "
                "WHERE m3.chat_id=c.id AND m3.content LIKE ?) ")
        args += [f"%{q}%", f"%{q}%"]
    sql += "ORDER BY c.updated_at DESC LIMIT ?"
    args.append(int(limit))
    with _connect() as c:
        rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item["files"] = json.loads(item.get("files") or "[]")
        except Exception:
            item["files"] = []
        item["tail"] = " ".join((item.get("tail") or "").split())[:110]
        out.append(item)
    return out


def get(cid: str) -> dict | None:
    """Диалог целиком: метаданные + сообщения (для восстановления в чат)."""
    with _connect() as c:
        row = c.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        msgs = c.execute(
            "SELECT id, role, content, meta, created_at FROM messages "
            "WHERE chat_id=? ORDER BY id", (cid,)).fetchall()
    chat = dict(row)
    try:
        chat["files"] = json.loads(chat.get("files") or "[]")
    except Exception:
        chat["files"] = []
    out_msgs = []
    for m in msgs:
        d = dict(m)
        try:
            d["meta"] = json.loads(d.get("meta") or "null")
        except Exception:
            d["meta"] = None
        out_msgs.append(d)
    chat["messages"] = out_msgs
    chat["files"] = get_files(cid)
    return chat


def rename(cid: str, title: str) -> bool:
    t = " ".join((title or "").split())[:120]
    if not t:
        return False
    with _connect() as c:
        cur = c.execute("UPDATE chats SET title=?, updated_at=? WHERE id=?",
                        (t, _now(), cid))
    return cur.rowcount > 0


def delete(cid: str) -> bool:
    with _connect() as c:
        c.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
        c.execute("DELETE FROM files WHERE chat_id=?", (cid,))
        cur = c.execute("DELETE FROM chats WHERE id=?", (cid,))
    return cur.rowcount > 0


def clear_all() -> int:
    with _connect() as c:
        n = c.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        c.execute("DELETE FROM messages")
        c.execute("DELETE FROM files")
        c.execute("DELETE FROM chats")
    return int(n)


def touch(cid: str, provider: str | None = None,
          model: str | None = None, files: list | None = None) -> None:
    """Отметить активность диалога (для сортировки «свежие сверху»)."""
    sets, args = ["updated_at=?"], [_now()]
    if provider:
        sets.append("provider=?"); args.append(provider)
    if model:
        sets.append("model=?"); args.append(model)
    if files is not None:
        names = [f.get("name") for f in files if isinstance(f, dict) and f.get("name")]
        sets.append("files=?"); args.append(json.dumps(names, ensure_ascii=False))
    args.append(cid)
    with _connect() as c:
        c.execute(f"UPDATE chats SET {', '.join(sets)} WHERE id=?", args)


# ---------------------------------------------------------------- сообщения
def add_message(cid: str, role: str, content: str, meta: dict | None = None) -> int:
    ts = _now()
    with _connect() as c:
        cur = c.execute(
            "INSERT INTO messages(chat_id,role,content,meta,created_at) VALUES(?,?,?,?,?)",
            (cid, role, content or "",
             json.dumps(meta, ensure_ascii=False) if meta else None, ts))
        mid = int(cur.lastrowid)
        c.execute("UPDATE chats SET updated_at=? WHERE id=?", (ts, cid))
    return mid


def update_message(mid: int, content: str, meta: dict | None = None) -> None:
    """Дописать потоковый ответ (вызывается с замедлением из сервера)."""
    with _connect() as c:
        if meta is None:
            c.execute("UPDATE messages SET content=? WHERE id=?", (content or "", mid))
        else:
            c.execute("UPDATE messages SET content=?, meta=? WHERE id=?",
                      (content or "", json.dumps(meta, ensure_ascii=False), mid))


def delete_message(mid: int) -> None:
    with _connect() as c:
        c.execute("DELETE FROM messages WHERE id=?", (mid,))




# ---------------------------------------------------------------- файлы диалога
def save_files(cid: str, files: list[dict]) -> list[str]:
    """Запоминает текст приложенных файлов (набор заменяется целиком).

    Возвращает имена файлов, чей текст НЕ сохранён из-за лимита — чтобы
    интерфейс честно предупредил: в старом диалоге документ придётся приложить снова.
    """
    names = [f.get("name") for f in files if isinstance(f, dict) and f.get("name")]
    skipped: list[str] = []
    ts = _now()
    with _connect() as c:
        c.execute("DELETE FROM files WHERE chat_id=?", (cid,))
        total = 0
        for f in files or []:
            if not isinstance(f, dict) or not f.get("name"):
                continue
            text = str(f.get("text") or "")
            if len(text) > MAX_FILE_TEXT or total + len(text) > MAX_CHAT_TEXT:
                text = ""
                if f["name"] not in skipped:
                    skipped.append(f["name"])
            else:
                total += len(text)
            c.execute(
                "INSERT INTO files(chat_id,name,kind,chars,pages,text,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (cid, str(f["name"]), str(f.get("kind") or ""),
                 int(f.get("chars") or len(text)), int(f.get("pages") or 0),
                 text or None, ts))
        c.execute("UPDATE chats SET files=? WHERE id=?",
                  (json.dumps(names, ensure_ascii=False), cid))
    return skipped


def get_files(cid: str) -> list[dict]:
    """Файлы диалога в том же виде, в каком их принимает /api/chat."""
    with _connect() as c:
        rows = c.execute(
            "SELECT name,kind,chars,pages,text FROM files WHERE chat_id=? ORDER BY id",
            (cid,)).fetchall()
    out = []
    for r in rows:
        out.append({"name": r["name"], "kind": r["kind"] or "",
                    "chars": r["chars"] or 0, "pages": r["pages"] or 0,
                    "text": r["text"] or ""})
    return out

def last_assistant(cid: str) -> tuple[int, str] | None:
    """(id, текст) последнего сообщения ассистента — для «Продолжить»."""
    with _connect() as c:
        row = c.execute(
            "SELECT id, content FROM messages WHERE chat_id=? AND role='assistant' "
            "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    return (int(row["id"]), row["content"] or "") if row else None


def last_messages(cid: str, limit: int = 40) -> list[dict]:
    """Последние сообщения — чтобы продолжить диалог, даже если UI потерял историю."""
    with _connect() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE chat_id=? AND content<>'' "
            "ORDER BY id DESC LIMIT ?", (cid, int(limit))).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------------------------------------------------------------- служебное
def stats() -> dict:
    p = _path()
    with _connect() as c:
        chats = c.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        msgs = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    size = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            size += os.path.getsize(p + suffix)
        except OSError:
            pass
    return {"path": p, "chats": int(chats), "messages": int(msgs), "bytes": size}


def export_all() -> list[dict]:
    """Выгрузка всех диалогов в JSON (для бэкапа/переноса)."""
    out = []
    for item in list_chats(limit=10000):
        full = get(item["id"])
        if full:
            out.append(full)
    return out


def import_all(items: list[dict]) -> int:
    """Импорт диалогов; конфликтующие id получают новое имя.

    Возвращает количество добавленных диалогов.
    """
    added = 0
    with _connect() as c:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or uuid.uuid4().hex[:12])
            if c.execute("SELECT 1 FROM chats WHERE id=?", (cid,)).fetchone():
                cid = uuid.uuid4().hex[:12]
            created = float(item.get("created_at") or _now())
            updated = float(item.get("updated_at") or created)
            try:
                files = json.dumps(item.get("files") or [], ensure_ascii=False)
            except Exception:
                files = "[]"
            c.execute("INSERT INTO chats(id,title,created_at,updated_at,provider,model,files) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (cid, str(item.get("title") or "Импорт"), created, updated,
                       item.get("provider"), item.get("model"), files))
            for f in item.get("files") or []:
                if not isinstance(f, dict) or not f.get("name"):
                    continue
                c.execute("INSERT INTO files(chat_id,name,kind,chars,pages,text,created_at) "
                          "VALUES(?,?,?,?,?,?,?)",
                          (cid, str(f["name"]), str(f.get("kind") or ""),
                           int(f.get("chars") or 0), int(f.get("pages") or 0),
                           f.get("text") or None, created))
            for m in item.get("messages") or []:
                if not isinstance(m, dict):
                    continue
                meta = m.get("meta")
                c.execute("INSERT INTO messages(chat_id,role,content,meta,created_at) "
                          "VALUES(?,?,?,?,?)",
                          (cid, str(m.get("role") or "user"), str(m.get("content") or ""),
                           json.dumps(meta, ensure_ascii=False) if meta else None,
                           float(m.get("created_at") or created)))
            added += 1
    return added


def search(query: str, limit: int = 50) -> list[dict]:
    """Поиск по всем сообщениям с фрагментом вокруг совпадения."""
    q = (query or "").strip()
    if not q:
        return []
    with _connect() as c:
        rows = c.execute(
            "SELECT m.id, m.chat_id, m.role, m.content, m.created_at, c.title "
            "FROM messages m JOIN chats c ON c.id=m.chat_id "
            "WHERE m.content LIKE ? ORDER BY m.created_at DESC LIMIT ?",
            (f"%{q}%", int(limit))).fetchall()
    out, seen = [], set()
    for r in rows:
        if r["chat_id"] in seen:          # один фрагмент на диалог
            continue
        seen.add(r["chat_id"])
        text = r["content"] or ""
        pos = text.lower().find(q.lower())
        start = max(0, pos - 60)
        snippet = ("…" if start else "") + text[start:start + 160].replace("\n", " ")
        out.append({"chat_id": r["chat_id"], "title": r["title"], "role": r["role"],
                    "snippet": snippet, "created_at": r["created_at"]})
    return out
