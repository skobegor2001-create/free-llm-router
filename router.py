"""Free LLM Router — ядро.

Единый асинхронный клиент к бесплатным тарифам LLM-провайдеров
(Groq, Cerebras, Gemini, OpenRouter, Mistral) с автопереключением
при лимитах (429), сбоях (5xx), недоступных моделях (404) и сетевых ошибках.

Все провайдеры говорят на OpenAI-совместимом Chat Completions,
поэтому протокол один: POST {base_url}/chat/completions.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

# --------------------------------------------------------------------------
# Описание провайдеров
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    name: str
    title: str
    base_url: str
    env: str                    # имя переменной с API-ключом
    signup: str                 # где получить ключ
    limits: str                 # бесплатные лимиты (актуально на 11.2026)
    models: tuple[str, ...]     # приоритетный список моделей
    extra_headers: dict[str, str] = field(default_factory=dict)
    tools: bool = True          # умеет ли провайдер tool calling (OpenAI-совместимо)

    @property
    def key(self) -> str | None:
        raw = os.getenv(self.env, "").strip().strip('"').strip("'")
        return raw or None


PROVIDERS: dict[str, Provider] = {
    "groq": Provider(
        name="groq",
        title="Groq",
        base_url="https://api.groq.com/openai/v1",
        env="GROQ_API_KEY",
        signup="https://console.groq.com/keys",
        limits="30 запр./мин · 1 000/день · 200K токенов/день на модель · очень быстро",
        models=("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"),
    ),
    "cerebras": Provider(
        name="cerebras",
        title="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        env="CEREBRAS_API_KEY",
        signup="https://cloud.cerebras.ai",
        limits="30 запр./мин · 1 000 000 токенов/день · самая щедрая бесплатная квота",
        models=("gpt-oss-120b", "qwen-3-235b-a22b-instruct-2507", "llama3.1-8b"),
    ),
    "gemini": Provider(
        name="gemini",
        title="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        env="GEMINI_API_KEY",
        signup="https://aistudio.google.com/apikey",
        limits="flash-lite: 15 запр./мин · 500/день · flash: 5/мин · 20/день",
        # Google для новых ключей выдаёт 3.x; 2.5 остались только старым аккаунтам.
        # flash-lite даёт до 500 запросов/день — поэтому она первая.
        models=("gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-2.5-flash"),
    ),
    "openrouter": Provider(
        name="openrouter",
        title="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        env="OPENROUTER_API_KEY",
        signup="https://openrouter.ai/keys",
        limits="20 запр./мин · 50/день на :free (1 000/день после разовой покупки $10)",
        models=(
            "openrouter/free",
            "nvidia/nemotron-3.5-lightning:free",
            "dots-studio/dots-3-note-preview:free",
            "inclusionai/ling-3.2-flash:free",
            "cohere/north-mini-code:free",
            "recursive/flint-v1.0:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        ),
        tools=False,            # у :free моделей поддержка инструментов разная — узнаём при первом вызове
        extra_headers={
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "Free LLM Router",
        },
    ),
    "mistral": Provider(
        name="mistral",
        title="Mistral",
        base_url="https://api.mistral.ai/v1",
        env="MISTRAL_API_KEY",
        signup="https://console.mistral.ai/api-keys",
        limits="~1 запр./сек · 500K токенов/мин · до ~1 млрд токенов/месяц (Experiment)",
        models=("mistral-small-latest", "mistral-large-latest", "codestral-latest"),
    ),
}

DEFAULT_ORDER: tuple[str, ...] = ("groq", "cerebras", "gemini", "openrouter", "mistral")

# --------------------------------------------------------------------------
# Состояние: «паузы» после лимитов и кеш моделей
# --------------------------------------------------------------------------

_cooldown: dict[str, float] = {}          # "provider" | "provider::model" -> до какого не трогать
_no_tools: set[str] = set()               # "provider::model", ответившие «инструменты не поддерживаются»
_models_cache: dict[str, tuple[float, list[str]]] = {}
MODELS_TTL = 600                          # сек

_client_obj: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _client_obj
    if _client_obj is None or _client_obj.is_closed:
        _client_obj = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=240.0, write=60.0, pool=15.0),
            follow_redirects=True,
        )
    return _client_obj


def _order() -> list[str]:
    raw = os.getenv("PROVIDER_ORDER", "").strip()
    names = [n.strip() for n in raw.split(",") if n.strip()] if raw else list(DEFAULT_ORDER)
    return [n for n in names if n in PROVIDERS]


def _cooldown_left(name: str, model: str | None = None) -> float:
    now = time.time()
    left = max(0.0, _cooldown.get(name, 0.0) - now)
    if model:
        left = max(left, _cooldown.get(f"{name}::{model}", 0.0) - now)
    return left


_DUR_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def _parse_duration(text: str) -> float | None:
    """Разбирает «7.66s», «1m26.4s», «2h», «120» (секунды по умолчанию)."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        try:
            return float(text)
        except ValueError:
            return None
    total, found = 0.0, False
    for num, unit in re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|h|m|s)", text):
        total += float(num) * _DUR_UNITS[unit]
        found = True
    return total if found else None


