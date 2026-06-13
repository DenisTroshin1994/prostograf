"""Дополнительные метаданные документов: METAINFO и флаг «целиком».

Хранится отдельно от самих документов в WORKING_DIR/doc_meta.json:
    { "<file_path>": { "metainfo": "...", "full": true } }
Ключ — file_path (канонический basename документа, как в ACL отделов).

- metainfo: текст-приписка, подмешивается в контекст генератора для найденного
  документа, но сам документ НЕ меняет.
- full: при попадании документа в результаты поиска в генератор включается его
  ПОЛНЫЙ текст (для процедурных инструкций, где важны все шаги).
"""

import json

from lightrag.api.repos.paths import atomic_write_json, data_dir, get_lock


def _path():
    return data_dir() / "doc_meta.json"


def get_all() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get(file_path: str) -> dict:
    return get_all().get(file_path, {}) or {}


def set_meta(file_path: str, metainfo: str | None = None, full: bool | None = None) -> dict:
    """Обновляет метаданные документа. None-поля не трогаются; пустой metainfo и
    full=False удаляют соответствующие ключи. Возвращает итоговую запись."""
    file_path = (file_path or "").strip()
    if not file_path:
        raise ValueError("file_path обязателен")
    with get_lock("doc_meta"):
        data = get_all()
        entry = dict(data.get(file_path, {}) or {})
        if metainfo is not None:
            mi = str(metainfo).strip()
            if mi:
                entry["metainfo"] = mi
            else:
                entry.pop("metainfo", None)
        if full is not None:
            if full:
                entry["full"] = True
            else:
                entry.pop("full", None)
        if entry:
            data[file_path] = entry
        else:
            data.pop(file_path, None)
        atomic_write_json(_path(), data)
        return entry


def remove(file_path: str) -> None:
    with get_lock("doc_meta"):
        data = get_all()
        if file_path in data:
            data.pop(file_path, None)
            atomic_write_json(_path(), data)


def metainfo_map() -> dict:
    """file_path -> metainfo (только непустые)."""
    return {fp: e["metainfo"] for fp, e in get_all().items() if isinstance(e, dict) and e.get("metainfo")}


def full_paths() -> set:
    """Множество file_path с флагом «целиком»."""
    return {fp for fp, e in get_all().items() if isinstance(e, dict) and e.get("full")}
