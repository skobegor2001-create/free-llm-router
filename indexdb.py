"""Семантический поиск по SQLite-индексу PDF AI (эмбеддинги + текст).

Знает схему вашего приложения (documents + chunks с BLOB-векторами), но умеет
работать и с произвольными базами: FTS5, обычные таблицы, колонки text/content.

Настройки (.env):
    INDEX_DB          путь к pdf_ai.sqlite3
    INDEX_PDF_DIR     папка с исходными PDF (по умолчанию — pdfs рядом с базой)
    INDEX_EMBED_URL   Ollama, по умолчанию http://127.0.0.1:11434
    INDEX_EMBED_MODEL модель эмбеддингов, по умолчанию qwen3-embedding:0.6b
    INDEX_SEARCH      auto | semantic | lexical
    INDEX_MIN_SCORE   порог косинусной близости (по умолчанию 0.25)
    INDEX_TABLE / INDEX_TEXT_COL / INDEX_DOC_COL / INDEX_PAGE_COL — ручное указание

База открывается ТОЛЬКО на чтение.
"""

from __future__ import annotations

import array
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

MAX_LIMIT = 20
SNIPPET_CHARS = 1200
VECTOR_CACHE_TTL = 120          # сек: перечитывать матрицу не чаще
EMBED_TIMEOUT = 20.0            # сек на запрос к Ollama

_TEXT_HINTS = ("content", "text", "body", "chunk", "page_content", "snippet",
               "tokeniz", "page_text", "value", "txt")
_DOC_HINTS = ("filename", "file", "doc", "path", "source", "title", "name", "pdf", "sha")
_PAGE_HINTS = ("page", "pageno", "pagenum", "page_num", "page_number", "page_index")

# --------------------------------------------------------------------------
# Подключение
# --------------------------------------------------------------------------


def db_path() -> Path | None:
    raw = (os.getenv("INDEX_DB") or "").strip().strip('"').strip("'")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.exists() else None


def pdf_dir() -> Path | None:
    raw = (os.getenv("INDEX_PDF_DIR") or "").strip().strip('"').strip("'")
    if raw:
        p = Path(raw).expanduser()
        return p if p.exists() else None
    base = db_path()
    if not base:
        return None
    for candidate in (base.parent / "pdfs", base.parent.parent / "data" / "pdfs",
                      base.parent / "data" / "pdfs"):
        if candidate.exists():
            return candidate
    return None


def _py_lower(value: Any) -> Any:
    """Unicode-lower для SQLite: встроенный LOWER() понимает только ASCII,
    поэтому «Предоплата» и «предоплата» им не совпадают."""
    return value.lower() if isinstance(value, str) else value


def _connect(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path).replace("\\", "/")) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.create_function("ru_lower", 1, _py_lower)
    return conn


def apply_setting(key: str, value: str) -> None:
    """Применяет путь к базе без перезапуска сервера."""
    os.environ[key] = value or ""
    _VEC_CACHE.pop("data", None)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Опознание схемы
# --------------------------------------------------------------------------


