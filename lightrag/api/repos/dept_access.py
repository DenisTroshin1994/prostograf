"""Отделы и их доступ к документам (ACL).

Отдел — это каталог `departments/<name>/` с файлом `access.json`
вида `{"files": [...]}`, где files — список разрешённых отделу путей
документов (file_path). Документы индексируются один раз; отдел — это
только ACL (allow-list). Новый документ по умолчанию недоступен ни одному
отделу (default-deny)."""

import json
import shutil

from lightrag.api.repos.paths import (
    atomic_write_json,
    atomic_write_text,
    departments_dir,
    department_dir,
    ensure_flat_name,
    get_lock,
    resolve_within,
)

# Особый отдел/маркер «полный доступ» — для администраторов.
ALL_ACCESS = "all"


def _access_path(dept_name: str):
    safe = ensure_flat_name(dept_name, "Имя отдела")
    return resolve_within(departments_dir(), safe, "access.json")


def get_departments() -> list[str]:
    base = departments_dir()
    if not base.exists():
        return []
    return sorted(item.name for item in base.iterdir() if item.is_dir())


def create_department(name: str) -> bool:
    d = department_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    access = d / "access.json"
    if not access.exists():
        atomic_write_json(access, {"files": []})
    return True


def delete_department(name: str) -> None:
    d = department_dir(name)
    if d.exists():
        shutil.rmtree(d)


def rename_department(old: str, new: str) -> None:
    """Переименовывает отдел: переносит каталог (с access.json)."""
    old_dir = department_dir(old)
    new_dir = department_dir(new)
    if not old_dir.exists():
        raise ValueError("Отдел не найден")
    if new_dir.exists():
        raise ValueError("Отдел с новым именем уже существует")
    with get_lock(f"dept:{old}"), get_lock(f"dept:{new}"):
        old_dir.rename(new_dir)


def get_dept_access(dept_name: str) -> list[str]:
    """Список путей документов, доступных отделу. [] если файла нет/он битый."""
    try:
        path = _access_path(dept_name)
    except ValueError:
        return []
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []
    files = data.get("files", []) if isinstance(data, dict) else []
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, str)]


def set_dept_access(dept_name: str, filenames: list[str]) -> list[str]:
    """Полностью заменяет allow-list отдела (дедуп + сортировка)."""
    path = _access_path(dept_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = sorted({f.strip() for f in filenames if isinstance(f, str) and f.strip()})
    with get_lock(f"dept:{dept_name}"):
        atomic_write_json(path, {"files": cleaned})
    return cleaned


def remove_file_from_all_access(filename: str) -> int:
    """Удаляет документ из allow-list всех отделов (при удалении документа)."""
    removed = 0
    for dept in get_departments():
        current = get_dept_access(dept)
        if filename in current:
            set_dept_access(dept, [f for f in current if f != filename])
            removed += 1
    return removed


def get_dept_prompt(dept_name: str) -> str:
    """Промпт ответа отдела (`departments/<name>/prompt.txt`). '' если не задан.

    Подмешивается как user_prompt в генерацию для пользователей этого отдела —
    задаёт роль/тон/формат ответа отдельно для каждого отдела."""
    try:
        path = resolve_within(
            departments_dir(), ensure_flat_name(dept_name, "Имя отдела"), "prompt.txt"
        )
    except ValueError:
        return ""
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def set_dept_prompt(dept_name: str, prompt: str) -> str:
    """Сохраняет промпт ответа отдела (пустая строка очищает)."""
    safe = ensure_flat_name(dept_name, "Имя отдела")
    d = resolve_within(departments_dir(), safe)
    d.mkdir(parents=True, exist_ok=True)
    path = resolve_within(departments_dir(), safe, "prompt.txt")
    with get_lock(f"dept:{dept_name}"):
        atomic_write_text(path, prompt or "")
    return prompt or ""


def resolve_allowed_file_paths(department: str) -> set[str] | None:
    """Разрешённые пользователю пути документов по его отделу.

    Возвращает None (без ограничений) ТОЛЬКО для маркера полного доступа
    (department == 'all'). Пустой/неизвестный отдел трактуется как «нет
    доступа» (пустое множество) — fail-closed: функция не должна молча
    открывать полный доступ. Решение «admin → полный доступ» принимается
    выше по стеку (см. utils_api.resolve_allowed_file_paths_for_user)."""
    if department == ALL_ACCESS:
        return None
    if not department:
        return set()
    return set(get_dept_access(department))