def _cooldown_seconds(status: int, raw: str, headers: dict[str, str] | None) -> tuple[int, bool] | None:
    """Сколько секунд не трогать пару «провайдер::модель» и на весь ли провайдер.

    Приоритет — подсказкам сервера, а не догадкам по тексту ошибки.
    """
    low = (raw or "").lower()
    h = {k.lower(): v for k, v in (headers or {}).items()}

    if status in (401, 403):
        return 120, True                      # проблема ключа — пауза всему провайдеру
    if status == 404:
        if any(t in low for t in ("no longer available", "not found", "does not exist",
                                  "not supported", "deprecated", "no access")):
            return 3600, False                # модели нет на тарифе — час не дёргаем
        return 60, False
    if status == 429:
        secs = _parse_duration(h.get("retry-after", ""))
        if secs is None:
            for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
                secs = _parse_duration(h.get(key, ""))
                if secs is not None:
                    break
        if secs is None:
            m = re.search(r"try again in\s+([0-9hms. ]+)", raw or "", re.I)
            if m:
                secs = _parse_duration(m.group(1))
        if secs is None:
            day = any(t in low for t in ("per day", "per-day", "daily", "rpd"))
            minute = any(t in low for t in ("per minute", "per-minute", "tpm", "rpm"))
            secs = 300.0 if (day and not minute) else 30.0
        return int(min(max(secs, 10.0), 900.0)), False
    if status >= 500:
        return 15, False
    return None


_NO_TOOLS_MARKERS = (
    "tool", "function", "unsupported", "not supported", "invalid_request",
    "unrecognized", "unknown parameter", "does not support",
)


def _looks_like_no_tools(err: PreStreamError) -> bool:
    """400-ответ на запрос с tools = «эта модель не умеет инструменты».

    Провайдеры отвечают по-разному («tool_use not supported», «unknown field tools»...),
    но общий признак один: 400/422 + в тексте ошибки упоминание инструментов.
    Такую пару «провайдер::модель» запоминаем и больше с tools не трогаем.
    """
    if err.status not in (400, 422):
        return False
    low = (err.raw or "").lower()
    has_tool = any(w in low for w in ("tool", "function"))
    has_reject = any(w in low for w in _NO_TOOLS_MARKERS)
    return has_tool and has_reject


def _supports_tools(provider: str, model: str) -> bool:
    p = PROVIDERS[provider]
    return bool(p.tools) and f"{provider}::{model}" not in _no_tools


def _note_no_tools(provider: str, model: str) -> None:
    _no_tools.add(f"{provider}::{model}")


def tools_capability() -> dict:
    """Кто из настроенных моделей умеет инструменты (для интерфейса)."""
    out: dict[str, list[str]] = {}
    for name, p in PROVIDERS.items():
        if not p.key:
            continue
        out[name] = [m for m in p.models if _supports_tools(name, m)]
    return out


