"""Сквозной смоук-тест среды без реальных ключей.

Поднимает мок-провайдера (OpenAI-совместимый, с имитацией 429),
подключает его к роутеру и проверяет:
  • фолбэк: первая модель отвечает 429 → запрос уходит на следующую;
  • /api/chat — поток NDJSON из веб-чата;
  • /v1/chat/completions — обычный и потоковый ответ для агентов;
  • /v1/models, /health, /api/status, главная страница.

    python tests/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MOCK_PORT = 9977
APP_PORT = 8123
os.environ["MOCK_API_KEY"] = "mock-key"

# --- мок-провайдер ---------------------------------------------------------


async def mock_app(scope, receive, send):
    assert scope["type"] == "http"
    path = scope["path"]
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body"):
            break

    if path.endswith("/models"):
        payload = {"data": [{"id": "mock-model"}, {"id": "fail-model"},
                            {"id": "tools-model"}, {"id": "notools-model"}]}
        await _json(send, 200, payload)
        return

    data = json.loads(body or b"{}")
    model = data.get("model", "")
    if model == "short-model":          # имитация обрыва по max_tokens
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        chunk = {"choices": [{"delta": {"content": "Начало длинного анализа,"}}]}
        await send({"type": "http.response.body",
                    "body": f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode(),
                    "more_body": True})
        end = {"choices": [{"delta": {}, "finish_reason": "length"}]}
        await send({"type": "http.response.body",
                    "body": f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode(),
                    "more_body": False})
        return

    if model == "fail-model":
        await _json(send, 429, {"error": {"message": "Rate limit exceeded: free-models-per-day"}})
        return

    if model == "notools-model" and data.get("tools"):
        # типичный ответ провайдера: «инструменты не поддерживаются»
        await _json(send, 400, {"error": {"message": "tool_use is not supported for this model"}})
        return

    if model == "tools-model" and data.get("tools"):
        # вызов инструмента приходит кусками: id+name, затем аргументы по частям
        if data.get("stream"):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            frames = [
                {"choices": [{"delta": {"role": "assistant", "content": None,
                                        "tool_calls": [{"index": 0, "id": "call_mock_1",
                                                        "type": "function",
                                                        "function": {"name": "search_index",
                                                                     "arguments": "{\"que"}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0,
                                                        "function": {"arguments": "ry\":\"пени\"}"}}]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ]
            for fr in frames:
                await send({"type": "http.response.body",
                            "body": f"data: {json.dumps(fr, ensure_ascii=False)}\n\n".encode(),
                            "more_body": True})
            await send({"type": "http.response.body", "body": b"data: [DONE]\n\n", "more_body": False})
            return
        await _json(send, 200, {"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_mock_1", "type": "function",
                            "function": {"name": "search_index",
                                         "arguments": "{\"query\":\"пени\"}"}}]},
            "finish_reason": "tool_calls"}]})
        return

    if data.get("stream"):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        for piece in ("Пауза", " не нужна", " — всё работает."):
            chunk = {"choices": [{"delta": {"content": piece}}]}
            await send({"type": "http.response.body",
                        "body": f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode(),
                        "more_body": True})
        await send({"type": "http.response.body", "body": b"data: [DONE]\n\n", "more_body": False})
        return

    payload = {
        "choices": [{"message": {"role": "assistant", "content": "ok from mock"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    await _json(send, 200, payload)


async def _json(send, status: int, payload: dict):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": json.dumps(payload).encode()})


def run_mock():
    import uvicorn

    uvicorn.Server(uvicorn.Config(mock_app, host="127.0.0.1", port=MOCK_PORT,
                                  log_level="error")).run()


# --- тест ------------------------------------------------------------------


def main() -> int:
    import router as rt
    import server

    # подключаем мок к роутеру на первое место
    rt.PROVIDERS["mock"] = rt.Provider(
        name="mock", title="Mock", base_url=f"http://127.0.0.1:{MOCK_PORT}",
        env="MOCK_API_KEY", signup="-", limits="тестовый",
        models=("fail-model", "mock-model", "tools-model", "notools-model", "short-model"),
    )
    os.environ["PROVIDER_ORDER"] = "mock"
    rt._cooldown.clear()

    threading.Thread(target=run_mock, daemon=True).start()

    import uvicorn

    app_server = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1",
                                               port=APP_PORT, log_level="error"))
    threading.Thread(target=app_server.run, daemon=True).start()
    for _ in range(100):
        if app_server.started:
            break
        time.sleep(0.05)

    ok = True
    base = f"http://127.0.0.1:{APP_PORT}"

    def check(name: str, cond: bool, extra: str = ""):
        nonlocal ok
        ok &= cond
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))

    # 1. ядро: фолбэк с 429
    res = asyncio.run(rt.chat([{"role": "user", "content": "привет"}],
                              provider="mock", allow_fallback=False))
    check("ядро: фолбэк fail-model(429) → mock-model",
          res["model"] == "mock-model" and "attempts" in res,
          f"попытки: {res['attempts']}")

    # 2. веб-ручки
    with httpx.Client(base_url=base, timeout=20) as c:
        r = c.get("/"); check("GET / — страница чата", r.status_code == 200 and "Free LLM Router" in r.text)
        r = c.get("/health"); check("GET /health", r.status_code == 200 and r.json()["ok"])
        r = c.get("/api/status"); st = r.json()
        mock = next(x for x in st["providers"] if x["name"] == "mock")
        check("GET /api/status", r.status_code == 200 and mock["configured"],
              f"провайдеров: {len(st['providers'])}, порядок: {st['order']}")
        r = c.get("/api/models"); check("GET /api/models", r.status_code == 200 and "mock" in r.json()["models"])

        # 3. поток веб-чата (NDJSON)
        events = []
        with c.stream("POST", "/api/chat",
                      json={"messages": [{"role": "user", "content": "привет"}],
                            "provider": "mock", "model": "fail-model"}) as r:
            for line in r.iter_lines():
                if line.strip():
                    events.append(json.loads(line))
        kinds = [e["type"] for e in events]
        text = "".join(e["text"] for e in events if e["type"] == "delta")
        meta_ev = next((e for e in events if e["type"] == "meta"), {})
        check("POST /api/chat — стрим + фолбэк",
              "meta" in kinds and kinds[-1] == "done" and "всё работает" in text,
              f"meta: {meta_ev.get('provider')}/{meta_ev.get('model')} · события: {'→'.join(kinds)}")

        # 4. шлюз: список моделей
        r = c.get("/v1/models"); ids = [m["id"] for m in r.json()["data"]]
        check("GET /v1/models", r.status_code == 200 and "mock::mock-model" in ids and "auto" in ids,
              f"{len(ids)} моделей")

        # 5. шлюз: обычный ответ
        r = c.post("/v1/chat/completions",
                   headers={"Authorization": "Bearer local"},
                   json={"model": "mock::fail-model", "messages": [{"role": "user", "content": "hi"}]})
        d = r.json()
        check("POST /v1/chat/completions",
              r.status_code == 200 and bool(d["choices"][0]["message"]["content"])
              and bool(d.get("model")),
              f"model={d.get('model')}")

        # 5б. инструменты: нестриминг
        TOOLS = [{"type": "function", "function": {
            "name": "search_index", "description": "поиск",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}]
        r = c.post("/v1/chat/completions", json={"model": "mock::tools-model",
                   "messages": [{"role": "user", "content": "найди пени"}], "tools": TOOLS})
        d = r.json()
        msg = d["choices"][0]["message"]
        tc = (msg.get("tool_calls") or [{}])[0]
        check("POST /v1 — tool_calls (нестриминг)",
              d["choices"][0]["finish_reason"] == "tool_calls"
              and tc.get("function", {}).get("name") == "search_index"
              and json.loads(tc["function"]["arguments"]) == {"query": "пени"},
              f"finish={d['choices'][0]['finish_reason']}")

        # 5в. инструменты: стриминг (кадры index/id/type + finish_reason)
        frames = []
        with c.stream("POST", "/v1/chat/completions",
                      json={"model": "mock::tools-model", "stream": True,
                            "messages": [{"role": "user", "content": "найди"}],
                            "tools": TOOLS}) as r:
            for line in r.iter_lines():
                if line.startswith("data:") and "[DONE]" not in line:
                    frames.append(json.loads(line[5:]))
        call_frames = [f for f in frames
                       if (f["choices"][0]["delta"].get("tool_calls") or [])]
        check("POST /v1 — tool_calls в стриме",
              bool(call_frames)
              and call_frames[0]["choices"][0]["delta"]["tool_calls"][0].get("index") == 0
              and frames[-1]["choices"][0]["finish_reason"] == "tool_calls",
              f"кадров: {len(call_frames)}")

        # 5г. модель без инструментов → роутер переключается, пара запоминается
        r = c.post("/v1/chat/completions", json={"model": "mock::notools-model",
                   "messages": [{"role": "user", "content": "найди"}], "tools": TOOLS})
        d = r.json()
        check("модель без tools → автопереключение",
              "notools-model" not in json.dumps(d.get("model", "")) and
              "mock::notools-model" in rt._no_tools,
              f"ответила: {d.get('model')}")

        # 5д. ручка /api/tools
        r = c.get("/api/tools"); info = r.json()
        check("GET /api/tools", r.status_code == 200 and "tools-model" in info["models"].get("mock", []),
              f"отсеяно: {len(info['rejected'])}")

        # 6. шлюз: потоковый ответ
        frames = []
        with c.stream("POST", "/v1/chat/completions",
                      json={"model": "mock", "stream": True,
                            "messages": [{"role": "user", "content": "hi"}]}) as r:
            for line in r.iter_lines():
                if line.startswith("data:") and "[DONE]" not in line:
                    frames.append(json.loads(line[5:].strip()))
        content = "".join(
            f["choices"][0]["delta"].get("content", "") for f in frames
        )
        check("POST /v1/chat/completions (stream)", "всё работает" in content,
              f"{len(frames)} чанков")

        # 7. монтированный префикс /openai/v1
        r = c.get("/openai/v1/models")
        check("GET /openai/v1/models (алиас для клиентов)", r.status_code == 200)

    print("\n" + ("ВСЁ ЗЕЛЁНОЕ ✓" if ok else "ЕСТЬ ОШИБКИ ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
