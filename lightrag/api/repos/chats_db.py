"""История диалогов (SQLite, WAL) в рабочем каталоге.

chats(id, login, title, created_at, updated_at, archived_at, model, mode)
messages(chat_id, mid, seq, role, content, created_at, pending, status,
         is_error, used_docs[json], rewritten_query, model, mode, latency_ms,
         prompt_tokens, completion_tokens, total_tokens,
         feedback, feedback_reason, feedback_comment, feedback_updated_at)

`archived_at IS NULL` ⇔ активный диалог. `mid` — стабильный ID сообщения.
Хранит реальные токены ответа и переписанный поисковый запрос."""

import json
import random
import sqlite3
import string
import threading
import time

from lightrag.api.repos.paths import chat_lock_name, chats_db_path, ensure_flat_name, get_lock

FEEDBACK_REASONS = {"off_topic", "incomplete", "excessive", "other"}

_chats_db_conn: sqlite3.Connection | None = None
_CHATS_DB_LOCK = threading.RLock()


def _get_chats_db() -> sqlite3.Connection:
    global _chats_db_conn
    if _chats_db_conn is None:
        path = chats_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _chats_db_conn = sqlite3.connect(str(path), check_same_thread=False)
        _chats_db_conn.row_factory = sqlite3.Row
        _chats_db_conn.execute("PRAGMA journal_mode=WAL")
        _chats_db_conn.execute("PRAGMA busy_timeout=5000")
        _chats_db_conn.execute("PRAGMA foreign_keys=ON")
        _chats_db_conn.execute("PRAGMA synchronous=NORMAL")
        _chats_db_conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id           TEXT PRIMARY KEY,
                login        TEXT NOT NULL,
                title        TEXT NOT NULL DEFAULT '',
                created_at   INTEGER NOT NULL,
                updated_at   INTEGER NOT NULL,
                archived_at  INTEGER,
                model        TEXT NOT NULL DEFAULT '',
                mode         TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_chats_login_active
                ON chats(login, archived_at, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_chats_admin_active
                ON chats(archived_at, updated_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                chat_id        TEXT NOT NULL,
                mid            TEXT NOT NULL,
                seq            INTEGER NOT NULL,
                role           TEXT NOT NULL,
                content        TEXT NOT NULL DEFAULT '',
                created_at     INTEGER NOT NULL,
                pending        INTEGER,
                status         TEXT,
                is_error       INTEGER,
                used_docs      TEXT,
                rewritten_query TEXT,
                model          TEXT,
                mode           TEXT,
                latency_ms     INTEGER,
                prompt_tokens     INTEGER,
                completion_tokens INTEGER,
                total_tokens      INTEGER,
                feedback       TEXT,
                feedback_reason TEXT,
                feedback_comment TEXT,
                feedback_updated_at INTEGER,
                PRIMARY KEY (chat_id, mid),
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat_seq
                ON messages(chat_id, seq);
            """
        )
        _chats_db_conn.commit()
    return _chats_db_conn


_ID_ALPHABET = string.ascii_lowercase + string.digits


def generate_chat_id() -> str:
    return "".join(random.choices(_ID_ALPHABET, k=10))


def _generate_mid() -> str:
    return "".join(random.choices(_ID_ALPHABET, k=10))


def _now() -> int:
    return int(time.time())


def _row_to_message(row: sqlite3.Row) -> dict:
    msg = {
        "mid": row["mid"],
        "role": row["role"],
        "content": row["content"] or "",
        "created_at": row["created_at"],
    }
    if row["role"] == "assistant":
        msg["pending"] = bool(row["pending"]) if row["pending"] is not None else False
        msg["status"] = row["status"] or ""
        msg["is_error"] = bool(row["is_error"]) if row["is_error"] is not None else False
        try:
            msg["used_docs"] = json.loads(row["used_docs"]) if row["used_docs"] else []
        except (json.JSONDecodeError, TypeError):
            msg["used_docs"] = []
        for col in ("rewritten_query", "model", "mode"):
            if row[col]:
                msg[col] = row[col]
        for col in ("latency_ms", "prompt_tokens", "completion_tokens", "total_tokens"):
            if row[col] is not None:
                msg[col] = row[col]
        if row["feedback"]:
            msg["feedback"] = row["feedback"]
        if row["feedback_reason"]:
            msg["feedback_reason"] = row["feedback_reason"]
        if row["feedback_comment"]:
            msg["feedback_comment"] = row["feedback_comment"]
        if row["feedback_updated_at"] is not None:
            msg["feedback_updated_at"] = row["feedback_updated_at"]
    return msg


def _row_to_chat(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "login": row["login"],
        "title": row["title"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "model": row["model"] or "",
        "mode": row["mode"] or "",
    }


def _load_messages(db, chat_id: str) -> list:
    rows = db.execute(
        "SELECT * FROM messages WHERE chat_id = ? ORDER BY seq ASC, created_at ASC",
        (chat_id,),
    ).fetchall()
    return [_row_to_message(r) for r in rows]


def _load_chat(chat_id: str, *, archived: bool | None = None) -> dict | None:
    db = _get_chats_db()
    where = "id = ?"
    if archived is True:
        where += " AND archived_at IS NOT NULL"
    elif archived is False:
        where += " AND archived_at IS NULL"
    with _CHATS_DB_LOCK:
        row = db.execute(f"SELECT * FROM chats WHERE {where}", (chat_id,)).fetchone()
        if row is None:
            return None
        chat = _row_to_chat(row)
        chat["messages"] = _load_messages(db, chat_id)
        return chat


def get_chat(chat_id: str) -> dict | None:
    try:
        safe = ensure_flat_name(chat_id, "ID диалога")
    except ValueError:
        return None
    return _load_chat(safe, archived=False)


def get_user_chats(login: str) -> list[dict]:
    try:
        safe_login = ensure_flat_name(login, "Логин")
    except ValueError:
        return []
    db = _get_chats_db()
    with _CHATS_DB_LOCK:
        chat_rows = db.execute(
            "SELECT * FROM chats WHERE login = ? AND archived_at IS NULL "
            "ORDER BY updated_at DESC, created_at DESC",
            (safe_login,),
        ).fetchall()
        if not chat_rows:
            return []
        chat_ids = [r["id"] for r in chat_rows]
        placeholders = ",".join("?" * len(chat_ids))
        msg_rows = db.execute(
            f"SELECT * FROM messages WHERE chat_id IN ({placeholders}) "
            "ORDER BY chat_id, seq ASC, created_at ASC",
            chat_ids,
        ).fetchall()
    msgs_by_chat: dict[str, list] = {}
    for m in msg_rows:
        msgs_by_chat.setdefault(m["chat_id"], []).append(_row_to_message(m))
    result = []
    for r in chat_rows:
        chat = _row_to_chat(r)
        chat["messages"] = msgs_by_chat.get(r["id"], [])
        result.append(chat)
    return result


def _insert_message(db, chat_id: str, msg: dict, seq: int):
    db.execute(
        """
        INSERT INTO messages (
            chat_id, mid, seq, role, content, created_at,
            pending, status, is_error, used_docs, rewritten_query, model, mode,
            latency_ms, prompt_tokens, completion_tokens, total_tokens,
            feedback, feedback_reason, feedback_comment, feedback_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            msg.get("mid") or _generate_mid(),
            seq,
            msg["role"],
            msg.get("content", ""),
            msg.get("created_at", _now()),
            int(bool(msg["pending"])) if "pending" in msg else None,
            msg.get("status"),
            int(bool(msg["is_error"])) if "is_error" in msg else None,
            json.dumps(msg["used_docs"], ensure_ascii=False) if msg.get("used_docs") is not None else None,
            msg.get("rewritten_query"),
            msg.get("model"),
            msg.get("mode"),
            msg.get("latency_ms"),
            msg.get("prompt_tokens"),
            msg.get("completion_tokens"),
            msg.get("total_tokens"),
            # Сохраняем оценку ЦЕЛИКОМ: при добавлении нового сообщения save_chat
            # переписывает все сообщения, и без причины/комментария/времени
            # негативная оценка теряла бы детали в админ-логах.
            msg.get("feedback"),
            msg.get("feedback_reason"),
            msg.get("feedback_comment"),
            msg.get("feedback_updated_at"),
        ),
    )


def create_chat(login: str) -> dict:
    safe_login = ensure_flat_name(login, "Логин")
    ts = _now()
    chat_id = f"{safe_login}_{generate_chat_id()}"
    db = _get_chats_db()
    with get_lock(chat_lock_name(chat_id)), _CHATS_DB_LOCK:
        with db:
            db.execute(
                "INSERT INTO chats (id, login, title, created_at, updated_at, model, mode) "
                "VALUES (?, ?, ?, ?, ?, '', '')",
                (chat_id, safe_login, "Новый диалог", ts, ts),
            )
    return {
        "id": chat_id, "login": safe_login, "created_at": ts, "updated_at": ts,
        "title": "Новый диалог", "messages": [], "model": "", "mode": "",
    }


def save_chat(chat: dict) -> None:
    chat_id = chat["id"]
    db = _get_chats_db()
    with get_lock(chat_lock_name(chat_id)), _CHATS_DB_LOCK:
        with db:
            db.execute(
                """
                INSERT INTO chats (id, login, title, created_at, updated_at, archived_at, model, mode)
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title, updated_at = excluded.updated_at,
                    model = excluded.model, mode = excluded.mode
                """,
                (
                    chat_id, chat["login"], chat.get("title", ""),
                    chat.get("created_at", _now()), chat.get("updated_at", _now()),
                    chat.get("model", ""), chat.get("mode", ""),
                ),
            )
            db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            for seq, msg in enumerate(chat.get("messages", [])):
                _insert_message(db, chat_id, msg, seq)


def delete_chat(chat_id: str) -> bool:
    try:
        safe = ensure_flat_name(chat_id, "ID диалога")
    except ValueError:
        return False
    db = _get_chats_db()
    with get_lock(chat_lock_name(safe)), _CHATS_DB_LOCK:
        with db:
            cur = db.execute(
                "UPDATE chats SET archived_at = ? WHERE id = ? AND archived_at IS NULL",
                (_now(), safe),
            )
            return cur.rowcount > 0


def delete_all_chats(login: str) -> None:
    try:
        safe_login = ensure_flat_name(login, "Логин")
    except ValueError:
        return
    db = _get_chats_db()
    with _CHATS_DB_LOCK:
        with db:
            db.execute(
                "UPDATE chats SET archived_at = ? WHERE login = ? AND archived_at IS NULL",
                (_now(), safe_login),
            )


# ── Горячий путь стриминга ───────────────────────────────────────

_ASSISTANT_COL_COERCE = {
    "content": lambda v: v,
    "status": lambda v: v,
    "pending": lambda v: int(bool(v)),
    "is_error": lambda v: int(bool(v)),
    "used_docs": lambda v: json.dumps(v, ensure_ascii=False) if v is not None else None,
    "rewritten_query": lambda v: v,
    "model": lambda v: v,
    "mode": lambda v: v,
    "latency_ms": lambda v: v,
    "prompt_tokens": lambda v: v,
    "completion_tokens": lambda v: v,
    "total_tokens": lambda v: v,
}


def _normalize_message_fields(fields: dict) -> dict:
    return {k: _ASSISTANT_COL_COERCE[k](v) for k, v in fields.items() if k in _ASSISTANT_COL_COERCE}


def update_pending_assistant(chat_id: str, **fields) -> dict | None:
    """Атомарно upsert-ит «ожидающее» сообщение ассистента диалога."""
    db = _get_chats_db()
    msg_fields = _normalize_message_fields(fields)
    chat_runtime = {k: fields[k] for k in ("model", "mode") if fields.get(k)}
    ts = _now()
    with get_lock(chat_lock_name(chat_id)), _CHATS_DB_LOCK:
        with db:
            chat_row = db.execute(
                "SELECT id FROM chats WHERE id = ? AND archived_at IS NULL", (chat_id,)
            ).fetchone()
            if not chat_row:
                return None
            existing = db.execute(
                "SELECT mid FROM messages WHERE chat_id = ? AND pending = 1", (chat_id,)
            ).fetchone()
            if existing:
                mid = existing["mid"]
                if msg_fields:
                    set_sql = ", ".join(f"{c} = ?" for c in msg_fields)
                    db.execute(
                        f"UPDATE messages SET {set_sql} WHERE chat_id = ? AND mid = ?",
                        list(msg_fields.values()) + [chat_id, mid],
                    )
            else:
                mid = _generate_mid()
                seq = db.execute(
                    "SELECT COALESCE(MAX(seq) + 1, 0) FROM messages WHERE chat_id = ?", (chat_id,)
                ).fetchone()[0]
                defaults = {
                    "content": "", "pending": 1, "status": "searching", "is_error": 0,
                    "used_docs": json.dumps([], ensure_ascii=False), "rewritten_query": None,
                    "model": None, "mode": None, "latency_ms": None,
                    "prompt_tokens": None, "completion_tokens": None, "total_tokens": None,
                }
                defaults.update(msg_fields)
                db.execute(
                    """
                    INSERT INTO messages (
                        chat_id, mid, seq, role, content, created_at,
                        pending, status, is_error, used_docs, rewritten_query, model, mode,
                        latency_ms, prompt_tokens, completion_tokens, total_tokens, feedback
                    ) VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        chat_id, mid, seq, defaults["content"], ts,
                        defaults["pending"], defaults["status"], defaults["is_error"],
                        defaults["used_docs"], defaults["rewritten_query"], defaults["model"],
                        defaults["mode"], defaults["latency_ms"], defaults["prompt_tokens"],
                        defaults["completion_tokens"], defaults["total_tokens"],
                    ),
                )
            chat_set = ["updated_at = ?"]
            chat_params: list = [ts]
            for col, val in chat_runtime.items():
                chat_set.append(f"{col} = ?")
                chat_params.append(val)
            chat_params.append(chat_id)
            db.execute(f"UPDATE chats SET {', '.join(chat_set)} WHERE id = ?", chat_params)
            row = db.execute(
                "SELECT * FROM messages WHERE chat_id = ? AND mid = ?", (chat_id, mid)
            ).fetchone()
    return _row_to_message(row) if row else None


def finalize_pending_assistant(chat_id: str, **fields) -> dict | None:
    fields["pending"] = False
    if "status" not in fields:
        fields["status"] = "error" if fields.get("is_error") else "done"
    return update_pending_assistant(chat_id, **fields)


def set_message_feedback(chat_id: str, mid: str, rating: str | None, *, reason: str | None = None, comment: str | None = None) -> dict | None:
    """Оценка ответа ассистента: 'positive' | 'negative' | None (сброс)."""
    if rating is not None and rating not in ("positive", "negative"):
        raise ValueError("Оценка должна быть 'positive', 'negative' или None")
    if rating == "negative":
        if reason not in FEEDBACK_REASONS:
            raise ValueError("Для негативной оценки нужна корректная причина")
        comment = (comment or "").strip()
        if reason == "other":
            if not comment:
                raise ValueError("Для причины 'other' нужен комментарий")
            if len(comment) > 200:
                raise ValueError("Комментарий не длиннее 200 символов")
        else:
            comment = None
    else:
        reason = None
        comment = None
    db = _get_chats_db()
    with get_lock(chat_lock_name(chat_id)), _CHATS_DB_LOCK:
        with db:
            row = db.execute(
                """
                SELECT * FROM messages WHERE chat_id = ? AND mid = ? AND role = 'assistant'
                  AND COALESCE(pending, 0) = 0 AND COALESCE(is_error, 0) = 0
                """,
                (chat_id, mid),
            ).fetchone()
            if not row:
                return None
            db.execute(
                """
                UPDATE messages SET feedback = ?, feedback_reason = ?,
                    feedback_comment = ?, feedback_updated_at = ?
                WHERE chat_id = ? AND mid = ?
                """,
                (rating, reason, comment, _now(), chat_id, mid),
            )
            row = db.execute(
                "SELECT * FROM messages WHERE chat_id = ? AND mid = ?", (chat_id, mid)
            ).fetchone()
    return _row_to_message(row) if row else None


# ── Админ-обзор (логи/история всех диалогов) ─────────────────────


def _rating_where(rating: str) -> str:
    """Фрагмент WHERE для фильтра по оценкам (константы, без интерполяции данных)."""
    if rating == "positive":
        return " AND EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = c.id AND m.feedback = 'positive')"
    if rating == "negative":
        return " AND EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = c.id AND m.feedback = 'negative')"
    if rating == "none":
        return " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = c.id AND m.feedback IN ('positive','negative'))"
    return ""


def get_all_chats_admin(
    include_archived: bool = False, page: int = 1, page_size: int = 50, rating: str = "all"
) -> dict:
    """Страница диалогов (с ≥1 сообщением) для админ-логов.

    Пагинация и фильтры выполняются в SQL (LIMIT/OFFSET + EXISTS), чтобы при
    сотнях тысяч диалогов не грузить всё в память. include_archived=True
    добавляет удалённые (archived=True). rating: all|positive|negative|none.
    Возвращает {items, total, page, page_size}.
    """
    db = _get_chats_db()
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 200))
    offset = (page - 1) * page_size
    arch = "" if include_archived else " AND c.archived_at IS NULL"
    rate = _rating_where(rating if rating in ("positive", "negative", "none") else "all")
    where = "WHERE EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = c.id)" + arch + rate
    with _CHATS_DB_LOCK:
        total = db.execute(f"SELECT COUNT(*) FROM chats c {where}").fetchone()[0]
        rows = db.execute(
            f"""
            SELECT c.id, c.login, c.title, c.created_at, c.updated_at, c.archived_at,
                (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.id) AS msg_count,
                (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.id AND m.feedback='positive') AS pos,
                (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.id AND m.feedback='negative') AS neg,
                (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.id
                    AND m.role='assistant' AND COALESCE(m.is_error,0)=0 AND COALESCE(m.pending,0)=0) AS aok
            FROM chats c {where}
            ORDER BY c.updated_at DESC LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
    items = []
    for r in rows:
        pos = r["pos"] or 0
        neg = r["neg"] or 0
        aok = r["aok"] or 0
        items.append({
            "id": r["id"], "login": r["login"], "title": r["title"] or "",
            "created_at": r["created_at"], "updated_at": r["updated_at"],
            "archived": r["archived_at"] is not None,
            "archived_at": r["archived_at"],
            "message_count": r["msg_count"] or 0,
            "feedback_positive": pos,
            "feedback_negative": neg,
            "feedback_none": max(0, aok - pos - neg),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_admin_stats(include_archived: bool = False) -> dict:
    """Агрегаты по ВСЕМ диалогам (для блока статистики), считаются в SQL."""
    db = _get_chats_db()
    arch = "" if include_archived else " AND c.archived_at IS NULL"
    with _CHATS_DB_LOCK:
        row = db.execute(
            f"""
            SELECT COUNT(DISTINCT m.chat_id) AS dialogs, COUNT(*) AS messages,
                SUM(CASE WHEN m.feedback='positive' THEN 1 ELSE 0 END) AS pos,
                SUM(CASE WHEN m.feedback='negative' THEN 1 ELSE 0 END) AS neg,
                SUM(CASE WHEN m.role='assistant' AND COALESCE(m.is_error,0)=0 AND COALESCE(m.pending,0)=0 THEN 1 ELSE 0 END) AS aok
            FROM messages m JOIN chats c ON c.id = m.chat_id WHERE 1=1 {arch}
            """
        ).fetchone()
        archived = db.execute(
            "SELECT COUNT(*) FROM chats WHERE archived_at IS NOT NULL"
        ).fetchone()[0]
    pos = row["pos"] or 0
    neg = row["neg"] or 0
    aok = row["aok"] or 0
    return {
        "dialogs": row["dialogs"] or 0,
        "messages": row["messages"] or 0,
        "positive": pos,
        "negative": neg,
        "none": max(0, aok - pos - neg),
        "archived": archived,
    }


def get_chat_admin(chat_id: str) -> dict | None:
    """Полный диалог по id — для админ-просмотра (любой пользователь)."""
    try:
        safe = ensure_flat_name(chat_id, "ID диалога")
    except ValueError:
        return None
    return _load_chat(safe, archived=False) or _load_chat(safe, archived=True)