def _note_failure(name: str, model: str, status: int, raw: str,
                  headers: dict[str, str] | None = None) -> None:
    """Ставит паузу по подсказке сервера (Retry-After / reset-заголовки / текст)."""
    plan = _cooldown_seconds(status, raw, headers)
    if plan is None:
        return
    secs, whole_provider = plan
    key = name if whole_provider else f"{name}::{model}"
    _cooldown[key] = max(_cooldown.get(key, 0.0), time.time() + secs)


# --------------------------------------------------------------------------
# Ошибки
# --------------------------------------------------------------------------


class RouterError(Exception):
    """Базовая ошибка роутера."""


class NoProviders(RouterError):
    """Ни один провайдер не смог ответить."""


class PreStreamError(Exception):
    """Провайдер вернул ошибку ДО начала ответа — можно переключиться."""

    def __init__(self, provider: str, status: int, raw: str):
        self.provider = provider
        self.status = status
        self.raw = raw or ""
        super().__init__(self.user_message)

    @property
    def detail(self) -> str:
        try:
            obj = json.loads(self.raw)
            err = obj.get("error")
            if isinstance(err, dict):
                msg = err.get("message") or err.get("code") or ""
            elif isinstance(err, str):
                msg = err
            else:
                msg = obj.get("detail") or obj.get("message") or ""
            return str(msg)[:220]
        except Exception:
            return self.raw.strip()[:220]

    @property
    def user_message(self) -> str:
        base = {
            401: "ключ не принят (проверьте .env)",
            402: "квота/кредиты тарифа исчерпаны",
            403: "доступ запрещён для этого ключа",
            404: "модель недоступна на бесплатном тарифе",
            413: "слишком длинный контекст",
            429: "превышен лимит запросов",
        }.get(self.status)
        if base is None:
            base = "сбой на стороне провайдера" if self.status >= 500 else f"HTTP {self.status}"
        return f"{base}: {self.detail}" if self.detail else base


class LimitReached(Exception):
    """Провайдер оборвал ответ по max_tokens (finish_reason = length)."""

    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(provider)


class StreamBroken(Exception):
    """Соединение оборвалось или провайдер вернул ошибку уже в процессе стрима."""

    def __init__(self, provider: str, detail: str):
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider}: {detail}")


# --------------------------------------------------------------------------
# Сборка цепочки кандидатов (provider, model)
# --------------------------------------------------------------------------


def _dedupe(seq: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_candidates(
    provider: str | None = None,
    model: str | None = None,
    allow_fallback: bool = True,
    require_tools: bool = False,
) -> list[tuple[str, str]]:
    """Возвращает упорядоченный список (провайдер, модель) для попыток."""
    order = _order()
    cands: list[tuple[str, str]] = []

    if provider:
        p = PROVIDERS.get(provider)
        if p is None:
            return []
        if model:
            cands.append((provider, model))
        cands += [(provider, m) for m in p.models]
    elif model:
        holders = [n for n in order if model in PROVIDERS[n].models]
        cands += [(n, model) for n in holders]
        if not holders and "openrouter" in PROVIDERS:
            # неизвестную модель пробуем через OpenRouter — самый широкий каталог
            cands.append(("openrouter", model))

    if allow_fallback or not cands:
        for n in order:
            p = PROVIDERS[n]
            cands += [(n, m) for m in p.models]

    # без ключа провайдера не трогаем; дубли убираем
    cands = [(n, m) for (n, m) in _dedupe(cands) if PROVIDERS[n].key]

    # агентам и клиентам с tools нужны модели, которые реально вызывают инструменты
    if require_tools:
        strict = [(n, m) for (n, m) in cands if _supports_tools(n, m)]
        if strict:
            return strict
        # ни одна из запрошенных не подтвердила поддержку — не сдаёмся сразу:
        # пробуем остальные, «не умеющие» отфильтруются при первом 400
        cands = [(n, m) for (n, m) in cands if PROVIDERS[n].tools]

    # провайдеров «на паузе» отправляем в конец очереди
    now = time.time()
    position = {c: i for i, c in enumerate(cands)}
    cands.sort(
        key=lambda c: (
            1 if _cooldown_left(c[0], c[1]) > 0 else 0,
            -(max(_cooldown.get(c[0], 0.0),
                  _cooldown.get(f"{c[0]}::{c[1]}", 0.0))),
            position[c],
        )
    )
    return cands


# --------------------------------------------------------------------------
# Стриминг у одного провайдера
# --------------------------------------------------------------------------


def _merge_tool_call_delta(acc: dict[int, dict], raw: list) -> None:
    """Собирает tool_calls из потока: провайдеры присылают их кусками.

    Первый кадр: {"index":0,"id":"call_x","type":"function","function":{"name":"search","arguments":""}}
    Дальше:      {"index":0,"function":{"arguments":"{\"query\""}}
    Аргументы нужно склеить по index — иначе на выходе будет обрезанный JSON.
    """
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if idx is None:
            idx = len(acc)
        slot = acc.setdefault(idx, {"id": "", "type": "function",
                                    "function": {"name": "", "arguments": ""}})
        if item.get("id"):
            slot["id"] = item["id"]
        if item.get("type"):
            slot["type"] = item["type"]
        fn = item.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]
        # некоторые провайдеры (старый формат) кладут function_call без index
        legacy = item.get("function_call")
        if isinstance(legacy, dict) and not fn:
            if legacy.get("name"):
                slot["function"]["name"] = legacy["name"]
            if legacy.get("arguments"):
                slot["function"]["arguments"] += legacy["arguments"]


