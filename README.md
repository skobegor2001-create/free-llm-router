# Free LLM Router

Веб-чат и OpenAI-совместимый шлюз к **бесплатным** нейросетям: Groq, Cerebras,
Gemini, OpenRouter и Mistral в одной цепочке с автоматическим переключением при
исчерпании лимита. Плюс поиск по вашему SQLite-индексу PDF, вложения (PDF, DOCX,
TXT), история диалогов и установщик для Windows 11 без прав администратора.

```
браузер ─┐
         ├─→ /api/chat (поток NDJSON) ─┐
агенты ──┘                             ├─→ роутер ─→ Groq → Cerebras → Gemini → OpenRouter → Mistral
         └─→ /v1 (OpenAI-совместимо) ──┘                    (при 429/5xx — следующему)
```

## Возможности

| | |
|---|---|
| **5 бесплатных провайдеров** | автопереключение при 429/5xx, паузы по подсказкам сервера, живой список моделей |
| **Веб-чат** | потоковый ответ, выбор модели, температура, «↻ Продолжить» при обрыве по лимиту |
| **OpenAI-шлюз** | `/v1/chat/completions`, `/v1/models`, стриминг, **tools / function calling** |
| **RAG по вашему индексу PDF** | семантический поиск (эмбеддинги из `chunks.vector`) + текстовый фолбэк, ссылки на страницы |
| **Файлы** | локальное извлечение текста из PDF/DOCX/TXT, файл остаётся в контексте диалога |
| **История чатов** | локальный SQLite, поиск, переименование, выгрузка/загрузка JSON, файлы сохраняются вместе с диалогом |
| **Агенты** | `model: "auto-tools"` — только модели с поддержкой инструментов; Cline, Open WebUI, CrewAI подключаются к `/v1` |
| **Windows 11** | установщик одним кликом, без прав администратора, встроенный Python |

## Быстрый старт

```bash
git clone https://github.com/<ваш-аккаунт>/free-llm-router.git
cd free-llm-router
python -m pip install -r requirements.txt
cp .env.example .env          # впишите хотя бы один бесплатный ключ
python server.py              # http://localhost:8000
```

Ключи: `GET-KEYS.md` — пошагово, где взять и какие лимиты. Проверка без ключей:

```bash
python tests/smoke_test.py    # поднимает мок-провайдера, ничего не требует
```

Windows: скачайте `FreeLLMRouter-Setup-<версия>.exe` из раздела
[Releases](../../releases) или соберите сами — `windows/WIN-INSTALL.md`.

## Для агентов

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="local")
r = client.chat.completions.create(
    model="auto-tools",                      # только модели с инструментами
    messages=[{"role": "user", "content": "Найди пени в договоре аренды"}],
    tools=[{
        "type": "function",
        "function": {
            "name": "search_index",
            "description": "Поиск по базе PDF",
            "parameters": {"type": "object",
                           "properties": {"query": {"type": "string"}},
                           "required": ["query"]},
        },
    }],
)
print(r.choices[0].finish_reason)        # tool_calls
print(r.choices[0].message.tool_calls)   # [id, type, function{name, arguments}]
```

Подключение без кода: Cline, Continue, OpenCode, Open WebUI, LangChain, CrewAI —
Base URL `http://localhost:8000/v1`, API key любой, модель `auto-tools`.
Подробнее: [README → Инструменты и агенты](README.md#инструменты-и-агенты-tools--function-calling).

## Документация

- [README.md](README.md) — полное описание, все ручки API, настройки
- [GET-KEYS.md](GET-KEYS.md) — как получить бесплатные ключи
- [windows/WIN-INSTALL.md](windows/WIN-INSTALL.md) — установка на Windows 11
- [docs/AGENTS-DESIGN.md](docs/AGENTS-DESIGN.md) — план агентской части (черновик)
- [examples/agent_demo.py](examples/agent_demo.py) — минимальный агент на нашем шлюзе

## Структура

```
server.py        FastAPI: веб-чат, /api/*, /v1 (OpenAI)
router.py        провайдеры, переключение, паузы, tool calling, стриминг
indexdb.py       поиск по SQLite-индексу PDF (семантика + слова), только чтение
filetext.py      извлечение текста из PDF/DOCX/TXT
chatstore.py     история чатов (локальный SQLite)
cli.py           запрос из терминала
doctor.py        диагностика подключения к провайдерам
get_keys.py      список мест, где взять бесплатные ключи
static/          веб-интерфейс (один HTML, без сборки)
windows/         установщик NSIS, лаунчер, деинсталлятор
tests/           смоук-тест на мок-провайдере
```

## Ограничения бесплатных тарифов

Лимиты меняются: Groq ~30 запр./мин и 1000/день, Cerebras ~1 млн токенов/день,
Gemini Flash 5–15 запр./мин, OpenRouter 50/день на `:free`, Mistral ~1 запр./сек.
Роутер ставит паузу провайдеру по `Retry-After` и переходит к следующему; карточка
провайдеров в чате показывает, кто на паузе. Актуальные цифры — `GET /api/status`.

## Лицензия

MIT — см. [LICENSE](LICENSE).
