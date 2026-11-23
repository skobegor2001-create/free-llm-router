"""Free LLM Router — веб-сервер.

Что даёт:
  • Веб-чат с потоковыми ответами:            http://localhost:8000/
  • OpenAI-совместимый API для агентов:        http://localhost:8000/v1
  • Служебные ручки статуса и списка моделей.

Запуск:
    python server.py            # или: ./start.sh
"""

from __future__ import annotations

# Запуск может идти из встроенного Python с файлом ._pth: в этом режиме папка
# скрипта не попадает в sys.path — добавляем её явно, до локальных импортов.
from pathlib import Path as _Path
import sys as _sys
_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import chatstore
import filetext
import indexdb
import router as rt

BASE = Path(__file__).resolve().parent
VERSION = "1.1.1"

app = FastAPI(title="Free LLM Router", version=VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_state = {"started": time.time()}


# --------------------------------------------------------------------------
# UI и служебные ручки
# --------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "app": "free-llm-router",
        "version": VERSION,
        "pid": os.getpid(),
        "providers_ready": sum(1 for p in rt.PROVIDERS.values() if p.key),
        "uptime": int(time.time() - _state["started"]),
    }


@app.get("/api/status")
async def api_status() -> dict:
    """Состояние провайдеров, ключей, лимитов и «пауз» после 429."""
    return rt.status()


def request_client_ip(request: Request) -> str:
    host = (request.client.host if request.client else "") or ""
    return "127.0.0.1" if host in ("::1", "localhost") else host


def _write_env(key: str, value: str) -> None:
    """Пишет ключ в .env, сохраняя остальные строки."""
    path = rt.ENV_PATH
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.strip().startswith(key + "="):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