def _finalize_tool_calls(acc: dict[int, dict]) -> list[dict]:
    """Приводит накопленные вызовы к формату OpenAI (с id и type)."""
    out: list[dict] = []
    for idx in sorted(acc):
        call = acc[idx]
        if not call["function"]["name"]:
            continue
        if not call["id"]:
            call["id"] = "call_" + uuid.uuid4().hex[:20]
        if call["type"] != "function":
            call["type"] = "function"
        out.append(call)
    return out


async def _stream_provider(
    p: Provider, model: str, payload: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Поток одного провайдера. Отдаёт события:

        {"type": "text",  "text": "..."}          кусок ответа
        {"type": "tools", "calls": [...]}         модель просит вызвать инструменты

    Текст может идти до вызовов и после; вызовы приходят последним событием.
    """
    url = f"{p.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {p.key}",
        "Content-Type": "application/json",
        **p.extra_headers,
    }
    body = dict(payload, model=model, stream=True)

    async with _client().stream("POST", url, headers=headers, json=body) as r:
        if r.status_code != 200:
            raw = (await r.aread()).decode("utf-8", "replace")
            err = PreStreamError(p.name, r.status_code, raw)
            err.headers = dict(r.headers)          # подсказки о сбросе лимита
            raise err

        tool_acc: dict[int, dict] = {}
        async for line in r.aiter_lines():
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("error"):
                raise StreamBroken(p.name, json.dumps(obj["error"], ensure_ascii=False)[:300])
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield {"type": "text", "text": piece}
                calls = delta.get("tool_calls") or delta.get("function_call")
                if calls:
                    _merge_tool_call_delta(tool_acc, calls if isinstance(calls, list) else [calls])
                # «length» = модель упёрлась в max_tokens; сообщаем вызывающему коду
                if choice.get("finish_reason") == "length":
                    raise LimitReached(p.name)

        final = _finalize_tool_calls(tool_acc)
        if final:
            yield {"type": "tools", "calls": final}


# --------------------------------------------------------------------------
# Публичный API: стриминг с автопереключением
# --------------------------------------------------------------------------


async def stream_chat(
    messages: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    tools: list[dict] | None = None,
    allow_fallback: bool = True,
    require_tools: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Генератор событий:
    {"type": "meta",  "provider", "model", "latency"}
    {"type": "delta", "text"}
    {"type": "done"}
    {"type": "error", "message"}
    """
    payload: dict[str, Any] = {"messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools

    candidates = build_candidates(provider, model, allow_fallback,
                                  require_tools=require_tools or bool(tools))
    if not candidates:
        yield {
            "type": "error",
            "message": (
                "Нет ни одного настроенного провайдера. Добавьте хотя бы один "
                "API-ключ в файл .env (бесплатные ключи: Groq, Cerebras, "
                "Gemini, OpenRouter, Mistral) и нажмите «обновить ключи»."
            ),
        }
        return

    started = time.time()
    problems: list[str] = []

    for pname, mname in candidates:
        p = PROVIDERS[pname]
        left = _cooldown_left(pname, mname)
        if left > 0:
            problems.append(f"{pname}/{mname}: пауза после лимита ещё {int(left)} с")
            continue

        agen = _stream_provider(p, mname, payload)
        try:
            try:
                first = await anext(agen)
            except StopAsyncIteration:
                yield {"type": "meta", "provider": pname, "model": mname,
                       "latency": round(time.time() - started, 2)}
                yield {"type": "done"}
                return
        except PreStreamError as e:
            if tools and _looks_like_no_tools(e):
                # модель не умеет инструменты — запоминаем и идём к следующей,
                # это не авария и не повод ставить паузу провайдеру
                _note_no_tools(pname, mname)
                problems.append(f"{pname}/{mname} — не поддерживает инструменты")
                continue
            _note_failure(pname, mname, e.status, e.raw, getattr(e, "headers", None))
            problems.append(f"{pname}/{mname} — {e.user_message}")
            continue
        except httpx.HTTPError as e:
            problems.append(f"{pname}/{mname} — сеть: {e.__class__.__name__}")
            continue
        except StreamBroken as e:
            problems.append(f"{pname}/{mname} — {e.detail}")
            continue

        yield {"type": "meta", "provider": pname, "model": mname,
               "latency": round(time.time() - started, 2)}

        def _emit(ev: dict[str, Any]) -> dict[str, Any]:
            if ev.get("type") == "text":
                return {"type": "delta", "text": ev["text"]}
            return ev          # {"type": "tools", "calls": [...]}

        yield _emit(first)

        try:
            async for ev in agen:
                yield _emit(ev)
        except LimitReached:
            yield {"type": "truncated", "provider": pname}
            yield {"type": "done"}
            return
        except (httpx.HTTPError, StreamBroken) as e:
            yield {
                "type": "error",
                "message": f"Соединение с {pname} оборвалось в середине ответа. "
                           f"Повторите запрос — роутер переключится на другого провайдера.",
            }
            return
        finally:
            await agen.aclose()

        yield {"type": "done"}
        return

    yield {
        "type": "error",
        "message": "Не удалось получить ответ ни от одного провайдера. "
                   + " | ".join(problems[-4:]),
    }


# --------------------------------------------------------------------------
# Публичный API: одиночный (нестриминговый) запрос
# --------------------------------------------------------------------------


async def chat(
    messages: list[dict[str, Any]],
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    tools: list[dict] | None = None,
    allow_fallback: bool = True,
    require_tools: bool = False,
) -> dict[str, Any]:
    """Возвращает {"text", "message", "provider", "model", "usage", "attempts"}."""
    payload: dict[str, Any] = {"messages": messages, "temperature": temperature}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools

    candidates = build_candidates(provider, model, allow_fallback,
                                  require_tools=require_tools or bool(tools))
    if not candidates:
        raise NoProviders(
            "Нет ни одного настроенного провайдера. Добавьте API-ключ в .env."
        )

    problems: list[str] = []
    for pname, mname in candidates:
        p = PROVIDERS[pname]
        left = _cooldown_left(pname, mname)
        if left > 0:
            problems.append(f"{pname}/{mname}: пауза после лимита ещё {int(left)} с")
            continue
        url = f"{p.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {p.key}",
            "Content-Type": "application/json",
            **p.extra_headers,
        }
        try:
            r = await _client().post(url, headers=headers, json=dict(payload, model=mname))
        except httpx.HTTPError as e:
            problems.append(f"{pname}/{mname} — сеть: {e.__class__.__name__}")
            continue

        if r.status_code != 200:
            e = PreStreamError(pname, r.status_code, r.text)
            if tools and _looks_like_no_tools(e):
                _note_no_tools(pname, mname)
                problems.append(f"{pname}/{mname} — не поддерживает инструменты")
                continue
            _note_failure(pname, mname, e.status, e.raw, dict(r.headers))
            problems.append(f"{pname}/{mname} — {e.user_message}")
            continue

        try:
            obj = r.json()
            choice = obj["choices"][0]
            message = choice["message"]
            text = message.get("content") or ""
            truncated = choice.get("finish_reason") == "length"
        except Exception:
            problems.append(f"{pname}/{mname} — неожиданный формат ответа")
            continue

        return {
            "text": text,
            "truncated": truncated,
            "tool_calls": message.get("tool_calls") or [],
            "message": message,
            "provider": pname,
            "model": mname,
            "usage": obj.get("usage"),
            "attempts": problems,
        }

    raise NoProviders("Все провайдеры недоступны. " + " | ".join(problems[-4:]))


# --------------------------------------------------------------------------
# Динамический список моделей (с кешем)
# --------------------------------------------------------------------------


async def list_models(provider: str, force: bool = False) -> list[str]:
    p = PROVIDERS[provider]
    if not p.key:
        return list(p.models)

    hit = _models_cache.get(provider)
    if hit and not force and time.time() - hit[0] < MODELS_TTL:
        return hit[1]

    ids: list[str] = []
    try:
        if provider == "gemini":
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": p.key},
                )
                r.raise_for_status()
                for m in r.json().get("models", []):
                    methods = m.get("supportedGenerationMethods") or []
                    if "generateContent" in methods:
                        ids.append(m["name"].removeprefix("models/"))
        elif provider == "openrouter":
            r = await _client().get(f"{p.base_url.rstrip('/')}/models")
            r.raise_for_status()
            for m in r.json().get("data", []):
                if str((m.get("pricing") or {}).get("prompt")) not in ("0", "0.0", "0e0"):
                    continue
                arch = m.get("architecture") or {}
                mods = arch.get("output_modalities") or []
                if mods and "text" not in mods:
                    continue
                ids.append(m["id"])
        else:
            r = await _client().get(
                f"{p.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {p.key}"},
            )
            r.raise_for_status()
            block = ("embed", "whisper", "tts", "guard", "rerank", "moderation", "ocr")
            for m in r.json().get("data", []):
                mid = m.get("id", "")
                if mid and not any(b in mid.lower() for b in block):
                    ids.append(mid)
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise ValueError("пустой список моделей")
    except Exception:
        return list(p.models)

    _models_cache[provider] = (time.time(), ids)
    return ids


