"""Извлечение текста из файлов для Free LLM Router.

Локально, без облака: PDF → pypdf, DOCX → python-docx, текст/код → декодирование.
Модель получает уже текст, поэтому работает с любым провайдером.
"""

from __future__ import annotations

import codecs
import io
from pathlib import PurePath

MAX_BYTES = 25 * 1024 * 1024          # ограничение на файл
MAX_CHARS = 60_000                    # на один файл (≈15-20 тыс. токенов)
TOTAL_CHARS = 120_000                 # суммарно на запрос

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".ini", ".cfg", ".log", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h",
    ".cs", ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".bat", ".ps1", ".html",
    ".css", ".xml", ".toml",
}


class ExtractError(Exception):
    """Файл не удалось прочитать — сообщение понятное пользователю."""


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251", "koi8-r", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _extract_pdf(data: bytes) -> tuple[str, dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # старая сборка без pypdf
        raise ExtractError(
            "Для чтения PDF нужен пакет pypdf. Запустите установщик Free LLM Router "
            "ещё раз — он поставит его автоматически."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ExtractError(f"Не удалось открыть PDF: {exc}") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            if reader.decrypt("") == 0:
                raise ExtractError("PDF защищён паролем — снять пароль и повторить.")
        except ExtractError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractError(f"PDF защищён паролем: {exc}") from exc

    pages: list[str] = []
    empty_pages = 0
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — одна битая страница не рушит документ
            text = ""
        text = text.strip()
        if not text:
            empty_pages += 1
            continue
        pages.append(f"--- стр. {i} ---\n{text}")

    info = {
        "pages": len(reader.pages),
        "empty_pages": empty_pages,
        "metadata": {},
    }
    try:
        meta = reader.metadata or {}
        for key, label in (("/Title", "title"), ("/Author", "author"),
                           ("/CreationDate", "created")):
            if meta.get(key):
                info["metadata"][label] = str(meta.get(key))[:120]
    except Exception:  # noqa: BLE001
        pass

    text = "\n\n".join(pages)
    if not text:
        raise ExtractError(
            "В PDF нет текстового слоя — это, похоже, скан. Такой файл нужно сначала "
            "распознать OCR (например, в Adobe/ABBYY или через онлайн-OCR), "
            "а потом загрузить."
        )
    return text, info


def _extract_docx(data: bytes) -> tuple[str, dict]:
    try:
        import docx  # python-docx
    except ImportError as exc:
        raise ExtractError(
            "Для .docx нужен пакет python-docx. Переустановите Free LLM Router, "
            "чтобы обновить зависимости."
        ) from exc
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ExtractError(f"Не удалось открыть DOCX: {exc}") from exc

    parts = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    if not text.strip():
        raise ExtractError("DOCX пуст или содержит только картинки.")
    return text, {"paragraphs": len(document.paragraphs), "tables": len(document.tables)}


def extract(name: str, data: bytes) -> dict:
    """Возвращает {'name','kind','chars','pages','text','truncated','warning','metadata'}."""
    if not data:
        raise ExtractError("Файл пустой.")
    if len(data) > MAX_BYTES:
        raise ExtractError(
            f"Файл больше {MAX_BYTES // (1024 * 1024)} МБ — уменьшите его или "
            "разделите на части."
        )

    suffix = PurePath(name or "file").suffix.lower()
    info: dict = {}

    if suffix == ".pdf":
        # «%PDF» в начале — даже если у файла нет расширения
        text, info = _extract_pdf(data)
        kind = "PDF"
    elif suffix == ".docx":
        text, info = _extract_docx(data)
        kind = "DOCX"
    elif suffix == ".doc":
        raise ExtractError(
            "Старый формат .doc не поддерживается — пересохраните файл как .docx или PDF."
        )
    elif suffix in TEXT_EXTS or not suffix:
        if data[:4] == b"%PDF":
            text, info = _extract_pdf(data)
            kind = "PDF"
        else:
            text = _decode(data)
            kind = "текст"
    else:
        raise ExtractError(
            f"Формат «{suffix or 'без расширения'}» не поддерживается. "
            "Можно: PDF, DOCX, TXT, MD, CSV, JSON и текстовые исходники."
        )

    text = text.replace("\x00", "").strip()
    truncated = False
    if len(text) > MAX_CHARS:
        head = text[: MAX_CHARS - 2000]
        tail = text[-1500:]
        text = (
            f"{head}\n\n[... пропущено {len(text) - MAX_CHARS:,} символов "
            f"в середине файла ...]\n\n{tail}"
        ).replace(",", " ")
        truncated = True

    warning = None
    if info.get("empty_pages"):
        warning = (f"В {info['empty_pages']} стр. из {info.get('pages')} нет текстового "
                   f"слоя (вероятно, картинки/сканы) — они пропущены.")

    return {
        "name": name,
        "kind": kind,
        "chars": len(text),
        "pages": info.get("pages"),
        "truncated": truncated,
        "warning": warning,
        "metadata": info.get("metadata", {}),
        "text": text,
    }


def as_prompt_block(files: list[dict]) -> str:
    """Собирает содержимое файлов в один блок для сообщения модели."""
    chunks: list[str] = []
    total = 0
    for f in files:
        text = f["text"]
        if total + len(text) > TOTAL_CHARS:
            left = max(0, TOTAL_CHARS - total)
            text = text[:left] + "\n[... файл урезан из-за общего лимита ...]"
        total += len(text)
        meta = []
        if f.get("pages"):
            meta.append(f"{f['pages']} стр.")
        meta.append(f"{f['chars']:,} симв.".replace(",", " "))
        chunks.append(
            f"=== Файл: {f['name']} ({f['kind']}; {', '.join(meta)}) ===\n{text}"
        )
    return "\n\n".join(chunks)