@app.post("/api/index/pick")
async def api_index_pick(request: Request) -> dict:
    """Открывает системный диалог выбора файла на машине пользователя.

    Работает, только если сервер запущен на том же компьютере, с которого открыт
    браузер (обычный случай для установщика). При отказе — путь вводится вручную.
    """
    client = request_client_ip(request)
    if client not in ("127.0.0.1", "::1", "localhost"):
        return {"ok": False, "reason": "remote",
                "hint": "Сервер не на этом компьютере — впишите путь к базе вручную."}
    if os.name != "nt":
        return {"ok": False, "reason": "unsupported",
                "hint": "Диалог доступен в Windows-сборке. Впишите путь вручную."}
    script = (
        "Add-Type -AssemblyName System.Windows.Forms | Out-Null;"
        "$d = New-Object System.Windows.Forms.OpenFileDialog;"
        "$d.Title = 'Укажите базу SQLite с индексом PDF';"
        "$d.Filter = 'SQLite (*.sqlite3;*.db;*.sqlite)|*.sqlite3;*.db;*.sqlite|Все файлы (*.*)|*.*';"
        "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.FileName }"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-STA", "-Command", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        path = out.decode("utf-8", "replace").strip().splitlines()
        chosen = path[-1].strip() if path else ""
        if chosen and os.path.exists(chosen):
            return {"ok": True, "path": chosen}
        return {"ok": False, "reason": "cancelled"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "error", "hint": str(exc)[:200]}


@app.post("/api/index/connect")
async def api_index_connect(body: dict) -> dict:
    """Подключает базу: проверяет, сохраняет в .env и применяет сразу."""
    raw = str(body.get("path") or "").strip().strip('"')
    if not raw:
        return {"ok": False, "error": "Путь пустой"}
    path = Path(os.path.expandvars(raw)).expanduser()
    if path.is_dir():
        for name in ("pdf_ai.sqlite3", "pdf_ai.db", "index.db", "index.sqlite3"):
            if (path / name).exists():
                path = path / name
                break
    if not path.exists():
        return {"ok": False, "error": f"Файл не найден: {path}"}
    if path.suffix.lower() not in (".db", ".sqlite3", ".sqlite", ""):
        return {"ok": False, "error": "Это не похоже на файл SQLite (.sqlite3 / .db)"}

    # проверяем, что это SQLite и есть подходящая таблица
    try:
        conn = indexdb._connect(path)
        try:
            sel = indexdb.detect(conn)["selected"]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Не удалось открыть как SQLite: {exc}"}
    if not sel:
        return {"ok": False, "error": "Таблица с текстом не найдена — укажите INDEX_TABLE вручную"}

    indexdb.apply_setting("INDEX_DB", str(path))
    try:
        _write_env("INDEX_DB", str(path))
        saved = True
    except Exception:
        saved = False

    st = indexdb.status()
    return {"ok": True, "saved": saved, "path": str(path), "status": st,
            "profile": sel.get("profile"), "table": sel.get("table"),
            "rows": sel.get("rows"), "documents": sel.get("documents"),
            "dims": sel.get("dims")}


@app.get("/api/index/status")
async def api_index_status() -> dict:
    """Что найдено в вашей SQLite-базе с PDF-индексом."""
    return indexdb.status()


@app.get("/api/index/page")
async def api_index_page(sha: str, page: int) -> dict:
    """Текст страницы из исходного PDF (data/pdfs/<sha>.pdf)."""
    return indexdb.page_text(sha, page)


@app.post("/api/index/search")
async def api_index_search(body: dict) -> dict:
    """Поиск по индексу (только чтение) — для отладки и UI."""
    query = (body.get("query") or "").strip()
    limit = int(body.get("limit") or 5)
    if not query:
        return {"hits": []}
    hits = indexdb.search(query, limit=limit)
    return {"hits": hits, "count": len(hits)}


@app.post("/api/extract")
async def api_extract(request: Request, name: str = "file") -> JSONResponse:
    """Извлекает текст из загруженного файла (PDF/DOCX/TXT/…). Тело — сырые байты."""
    raw = await request.body()
    if not raw:
        return JSONResponse(status_code=400, content={"error": "Пустой файл."})
    if len(raw) > filetext.MAX_BYTES:
        mb = filetext.MAX_BYTES // (1024 * 1024)
        return JSONResponse(status_code=413,
                            content={"error": f"Файл больше {mb} МБ."})
    try:
        result = filetext.extract(name, raw)
    except filetext.ExtractError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 — не роняем сервер из-за файла
        return JSONResponse(status_code=500,
                            content={"error": f"Не удалось прочитать файл: {exc}"})
    result.pop("text", None) if False else None
    return JSONResponse(content=result)


@app.post("/api/reload")
async def api_reload() -> dict:
    """Перечитывает .env без перезапуска сервера."""
    load_dotenv(rt.ENV_PATH, override=True)
    chatstore.reset_path()          # CHAT_DB мог измениться
    rt._models_cache.clear()
    rt._cooldown.clear()
    return {"ok": True, "ready": sum(1 for p in rt.PROVIDERS.values() if p.key)}


@app.get("/api/models")
async def api_models(provider: str | None = None, force: bool = False) -> dict:
    """Живые списки моделей. Без ключа провайдер не трогаем — отдаём дефолт."""
    names = [provider] if provider in rt.PROVIDERS else list(rt.PROVIDERS)
    out = {}
    results = await asyncio.gather(
        *(rt.list_models(n, force=force) for n in names), return_exceptions=True
    )
    for name, res in zip(names, results):
        out[name] = res if isinstance(res, list) else list(rt.PROVIDERS[name].models)
    return {"models": out}


# --------------------------------------------------------------------------
# Веб-чат: потоковые события в формате NDJSON
# --------------------------------------------------------------------------


# --------------------------------------------------------------- история чатов
@app.get("/api/chats")
async def api_chats(q: str = "", limit: int = 200):
    """Список диалогов (свежие сверху) + поиск по заголовку и содержимому."""
    try:
        return {"ok": True, "chats": chatstore.list_chats(q, limit),
                "stats": chatstore.stats()}
    except Exception as e:
        return {"ok": False, "error": str(e), "chats": []}


@app.get("/api/chats/{cid}")
async def api_chat_get(cid: str):
    try:
        chat = chatstore.get(cid)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not chat:
        return {"ok": False, "error": "Диалог не найден"}
    return {"ok": True, "chat": chat}


@app.post("/api/chats")
async def api_chat_create(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    chat = chatstore.create(body.get("title") or "",
                            body.get("provider"), body.get("model"))
    return {"ok": True, "chat": chat}


@app.post("/api/chats/{cid}/rename")
async def api_chat_rename(cid: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok = chatstore.rename(cid, body.get("title") or "")
    return {"ok": ok, "error": None if ok else "Пустое название"}


@app.post("/api/chats/{cid}/messages")
async def api_chat_add_message(cid: str, request: Request):
    """Сохраняет сообщение пользователя (ответ модели пишется во время стрима)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not chatstore.get(cid):
        return {"ok": False, "error": "Диалог не найден"}
    mid = chatstore.add_message(cid, body.get("role") or "user",
                                body.get("content") or "", body.get("meta"))
    return {"ok": True, "id": mid}


@app.delete("/api/chats/{cid}")
async def api_chat_delete(cid: str):
    return {"ok": chatstore.delete(cid)}


@app.post("/api/chats/clear")
async def api_chats_clear():
    return {"ok": True, "deleted": chatstore.clear_all()}


@app.get("/api/chats/export/json")
async def api_chats_export():
    """Выгрузка всей истории одним JSON-файлом."""
    data = json.dumps({"app": "Free LLM Router", "type": "chat-history",
                       "version": 1, "exported_at": time.time(),
                       "chats": chatstore.export_all()},
                      ensure_ascii=False, indent=1)
    filename = "free-llm-router-chats-" + time.strftime("%Y%m%d-%H%M") + ".json"
    return Response(content=data, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.post("/api/chats/import")
async def api_chats_import(request: Request):
    """Загружает ранее выгруженный JSON (или одиночный диалог)."""
    try:
        body = await request.json()
    except Exception as e:
        return {"ok": False, "error": f"Не JSON: {e}"}
    items = body if isinstance(body, list) else (body.get("chats") if isinstance(body, dict) else None)
    if items is None and isinstance(body, dict) and body.get("messages"):
        items = [body]
    if not isinstance(items, list):
        return {"ok": False, "error": "В файле нет списка диалогов"}
    try:
        added = chatstore.import_all(items)
    except Exception as e:
        return {"ok": False, "error": f"Ошибка импорта: {e}"}
    return {"ok": True, "added": added}


@app.post("/api/chat")
async def api_chat(body: dict) -> StreamingResponse:
    messages = body.get("messages") or []
    if not messages and body.get("chat_id"):
        # продолжение ранее сохранённого диалога: история берётся из базы
        saved = chatstore.get(str(body["chat_id"])) or {}
        messages = [{"role": m["role"], "content": m["content"]}
                    for m in saved.get("messages") or [] if m.get("content")]
        if body.get("append_user"):
            messages.append({"role": "user", "content": str(body["append_user"])})
    provider = body.get("provider") or None
    model = body.get("model") or None
    if provider in ("auto", ""):
        provider = None
    if model in ("auto", ""):
        model = None

    files = body.get("files") or []
    if files:
        messages = _with_files(messages, files)
    files_in_context = [str(f.get("name") or "") for f in files if f.get("text")]

    index_hits: list[dict] = []
    if body.get("use_index") or body.get("x_use_index"):
        messages, index_hits = _with_index(messages, body.get("index_limit") or 5)

    # ---- история: диалог создаётся/дополняется автоматически ----
    store = None
    try:
        store = _hook_history(body, files)
    except Exception as exc:  # noqa: BLE001 — история не должна ломать чат
        print(f"[chats] не удалось открыть историю: {exc}")

    async def events():
        if files_in_context:
            yield json.dumps({"type": "context", "files": files_in_context},
                             ensure_ascii=False) + "\n"
        if index_hits:
            yield json.dumps({"type": "index", "hits": len(index_hits),
                              "mode": index_hits[0].get("mode"),
                              "sources": _sources(index_hits)},
                             ensure_ascii=False) + "\n"
        if store:
            yield json.dumps({"type": "chat", "id": store["id"]}, ensure_ascii=False) + "\n"
        buf = (store or {}).get("prefix") or ""
        last_save, meta = 0.0, {}
        try:
            async for event in rt.stream_chat(
                messages,
                provider=provider,
                model=model,
                temperature=float(body.get("temperature") or 0.7),
                max_tokens=int(body.get("max_tokens") or 4096),   # длинные ответы не режем
                allow_fallback=body.get("allow_fallback", True),
                tools=body.get("tools") or None,
                require_tools=bool(body.get("tools")) or bool(body.get("require_tools")),
            ):
                kind = event.get("type")
                if kind == "delta":
                    buf += event.get("text") or ""
                elif kind == "meta":
                    meta = {"provider": event.get("provider"), "model": event.get("model"),
                            "latency": event.get("latency")}
                elif kind == "index":
                    meta["sources"] = event.get("sources") or []
                elif kind == "truncated":
                    meta["truncated"] = True
                elif kind == "error":
                    meta["error"] = event.get("message")
                # пишем на диск не чаще раза в 1.5 с, чтобы не тормозить поток
                if store and (kind in ("done", "error", "truncated") or time.monotonic() - last_save > 1.5):
                    try:
                        chatstore.update_message(store["mid"], buf, meta or None)
                        last_save = time.monotonic()
                    except Exception:
                        pass
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:  # noqa: BLE001 — отдаём ошибку в UI, а не 500
            yield json.dumps(
                {"type": "error", "message": f"Внутренняя ошибка: {exc}"}, ensure_ascii=False
            ) + "\n"
        finally:
            if store:
                try:
                    chatstore.update_message(store["mid"], buf, meta or None)
                except Exception:
                    pass

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# OpenAI-совместимый шлюз для агентов
# --------------------------------------------------------------------------


MAX_FILES_TOTAL = filetext.TOTAL_CHARS


def _hook_history(body: dict, files: list[dict]) -> dict | None:
    """Создаёт/находит диалог и сохраняет в него пару «вопрос + пустой ответ».

    Возвращает {id диалога, id сообщения-ответа} — сервер дописывает ответ
    по мере стрима. Если в запросе нет chat_id (шлюз /v1, агенты) — ничего не делает.
    """
    history = body.get("messages") or []
    last = history[-1] if history else {}
    question = str(last.get("content") or "") if last.get("role") == "user" else ""
    is_continue = bool(body.get("continue"))
    if not question and not is_continue:
        return None

    # диалог создаётся только по первому реальному вопросу — пустых записей нет
    cid = str(body.get("chat_id") or "").strip()
    chat = chatstore.get(cid) if cid else None
    if chat is None:
        first = next((str(m.get("content") or "") for m in history
                      if m.get("role") == "user" and str(m.get("content") or "").strip()), question)
        chat = chatstore.create(_short_title(first), body.get("provider"), body.get("model"))
        cid = chat["id"]
        chat = chatstore.get(cid) or chat

    if is_continue:
        # «Продолжить»: дописываем в тот же ответ модели, а не создаём новый
        prev = chatstore.last_assistant(cid)
        if prev:
            mid, prefix = prev
            chatstore.touch(cid, body.get("provider"), body.get("model"), None)
            return {"id": cid, "mid": mid, "prefix": prefix}
        mid = chatstore.add_message(cid, "assistant", "")
        chatstore.touch(cid, body.get("provider"), body.get("model"), None)
        return {"id": cid, "mid": mid, "prefix": ""}

    # сообщение пользователя — только если его ещё нет
    # (первый запрос сохраняет вопрос, повторные ходы — только свои)
    saved = chat.get("messages") or []
    have = sum(1 for m in saved if m.get("role") == "user")
    expected = sum(1 for m in history if m.get("role") == "user")
    if expected > have:
        chatstore.add_message(cid, "user", question,
                              {"files": [f.get("name") for f in files]} if files else None)

    if not saved:
        chatstore.rename(cid, _short_title(question))
    if files:
        try:
            chatstore.save_files(cid, files)
        except Exception:
            pass

    chatstore.touch(cid, body.get("provider"), body.get("model"), files)
    mid = chatstore.add_message(cid, "assistant", "")
    return {"id": cid, "mid": mid, "prefix": ""}


def _short_title(text: str) -> str:
    """Заголовок диалога = первая строка первого вопроса."""
    q = " ".join((text or "").split())
    if not q:
        return "Новый диалог"
    return q if len(q) <= 60 else q[:59].rstrip() + "…"


def _with_files(messages: list[dict], files: list[dict]) -> list[dict]:
    """Добавляет содержимое файлов в последнее сообщение пользователя."""
    if not messages:
        return messages
    clean = []
    total = 0
    for f in files:
        text = str(f.get("text") or "")
        if not text:
            continue
        if total + len(text) > MAX_FILES_TOTAL:
            text = text[: max(0, MAX_FILES_TOTAL - total)]
        total += len(text)
        clean.append({"name": f.get("name") or "файл", "kind": f.get("kind") or "файл",
                      "chars": len(text), "pages": f.get("pages"), "text": text})
    if not clean:
        return messages

    block = filetext.as_prompt_block(clean)
    out = [dict(m) for m in messages]
    last = out[-1]
    last["content"] = (
        "Содержимое приложенных файлов:\n\n" + block +
        "\n\n---\nЗапрос:\n" + (last.get("content") or "")
    )
    return out


def _with_index(messages: list[dict], limit: int) -> tuple[list[dict], list[dict]]:
    """Ищет в SQLite-индексе по последнему сообщению и добавляет контекст."""
    if not messages:
        return messages, []
    query = str(messages[-1].get("content") or "")
    if not query.strip():
        return messages, []
    block, hits = indexdb.as_context(query, limit=int(limit))
    if not hits:
        return messages, []
    out = [dict(m) for m in messages]
    out[-1]["content"] = block + "\n\n---\nВопрос:\n" + query
    return out, hits


def _sources(hits: list[dict]) -> list[dict]:
    """Компактный список источников для интерфейса."""
    out, seen = [], set()
    for h in hits:
        key = (h.get("sha"), h.get("page"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"doc": h.get("doc"), "sha": h.get("sha"),
                    "page": h.get("page"), "score": h.get("score"),
                    "mode": h.get("mode")})
    return out[:8]


def _mount(request: Request) -> str:
    """Префикс, под которым смонтирован шлюз (для /v1 и /openai/v1 одновременно)."""
    return request.scope.get("mount_path", "") or ""


def _entry(spec: str, created: int = 1700000000) -> dict:
    prov, model = rt.parse_model_spec(spec)
    if prov is None and model is None:
        label = "auto"
    elif model is None:
        label = f"{prov}::auto"
    elif prov:
        label = f"{prov}::{model}"
    else:
        label = model
    return {"id": label, "object": "model", "created": created, "owned_by": "free-llm-router"}


def _catalog() -> list[dict]:
    entries = [_entry("auto"), _entry("auto-tools")]
    for name in rt._order():
        p = rt.PROVIDERS[name]
        entries.append(_entry(f"{name}::auto"))
        entries += [_entry(f"{name}::{m}") for m in p.models]
    return entries


@app.get("/api/tools")
async def api_tools():
    """Кто из моделей умеет вызывать инструменты — и какие уже отсеялись на деле."""
    return {
        "ok": True,
        "models": rt.tools_capability(),
        "rejected": sorted(rt._no_tools),
        "usage": {
            "model": "auto-tools",
            "note": "укажите model=auto-tools или передайте tools — роутер выберет "
                    "только модели с поддержкой инструментов",
        },
    }


@app.get("/v1/models")
@app.get("/v1/models/")
async def v1_models(request: Request) -> dict:
    return {"object": "list", "data": _catalog()}


@app.get("/v1/models/{spec:path}")
async def v1_models_one(request: Request, spec: str) -> dict:
    if spec.strip().lower() in ("auto-tools", "tools", "auto_tools"):
        spec = "auto-tools"
    return _entry(spec)


@app.post("/v1/chat/completions", response_model=None)
@app.post("/v1/chat/completions/", response_model=None)
async def v1_chat(request: Request, body: dict):
    spec = body.get("model") or "auto"
    # "auto-tools" и "tools" — принудительно только модели с поддержкой инструментов
    require_tools = str(spec).strip().lower() in ("auto-tools", "tools", "auto_tools")
    if require_tools:
        spec = "auto"
    provider, model = rt.parse_model_spec(spec)
    messages = body.get("messages") or []
    files = body.get("files") or []
    if files:
        messages = _with_files(messages, files)
    v1_sources: list[dict] = []
    if body.get("use_index"):
        messages, v1_sources = _with_index(messages, body.get("index_limit") or 5)
    temperature = body.get("temperature")
    temperature = 0.7 if temperature is None else float(temperature)
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens") or 4096
    tools = body.get("tools")
    stream = bool(body.get("stream"))
    created = int(time.time())
    model_id = spec

    if stream:
        mount = _mount(request)

        async def sse():
            started = False
            truncated = False
            provider_used = None
            had_tools = False

            def frame(delta: dict, finish: str | None = None) -> str:
                chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_id,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            try:
                async for ev in rt.stream_chat(
                    messages,
                    provider=provider,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    require_tools=require_tools,
                ):
                    kind = ev["type"]
                    if kind == "meta":
                        provider_used = ev
                        if not started:
                            started = True
                            yield frame({"role": "assistant", "content": ""})
                    elif kind == "delta":
                        yield frame({"content": ev["text"]})
                    elif kind == "tools":
                        # OpenAI-протокол: каждый вызов отдельным кадром, с index,
                        # id и type — именно так их ждут клиенты (Cline, SDK, CrewAI)
                        had_tools = True
                        for i, call in enumerate(ev.get("calls") or []):
                            yield frame({"tool_calls": [{
                                "index": i,
                                "id": call.get("id"),
                                "type": call.get("type") or "function",
                                "function": call.get("function") or {},
                            }]})
                    elif kind == "error":
                        err = {"error": {"message": ev["message"], "type": "router_error"}}
                        yield "event: router.error\ndata: " + json.dumps(
                            err, ensure_ascii=False
                        ) + "\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    elif kind == "truncated":
                        truncated = True              # финальный кадр будет с length
                    elif kind == "done":
                        finish = "length" if truncated else ("tool_calls" if had_tools else "stop")
                        yield frame({}, finish=finish)
                        yield "data: [DONE]\n\n"
                if not started:
                    yield frame({}, finish="stop")
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:  # клиент закрыл соединение
                raise

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(sse(), media_type="text/event-stream", headers=headers)

    try:
        result = await rt.chat(
            messages,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            require_tools=require_tools,
        )
    except rt.NoProviders as exc:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": str(exc), "type": "router_error"}},
        )

    message = {
        "role": "assistant",
        "content": result["text"] or None,
        **({"tool_calls": result["tool_calls"]} if result.get("tool_calls") else {}),
    }
    if message.get("tool_calls"):
        reason = "tool_calls"
    elif result.get("truncated"):
        reason = "length"
    else:
        reason = "stop"
    usage = result.get("usage") or {}
    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": created,
            "model": f"{result['provider']}::{result['model']}",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": reason,
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            # служебные поля: что не сработало и какие фрагменты индекса использованы
            "x_router": {"attempts": result.get("attempts", [])},
            "x_sources": _sources(v1_sources) if v1_sources else [],
        }
    )


# Роуты под прикрытием: некоторые клиенты хотят свой префикс —
# например, LM Studio-клиенты дёргают /openai/v1/... или /api/v1.
for _prefix in ("/openai", "/proxy"):
    app.mount(_prefix, app)  # type: ignore[arg-type]


def _free_port(host: str, preferred: int, attempts: int = 20) -> int:
    """Первый свободный порт начиная с preferred (для запуска рядом с занятым)."""
    probe_host = "0.0.0.0" if host in ("0.0.0.0", "::", "") else host
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # как у uvicorn: порт в TIME_WAIT после недавней остановки — свободен
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((probe_host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"Нет свободного порта в диапазоне {preferred}-{preferred + attempts - 1}")


if __name__ == "__main__":
    # Windows: гарантируем utf-8 при перенаправлении вывода в файл/пайп
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import argparse

    ap = argparse.ArgumentParser(description="Free LLM Router — чат и OpenAI-совместимый шлюз")
    ap.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"),
                    help="интерфейс (по умолчанию HOST из .env или 0.0.0.0)")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--open", action="store_true", help="открыть браузер после запуска")
    args = ap.parse_args()

    port = _free_port(args.host, args.port)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{shown}:{port}"
    ready = sum(1 for p in rt.PROVIDERS.values() if p.key)
    print()
    print(f"  Free LLM Router  ->  {url}")
    print(f"  OpenAI-шлюз      ->  {url}/v1      (base_url для агента)")
    print(f"  Провайдеров с ключом: {ready} из {len(rt.PROVIDERS)}"
          + ("" if ready else "   (впишите ключ в .env и нажмите 'Ключи' в чате)"))
    print()

    if args.open:
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run(app, host=args.host, port=port, log_level="info")