# --------------------------------------------------------------------------
# Служебное: статус для UI, разбор имени модели
# --------------------------------------------------------------------------


def status() -> dict[str, Any]:
    now = time.time()
    return {
        "order": _order(),
        "providers": [
            {
                "name": p.name,
                "title": p.title,
                "env": p.env,
                "configured": p.key is not None,
                "signup": p.signup,
                "limits": p.limits,
                "models": list(p.models),
                "cooldown": int(max(0.0, _cooldown.get(p.name, 0.0) - now)),
            }
            for p in PROVIDERS.values()
        ],
    }


def parse_model_spec(spec: str | None) -> tuple[str | None, str | None]:
    """Разбирает строку модели в формат OpenAI-совместимого API.

    "auto"                    -> (None, None)              автовыбор
    "groq::auto"              -> ("groq", None)            провайдер, модель по умолчанию
    "groq::openai/gpt-oss-120b" -> ("groq", "openai/gpt-oss-120b")
    "openai/gpt-oss-120b"     -> провайдер, у которого модель в списке (Groq)
    "llama3.1-8b"             -> если не нашли — отдаём OpenRouter/автовыбору
    """
    if not spec or spec.strip().lower() in ("auto", "default", "router"):
        return None, None
    spec = spec.strip()

    if "::" in spec:
        prov, _, mod = spec.partition("::")
        prov = prov.strip() or None
        mod = mod.strip()
        if prov and prov not in PROVIDERS:
            prov = None
        return prov, (None if mod.lower() in ("", "auto") else mod)

    for name, p in PROVIDERS.items():
        if spec in p.models:
            return name, spec

    for name in PROVIDERS:
        if spec.startswith(name + "/"):
            return name, spec[len(name) + 1:]

    return None, spec
