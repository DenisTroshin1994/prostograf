"""Пути, именованные блокировки, атомарная запись и проверки имён.

Базовый слой без доменных зависимостей. Все репозитории (пользователи,
отделы, чаты) хранят данные внутри рабочего каталога (WORKING_DIR — том
данных), поэтому переживают пересоздание контейнера."""

import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path


def data_dir() -> Path:
    """Корень для персистентных данных — рабочий каталог (том данных)."""
    return Path(os.getenv("WORKING_DIR", "./rag_storage"))


def users_db_path() -> Path:
    return data_dir() / "users.db"


def jwt_secret_path() -> Path:
    return data_dir() / "jwt_secret.txt"


def chats_db_path() -> Path:
    return data_dir() / "chats.db"


def departments_dir() -> Path:
    return data_dir() / "departments"


# ── Именованные блокировки ──────────────────────────────────────

_FILE_LOCKS: dict[str, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def get_lock(name: str) -> threading.RLock:
    """Возвращает процессную именованную RLock (идемпотентно)."""
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(name)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[name] = lock
        return lock


def chat_lock_name(chat_id: str) -> str:
    return f"chat:{chat_id}"


# ── Помощники путей ─────────────────────────────────────────────


def _normalize_path(path: Path) -> Path:
    return Path(path).resolve(strict=False)


_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def ensure_flat_name(name: str, field_name: str) -> str:
    """Проверяет, что `name` — плоское имя (без разделителей, `..`, нулей)."""
    value = (name or "").strip()
    if not value:
        raise ValueError(f"{field_name} обязательно")
    if value in {".", ".."}:
        raise ValueError(f"{field_name} некорректно")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"{field_name} не должно содержать разделителей пути")
    if Path(value).name != value:
        raise ValueError(f"{field_name} должно быть плоским именем")
    # Зарезервированные имена устройств Windows и завершающие точки/пробелы
    # создают каталоги/файлы, которые потом нельзя надёжно прочитать/удалить.
    if value != value.rstrip(". "):
        raise ValueError(f"{field_name} не должно оканчиваться точкой или пробелом")
    stem = value.split(".")[0].lower()
    if stem in _WINDOWS_RESERVED:
        raise ValueError(f"{field_name} использует зарезервированное системное имя")
    return value


def resolve_within(base_dir: Path, *parts: str) -> Path:
    """Разрешает `parts` внутри `base_dir`; запрещает выход за пределы."""
    base = _normalize_path(base_dir)
    candidate = _normalize_path(base.joinpath(*parts))
    if os.path.commonpath([str(base), str(candidate)]) != str(base):
        raise ValueError("Путь выходит за пределы каталога данных")
    return candidate


def department_dir(dept_name: str) -> Path:
    return resolve_within(departments_dir(), ensure_flat_name(dept_name, "Имя отдела"))


# ── Атомарная запись ────────────────────────────────────────────


def atomic_write_text(path: Path, content: str, _retries: int = 8, _base_delay: float = 0.02):
    """Атомарно пишет `content` в `path` через tempfile + os.replace.

    Повторяет при PermissionError (антивирус/блокировки на Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        last_err = None
        for attempt in range(_retries):
            try:
                os.replace(temp_name, path)
                return
            except PermissionError as exc:
                last_err = exc
                time.sleep(_base_delay * (2 ** attempt))
        raise last_err  # type: ignore[misc]
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass


def atomic_write_json(path: Path, payload):
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


# ── Персистентный секрет JWT ─────────────────────────────────────

_JWT_SECRET_GUARD = threading.Lock()


def get_or_create_jwt_secret() -> str:
    """Возвращает стойкий случайный секрет JWT из тома данных, создавая его
    при первом запуске.

    Нужен, потому что многопользовательский режим не использует AUTH_ACCOUNTS,
    из-за чего auth_handler по умолчанию падает на ПУБЛИЧНО ИЗВЕСТНЫЙ
    DEFAULT_TOKEN_SECRET (любой смог бы подделать admin-токен). Секрет хранится
    в томе данных и переживает пересоздание контейнера, поэтому ранее выданные
    токены остаются валидны между перезапусками. Переопределяется явным
    TOKEN_SECRET в окружении (обрабатывается вызывающей стороной)."""
    path = jwt_secret_path()
    with _JWT_SECRET_GUARD:
        try:
            if path.exists():
                existing = path.read_text(encoding="utf-8").strip()
                if existing:
                    return existing
        except OSError:
            pass
        secret = secrets.token_urlsafe(48)
        atomic_write_text(path, secret)
        return secret
