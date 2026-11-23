# Как выложить репозиторий на GitHub

Репозиторий уже инициализирован: ветка `main`, 2 коммита, 36 файлов, 383 КБ.
`.env`, история чатов и готовые сборки исключены через `.gitignore`.

Есть два пути — выберите тот, что удобнее.

---

## Вариант 1: git push (рекомендую)

### 1. Создайте пустой репозиторий

https://github.com/new → имя `free-llm-router` → **Public** или Private →
**не** ставьте галочки «Add README», «Add .gitignore», «Choose a license» →
**Create repository**.

### 2. Отправьте код

Из папки проекта:

```bash
git remote set-url origin https://github.com/ВАШ_ЛОГИН/free-llm-router.git
git push -u origin main
```

GitHub попросит пароль — обычный пароль не подойдёт, нужен **Personal Access
Token**: https://github.com/settings/tokens → *Generate new token (classic)* →
права `repo` → скопируйте и вставьте вместо пароля.

Если git на Windows не установлен — https://git-scm.com/download/win.

### 3. Приложите готовые сборки

Репозиторий содержит только исходники. Установщик (24 МБ) в git не кладут —
его место в разделе Releases:

**Через сайт:** Releases → *Draft a new release* → тег `v1.1.1` → заголовок
`Free LLM Router 1.1.1` → перетащите файлы из папки `releases/`
(`FreeLLMRouter-Setup-1.1.1.exe`, `FreeLLMRouter-win64-portable.zip`,
`FreeLLMRouter-win64.zip`, `SHA256SUMS.txt`) → **Publish release**.

**Через gh CLI:**
```bash
gh release create v1.1.1 \
  releases/FreeLLMRouter-Setup-1.1.1.exe \
  releases/FreeLLMRouter-win64-portable.zip \
  releases/FreeLLMRouter-win64.zip \
  releases/SHA256SUMS.txt \
  --title "Free LLM Router 1.1.1" --notes "История чатов, tool calling, RAG по индексу PDF"
```

---

## Вариант 2: без установки git (через bundle)

В папке лежит `free-llm-router.bundle` — полная копия репозитория с историей.
Скопируйте его на машину, где git есть:

```bash
git clone free-llm-router.bundle free-llm-router
cd free-llm-router
git remote set-url origin https://github.com/ВАШ_ЛОГИН/free-llm-router.git
git push -u origin main
```

---

## Вариант 3: совсем без командной строки

GitHub → новый репозиторий → **uploading an existing file** → перетащите всё
содержимое папки проекта, кроме `releases/`, `data/`, `build/`, `.env`,
`__pycache__`. История коммитов при этом не сохранится — только файлы.

---

## Что дальше

- **Описание и темы** репозитория (Settings → About): `llm`, `openai`, `free-api`,
  `groq`, `cerebras`, `gemini`, `rag`, `windows`. Это заметно помогает поиску.
- **Автосборка установщика** на каждый тег — `.github/workflows/build.yml`
  (в репозитории уже есть, работает на `windows-latest`).
- Скриншоты интерфейса — в `docs/screenshots/` (сейчас пусто).