def _tables(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        cols = [c["name"] for c in conn.execute(f'PRAGMA table_info("{r["name"]}")')]
        out[r["name"]] = {"columns": cols, "sql": (r["sql"] or "")}
    return out


def detect(conn: sqlite3.Connection) -> dict[str, Any]:
    """Определяет профиль базы: pdf_ai (векторы) либо общий текстовый."""
    tables = _tables(conn)
    lower = {name.lower(): name for name in tables}

    # --- профиль PDF AI: documents + chunks с BLOB-вектором ---
    chunks = lower.get("chunks")
    docs = lower.get("documents")
    if chunks:
        cols = [c.lower() for c in tables[chunks]["columns"]]
        has_vec = any(c in ("vector", "embedding", "embeddings") for c in cols)
        if has_vec and "text" in cols:
            vec_col = next(c for c in tables[chunks]["columns"]
                           if c.lower() in ("vector", "embedding", "embeddings"))
            result = {
                "profile": "pdf_ai",
                "table": chunks,
                "docs_table": docs,
                "text_col": "text",
                "doc_col": "sha",
                "page_col": "page" if "page" in cols else None,
                "part_col": "part" if "part" in cols else None,
                "vector_col": vec_col,
                "fts": False,
            }
            try:
                result["rows"] = conn.execute(f'SELECT COUNT(*) FROM "{chunks}"').fetchone()[0]
                if docs:
                    result["documents"] = conn.execute(
                        f'SELECT COUNT(*) FROM "{docs}"').fetchone()[0]
                size = conn.execute(
                    f'SELECT LENGTH("{vec_col}") FROM "{chunks}" LIMIT 1').fetchone()
                result["dims"] = (size[0] // 4) if size and size[0] else None
            except sqlite3.Error:
                pass
            return {"selected": result, "candidates": [result]}

    # --- общий профиль: таблица с текстом (FTS5 или обычная), как раньше ---
    candidates: list[dict[str, Any]] = []
    for name, info in tables.items():
        cols = info["columns"]
        text_col = _pick(cols, _TEXT_HINTS)
        if not text_col:
            continue
        is_fts = "using fts" in (info["sql"] or "").lower()
        cand = {
            "profile": "generic",
            "table": name, "docs_table": None, "fts": is_fts,
            "text_col": text_col,
            "doc_col": _pick(cols, _DOC_HINTS),
            "page_col": _pick(cols, _PAGE_HINTS),
            "part_col": None, "vector_col": None,
        }
        try:
            cand["rows"] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error:
            cand["rows"] = 0
        cand["_score"] = (5 if is_fts else 0) + (3 if cand["doc_col"] else 0) + \
                         (1 if cand["page_col"] else 0)
        candidates.append(cand)
    candidates.sort(key=lambda c: (c["_score"], c["rows"]), reverse=True)
    for c in candidates:
        c.pop("_score", None)

    best = candidates[0] if candidates else None
    if best:
        for key, env in (("table", "INDEX_TABLE"), ("text_col", "INDEX_TEXT_COL"),
                         ("doc_col", "INDEX_DOC_COL"), ("page_col", "INDEX_PAGE_COL")):
            forced = (os.getenv(env) or "").strip()
            if forced:
                best[key] = forced
    return {"selected": best, "candidates": candidates[:6]}


def _pick(columns: list[str], hints: tuple[str, ...]) -> str | None:
    lowered = [(c, c.lower()) for c in columns]
    for hint in hints:
        for name, low in lowered:
            if low == hint:
                return name
    for hint in hints:
        for name, low in lowered:
            if hint in low:
                return name
    return None


# --------------------------------------------------------------------------
# Эмбеддинги запроса через Ollama
# --------------------------------------------------------------------------


def _hash_vector(text: str, dims: int) -> list[float]:
    """Детерминированный вектор по словам — только для тестов/офлайна."""
    vec = [0.0] * dims
    for word in re.findall(r"[\w\-]+", text.lower()):
        h = int.from_bytes(word.encode("utf-8")[:8].ljust(8, b"\0"), "little")
        vec[h % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def embed_query(text: str) -> list[float] | None:
    """Вектор запроса. None — если Ollama недоступна (тогда сработает текст)."""
    if (os.getenv("INDEX_EMBED_MODE") or "").lower() == "hash":
        dims = int(os.getenv("INDEX_DIMS") or 1024)
        return _hash_vector(text, dims)

    url = (os.getenv("INDEX_EMBED_URL") or "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("INDEX_EMBED_MODEL") or "qwen3-embedding:0.6b"
    instruct = (os.getenv("INDEX_EMBED_INSTRUCT") or "").strip()
    payload_text = (instruct + "\n" + text) if instruct else text

    try:
        import httpx
        with httpx.Client(timeout=EMBED_TIMEOUT) as client:
            r = client.post(f"{url}/api/embed", json={"model": model, "input": payload_text})
            if r.status_code == 404:      # старый Ollama
                r = client.post(f"{url}/api/embeddings",
                                json={"model": model, "prompt": payload_text})
            r.raise_for_status()
            data = r.json()
        vec = data.get("embeddings", [None])[0] or data.get("embedding")
        if not vec:
            return None
        norm = math.sqrt(sum(float(x) * float(x) for x in vec)) or 1.0
        return [float(x) / norm for x in vec]
    except Exception:
        return None


def ollama_state() -> dict[str, Any]:
    url = (os.getenv("INDEX_EMBED_URL") or "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("INDEX_EMBED_MODEL") or "qwen3-embedding:0.6b"
    if (os.getenv("INDEX_EMBED_MODE") or "").lower() == "hash":
        return {"ok": True, "url": "hash", "model": "hash", "has_model": True}
    try:
        import httpx
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{url}/api/tags")
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
        return {"ok": True, "url": url, "model": model,
                "has_model": any(n == model or n.startswith(model.split(":")[0]) for n in names)}
    except Exception:
        return {"ok": False, "url": url, "model": model, "has_model": False}


# --------------------------------------------------------------------------
# Загрузка векторов (кеш)
# --------------------------------------------------------------------------

_VEC_CACHE: dict[str, Any] = {}


def _load_vectors(conn: sqlite3.Connection, sel: dict[str, Any]) -> dict[str, Any] | None:
    path = str(db_path())
    try:
        stat = os.stat(path)
        key = f"{stat.st_mtime_ns}:{stat.st_size}:{sel['table']}"
    except OSError:
        return None
    cached = _VEC_CACHE.get("data")
    if cached and cached["key"] == key and time.time() - cached["ts"] < VECTOR_CACHE_TTL:
        return cached

    table, vec_col = sel["table"], sel["vector_col"]
    docs_table = sel.get("docs_table")
    if docs_table:
        sql = (f'SELECT c."{sel["doc_col"]}" AS sha, c."page" AS page, c."part" AS part, '
               f'c."{sel["text_col"]}" AS text, c."{vec_col}" AS vector, '
               f'd."filename" AS filename FROM "{table}" c '
               f'LEFT JOIN "{docs_table}" d ON d.sha = c."{sel["doc_col"]}"')
    else:
        sql = (f'SELECT * FROM "{table}"')
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error:
        return None

    buffers: list[bytes] = []
    meta: list[dict[str, Any]] = []
    dims = None
    for row in rows:
        blob = row["vector"]
        if not isinstance(blob, (bytes, bytearray)) or len(blob) % 4:
            continue
        if dims is None:
            dims = len(blob) // 4
        if len(blob) // 4 != dims:
            continue
        buffers.append(bytes(blob))
        meta.append({
            "sha": row["sha"],
            "page": row["page"] if "page" in row.keys() else None,
            "part": row["part"] if "part" in row.keys() else None,
            "text": row["text"] if "text" in row.keys() else "",
            "filename": row["filename"] if "filename" in row.keys() else None,
        })
    if not buffers or not dims:
        return None

    payload: dict[str, Any] = {"key": key, "ts": time.time(), "dims": dims,
                               "meta": meta, "buffers": buffers, "matrix": None}
    try:
        import numpy as np
        payload["matrix"] = np.vstack(
            [np.frombuffer(b, dtype="<f4") for b in buffers]).astype("float32")
    except Exception:
        payload["matrix"] = None
    _VEC_CACHE["data"] = payload
    return payload


def _cosine_top(payload: dict[str, Any], qvec: list[float], k: int) -> list[tuple[int, float]]:
    matrix = payload.get("matrix")
    if matrix is not None:
        try:
            import numpy as np
            q = np.asarray(qvec, dtype="float32")
            scores = matrix @ q
            idx = np.argsort(-scores)[:k]
            return [(int(i), float(scores[i])) for i in idx]
        except Exception:
            pass
    # чистый Python: ~0.65 с на 5 000 чанков
    flat = array.array("f")
    out: list[tuple[int, float]] = []
    for i, buf in enumerate(payload["buffers"]):
        flat.frombytes(buf)
        if len(flat) != len(qvec):
            del flat[:]
            continue
        s = 0.0
        for a, b in zip(qvec, flat):
            s += a * b
        out.append((i, s))
        del flat[:]
    out.sort(key=lambda t: t[1], reverse=True)
    return out[:k]


# --------------------------------------------------------------------------
# Поиск
# --------------------------------------------------------------------------

_RU_ENDINGS = ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
               "ей", "ой", "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие",
               "ов", "ев", "ах", "ях", "ам", "ям", "ом", "ем", "ую", "юю",
               "ия", "ии", "ий", "ть", "а", "я", "ы", "и", "е", "у", "ю", "о", "ь")


def _stem_ru(word: str) -> str:
    low = word.lower()
    if not re.search("[а-яё]", low):
        return word
    for ending in _RU_ENDINGS:
        if low.endswith(ending) and len(low) - len(ending) >= 4:
            return word[: len(word) - len(ending)]
    return word


def _variants(token: str) -> list[str]:
    stem = _stem_ru(token)
    return list(dict.fromkeys([token] + ([stem] if stem and stem != token else [])))


def _tokens(query: str) -> list[str]:
    words = re.findall(r"[\w\-]{2,}", query, re.UNICODE)
    seen, out = set(), []
    for w in words:
        low = w.lower()
        if low not in seen and not low.isdigit():
            seen.add(low)
            out.append(w)
    return out[:12]


def _snippet(text: str, tokens: list[str], size: int = SNIPPET_CHARS) -> str:
    if not text:
        return ""
    flat = " ".join(text.split())
    if len(flat) <= size:
        return flat
    low = flat.lower()
    pos = -1
    for t in tokens:
        for v in _variants(t):
            pos = low.find(v.lower())
            if pos >= 0:
                break
        if pos >= 0:
            break
    start = max(0, (pos if pos >= 0 else 0) - size // 3)
    return ("…" if start else "") + flat[start:start + size] + "…"


def _search_vectors(query: str, conn: sqlite3.Connection, sel: dict[str, Any],
                    limit: int) -> list[dict[str, Any]]:
    payload = _load_vectors(conn, sel)
    if not payload:
        return []
    qvec = embed_query(query)
    if not qvec:
        return []
    if len(qvec) != payload["dims"]:
        return []
    min_score = _env_float("INDEX_MIN_SCORE", 0.25)
    tokens = _tokens(query)
    hits = []
    for idx, score in _cosine_top(payload, qvec, max(limit * 3, 12)):
        if score < min_score:
            continue
        m = payload["meta"][idx]
        hits.append({
            "doc": m.get("filename") or m.get("sha"),
            "sha": m.get("sha"),
            "page": m.get("page"),
            "part": m.get("part"),
            "text": _snippet(m.get("text") or "", tokens),
            "score": round(float(score), 4),
            "mode": "semantic",
        })
        if len(hits) >= limit:
            break
    return hits


def _search_lexical(query: str, conn: sqlite3.Connection, sel: dict[str, Any],
                    limit: int) -> list[dict[str, Any]]:
    tokens = _tokens(query)
    if not tokens:
        return []
    table, tcol = sel["table"], sel["text_col"]
    doc_expr = f'c."{sel["doc_col"]}"' if sel.get("doc_col") else "NULL"
    page_expr = f'c."{sel["page_col"]}"' if sel.get("page_col") else "NULL"
    part_expr = f'c."{sel.get("part_col")}"' if sel.get("part_col") else "NULL"

    if sel.get("profile") == "pdf_ai" and sel.get("docs_table"):
        select = (f'SELECT {doc_expr} AS sha, {page_expr} AS page, {part_expr} AS part, '
                  f'c."{tcol}" AS text, d."filename" AS filename '
                  f'FROM "{table}" c LEFT JOIN "{sel["docs_table"]}" d '
                  f'ON d.sha = c."{sel["doc_col"]}"')
    else:
        select = (f'SELECT {doc_expr} AS sha, {page_expr} AS page, {part_expr} AS part, '
                  f'"{tcol}" AS text, {doc_expr} AS filename FROM "{table}" c')

    rows: list[sqlite3.Row] = []
    if sel.get("fts"):
        fts_q = " OR ".join(f'"{v}"*' for t in tokens for v in _variants(t))
        try:
            rows = conn.execute(
                f'{select} WHERE "{table}" MATCH ? ORDER BY rank LIMIT ?',
                (fts_q, limit * 3)).fetchall()
        except sqlite3.Error:
            rows = []
    if not rows:
        needles = [v.lower() for t in tokens for v in _variants(t)]
        needles = list(dict.fromkeys(needles))
        where = " OR ".join([f'ru_lower(c."{tcol}") LIKE ?'] * len(needles))
        rows = conn.execute(f'{select} WHERE {where} LIMIT ?',
                            [f"%{n}%" for n in needles] + [limit * 3]).fetchall()

    out = []
    for row in rows:
        text = row["text"] or ""
        low = text.lower()
        score = sum(low.count(v.lower()) for t in tokens for v in _variants(t))
        out.append({
            "doc": row["filename"] or row["sha"], "sha": row["sha"],
            "page": row["page"], "part": row["part"],
            "text": _snippet(text, tokens), "score": score, "mode": "lexical",
        })
    out.sort(key=lambda r: r["score"], reverse=True)
    return out[:limit]


def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Гибридный поиск: эмбеддинги (если живы), иначе текст."""
    path = db_path()
    if path is None or not (query or "").strip():
        return []
    limit = max(1, min(int(limit or 5), MAX_LIMIT))
    mode = (os.getenv("INDEX_SEARCH") or "auto").lower()

    try:
        conn = _connect(path)
    except sqlite3.Error:
        return []
    try:
        sel = detect(conn)["selected"]
        if not sel:
            return []

        semantic: list[dict[str, Any]] = []
        if mode in ("auto", "semantic") and sel.get("vector_col"):
            semantic = _search_vectors(query, conn, sel, limit)
            if semantic:
                return semantic

        if mode == "semantic":
            return []
        return _search_lexical(query, conn, sel, limit)
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def as_context(query: str, limit: int = 5, max_chars: int = 18000) -> tuple[str, list[dict]]:
    hits = search(query, limit)
    if not hits:
        return "", []
    parts, used = [], 0
    for h in hits:
        label = h.get("doc") or "фрагмент"
        if h.get("page") not in (None, ""):
            label += f", стр. {h['page']}"
        piece = f"[{label}]\n{h['text']}"
        if used + len(piece) > max_chars:
            break
        parts.append(piece)
        used += len(piece)
    mode = "эмбеддинги" if hits[0].get("mode") == "semantic" else "поиск по словам"
    block = (
        f"Ниже — фрагменты из личного индекса PDF ({mode}). "
        "Отвечай по ним и ссылайся на источник в квадратных скобках.\n\n"
        + "\n\n---\n\n".join(parts)
    )
    return block, hits


# --------------------------------------------------------------------------
# Страница исходного PDF (для предпросмотра источника)
# --------------------------------------------------------------------------


def page_text(sha: str, page: int) -> dict[str, Any]:
    """Текст страницы из data\\pdfs\\<sha>.pdf (лениво, только по запросу)."""
    folder = pdf_dir()
    if not folder:
        return {"error": "Папка с PDF не найдена (INDEX_PDF_DIR)"}
    target = folder / f"{sha}.pdf"
    if not target.exists():
        return {"error": f"Файл {sha}.pdf не найден в {folder}"}
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(target))
        total = len(reader.pages)
        if not (1 <= page <= total):
            return {"error": f"Страницы {page} нет (всего {total})"}
        text = (reader.pages[page - 1].extract_text() or "").strip()
        return {"sha": sha, "page": page, "pages": total,
                "text": text or "(на этой странице нет текстового слоя)"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Не удалось открыть PDF: {exc}"}


# --------------------------------------------------------------------------
# Статус
# --------------------------------------------------------------------------


def status() -> dict[str, Any]:
    path = db_path()
    if path is None:
        raw = (os.getenv("INDEX_DB") or "").strip()
        return {
            "configured": False,
            "reason": ("INDEX_DB не задан" if not raw else "файл базы не найден"),
            "hint": "Укажите путь в .env: INDEX_DB=C:\\...\\pdf_ai.sqlite3",
        }
    try:
        conn = _connect(path)
        try:
            info = detect(conn)
            sel = info["selected"]
            if not sel:
                return {"configured": True, "path": str(path), "found": False,
                        "hint": "Не нашёл таблицу с текстом (INDEX_TABLE/INDEX_TEXT_COL)."}
            data = {
                "configured": True, "path": str(path), "found": True,
                "profile": sel.get("profile"), "table": sel["table"],
                "fts": sel.get("fts", False), "rows": sel.get("rows"),
                "documents": sel.get("documents"),
                "dims": sel.get("dims"), "vector_col": sel.get("vector_col"),
                "text_col": sel.get("text_col"), "doc_col": sel.get("doc_col"),
                "page_col": sel.get("page_col"),
                "pdf_dir": str(pdf_dir()) if pdf_dir() else None,
                "embed": ollama_state(),
                "mode": (os.getenv("INDEX_SEARCH") or "auto"),
            }
            return data
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"configured": True, "path": str(path), "found": False,
                "error": f"SQLite: {exc}"}
