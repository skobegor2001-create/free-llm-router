"""Пример: как подключить агента к среде.

Показывает два уровня:
  1) прямой вызов ядра (router.chat) — без сети и сервера;
  2) настоящий tool-calling цикл через локальный OpenAI-шлюз (/v1),
     то есть так, как это увидит LangChain / CrewAI / SDK OpenAI.

Запуск:
    python examples/agent_demo.py            # через ядро
    python examples/agent_demo.py --gateway  # через http://localhost:8000/v1
"""

from __future__ import annotations

import argparse
import asyncio
import ast
import datetime as dt
import json
import operator
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import router as rt  # noqa: E402

GATEWAY = "http://localhost:8000/v1"

# --------------------------------------------------------------------------
# Инструменты агента
# --------------------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.USub: operator.neg}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("только арифметика: числа, + - * / % **")


def calculator(expression: str) -> str:
    return str(_safe_eval(ast.parse(expression, mode="eval").body))


def current_time(tz: str = "Europe/Moscow") -> str:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M (%Z)")
    except Exception:
        return dt.datetime.now().strftime("%Y-%m-%d %H:%M (локальное)")


def http_get(url: str) -> str:
    r = httpx.get(url, timeout=15, follow_redirects=True)
    return r.text[:3000]


TOOLS = [
    {"type": "function",
     "function": {"name": "current_time",
                  "description": "Текущие дата и время в часовом поясе.",
                  "parameters": {"type": "object",
                                 "properties": {"tz": {"type": "string",
                                                       "description": "IANA-пояс, напр. Europe/Moscow"}},
                                 "required": []}}},
    {"type": "function",
     "function": {"name": "calculator",
                  "description": "Вычисляет арифметическое выражение, например «(3+4)*12».",
                  "parameters": {"type": "object",
                                 "properties": {"expression": {"type": "string"}},
                                 "required": ["expression"]}}},
    {"type": "function",
     "function": {"name": "http_get",
                  "description": "Скачивает текст страницы по http(s)-адресу (до 3000 символов).",
                  "parameters": {"type": "object",
                                 "properties": {"url": {"type": "string"}},
                                 "required": ["url"]}}},
]

REGISTRY = {"current_time": current_time, "calculator": calculator, "http_get": http_get}


def run_tool(name: str, args: dict) -> str:
    try:
        return REGISTRY[name](**args)
    except Exception as exc:  # noqa: BLE001
        return f"ошибка инструмента: {exc}"


# --------------------------------------------------------------------------
# Уровень 1: агент через ядро роутера
# --------------------------------------------------------------------------


async def agent_core(question: str, max_steps: int = 5) -> None:
    messages = [
        {"role": "system", "content": "Ты аккуратный агент. Используй инструменты, "
                                      "когда нужны вычисления, время или сеть."},
        {"role": "user", "content": question},
    ]
    for step in range(1, max_steps + 1):
        res = await rt.chat(messages, tools=TOOLS, allow_fallback=True)
        msg = res["message"]
        calls = msg.get("tool_calls") or []
        print(f"  шаг {step}: {res['provider']}/{res['model']} → "
              f"{'tools: ' + ', '.join(c['function']['name'] for c in calls) if calls else 'ответ'}")

        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         **({"tool_calls": calls} if calls else {})})
        if not calls:
            print("\nФинал:", (msg.get("content") or "").strip())
            return
        for call in calls:
            args = json.loads(call["function"].get("arguments") or "{}")
            out = run_tool(call["function"]["name"], args)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
    print("Лимит шагов исчерпан.")


# --------------------------------------------------------------------------
# Уровень 2: тот же агент — но через OpenAI-совместимый шлюз
# (так сюда подключится любой внешний фреймворк)
# --------------------------------------------------------------------------


async def agent_gateway(question: str, max_steps: int = 5) -> None:
    messages = [{"role": "user", "content": question}]
    async with httpx.AsyncClient(timeout=180) as client:
        for step in range(1, max_steps + 1):
            r = await client.post(
                f"{GATEWAY}/chat/completions",
                headers={"Authorization": "Bearer local"},
                json={"model": "auto", "messages": messages, "tools": TOOLS},
            )
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            print(f"  шаг {step}: {data['model']} → "
                  f"{'tools: ' + ', '.join(c['function']['name'] for c in calls) if calls else 'ответ'}")
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             **({"tool_calls": calls} if calls else {})})
            if not calls:
                print("\nФинал:", (msg.get("content") or "").strip())
                return
            for call in calls:
                args = json.loads(call["function"].get("arguments") or "{}")
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": run_tool(call["function"]["name"], args)})
    print("Лимит шагов исчерпан.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", action="store_true", help="идти через локальный шлюз /v1")
    ap.add_argument("question", nargs="?",
                    default="Сколько будет (137*24 + 15) / 3? И который сейчас час в Стокгольме?",
                    help="вопрос агенту")
    args = ap.parse_args()
    print(f"Агент · вопрос: {args.question}\n")
    asyncio.run(agent_gateway(args.question) if args.gateway else agent_core(args.question))
