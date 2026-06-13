"""Репозиторий пользователей (SQLite, WAL) в рабочем каталоге.

Таблица users(login PK, password, role, department, display_name).
Пароли хэшируются через lightrag.api.passwords (bcrypt). Соединение —
процессный синглтон (деплой однопроцессный: один uvicorn-воркер)."""

import os
import sqlite3

from lightrag.api.passwords import hash_password, verify_password
from lightrag.api.repos.paths import get_lock, users_db_path

_user_db_conn: sqlite3.Connection | None = None


def _get_user_db() -> sqlite3.Connection:
    global _user_db_conn
    if _user_db_conn is None:
        path = users_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _user_db_conn = sqlite3.connect(str(path), check_same_thread=False)
        _user_db_conn.row_factory = sqlite3.Row
        _user_db_conn.execute("PRAGMA journal_mode=WAL")
        _user_db_conn.execute("PRAGMA busy_timeout=5000")
        _user_db_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                login        TEXT PRIMARY KEY,
                password     TEXT NOT NULL,
                role         TEXT NOT NULL DEFAULT 'user',
                department   TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT ''
            )
            """
        )
        _user_db_conn.commit()
    return _user_db_conn


def read_users() -> list[dict]:
    db = _get_user_db()
    rows = db.execute(
        "SELECT login, role, department, display_name FROM users ORDER BY login"
    ).fetchall()
    return [dict(row) for row in rows]


def read_users_paginated(page: int = 1, page_size: int = 50, search: str = "") -> dict:
    """Страница пользователей с опциональным поиском по логину/имени.

    Возвращает {items, total, page, page_size}. Пагинация в SQL (LIMIT/OFFSET),
    чтобы не грузить десятки тысяч строк в память."""
    db = _get_user_db()
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    offset = (page - 1) * page_size
    search = (search or "").strip()
    if search:
        like = f"%{search}%"
        total = db.execute(
            "SELECT COUNT(*) FROM users WHERE login LIKE ? OR display_name LIKE ?",
            (like, like),
        ).fetchone()[0]
        rows = db.execute(
            "SELECT login, role, department, display_name FROM users "
            "WHERE login LIKE ? OR display_name LIKE ? ORDER BY login LIMIT ? OFFSET ?",
            (like, like, page_size, offset),
        ).fetchall()
    else:
        total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        rows = db.execute(
            "SELECT login, role, department, display_name FROM users "
            "ORDER BY login LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def reassign_department(old_name: str, new_name: str) -> int:
    """Переназначить отдел всех пользователей одним UPDATE (без загрузки всех)."""
    db = _get_user_db()
    with get_lock("users"):
        cur = db.execute(
            "UPDATE users SET department = ? WHERE department = ?", (new_name, old_name)
        )
        db.commit()
    return cur.rowcount


def find_user(login: str) -> dict | None:
    db = _get_user_db()
    row = db.execute(
        "SELECT login, password, role, department, display_name FROM users WHERE login = ?",
        (login,),
    ).fetchone()
    return dict(row) if row else None


def has_users() -> bool:
    db = _get_user_db()
    return db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def create_user(login: str, password: str, role: str = "user", department: str = "", display_name: str = "") -> dict:
    login = (login or "").strip()
    if not login:
        raise ValueError("Логин обязателен")
    if not password:
        raise ValueError("Пароль обязателен")
    if role not in ("admin", "user"):
        raise ValueError("Роль должна быть 'admin' или 'user'")
    if find_user(login):
        raise ValueError("Пользователь с таким логином уже существует")
    db = _get_user_db()
    with get_lock("users"):
        db.execute(
            "INSERT INTO users (login, password, role, department, display_name) VALUES (?, ?, ?, ?, ?)",
            (login, hash_password(password), role, department or "", display_name or login),
        )
        db.commit()
    return {"login": login, "role": role, "department": department or "", "display_name": display_name or login}


def update_user(login: str, **updates) -> dict | None:
    """Обновляет поля пользователя. `password` (если задан непустым) хэшируется."""
    allowed = {"password", "role", "department", "display_name"}
    filtered = {k: v for k, v in updates.items() if k in allowed and v is not None}
    # Пустой пароль означает «не менять».
    if "password" in filtered:
        if not str(filtered["password"]).strip():
            filtered.pop("password")
        else:
            filtered["password"] = hash_password(str(filtered["password"]))
    if "role" in filtered and filtered["role"] not in ("admin", "user"):
        raise ValueError("Роль должна быть 'admin' или 'user'")
    if not filtered:
        return find_user(login)
    set_clause = ", ".join(f"{col} = ?" for col in filtered)
    values = list(filtered.values()) + [login]
    db = _get_user_db()
    with get_lock("users"):
        db.execute(f"UPDATE users SET {set_clause} WHERE login = ?", values)
        db.commit()
    return find_user(login)


def delete_user(login: str) -> None:
    db = _get_user_db()
    with get_lock("users"):
        db.execute("DELETE FROM users WHERE login = ?", (login,))
        db.commit()


def authenticate(login: str, password: str) -> dict | None:
    """Проверяет логин/пароль. Возвращает запись пользователя (без пароля) или None."""
    user = find_user((login or "").strip())
    if not user:
        return None
    if not verify_password(password or "", user.get("password", "")):
        return None
    return {
        "login": user["login"],
        "role": user.get("role", "user"),
        "department": user.get("department", ""),
        "display_name": user.get("display_name", user["login"]),
    }


def bootstrap_admin() -> None:
    """Создаёт стартового администратора, если таблица пуста.

    По умолчанию admin/admin (меняется в админке). Переопределяется через
    env RAG_BOOTSTRAP_ADMIN_LOGIN / RAG_BOOTSTRAP_ADMIN_PASSWORD."""
    _get_user_db()
    if has_users():
        return
    login = (os.getenv("RAG_BOOTSTRAP_ADMIN_LOGIN") or "admin").strip() or "admin"
    password = (os.getenv("RAG_BOOTSTRAP_ADMIN_PASSWORD") or "admin").strip() or "admin"
    create_user(
        login=login,
        password=password,
        role="admin",
        department="all",
        display_name="Администратор",
    )
