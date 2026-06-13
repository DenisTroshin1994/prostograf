"""In-memory троттлинг входа (защита /login от перебора).

Скользящее окно по ключу (IP|логин): ≥ MAX_FAILS неудач за WINDOW_SEC →
временная блокировка, пока старые попытки не выйдут из окна. Состояние в
памяти процесса (однопроцессный uvicorn), сбрасывается при рестарте.

Админ-операции (создание/смена пароля/удаление пользователя) сбрасывают
блокировку по этому логину через clear_login(), чтобы пересоздание учётки или
смена пароля сразу позволяли войти, не дожидаясь окончания окна. Это отдельный
модуль (а не функции в lightrag_server), чтобы и /login, и admin-роуты могли им
пользоваться без циклических импортов."""

import os
import threading
import time

MAX_FAILS = 5
WINDOW_SEC = 300

# X-Forwarded-For доверяем только за обратным прокси (он его выставляет).
# Напрямую заголовок подделывается клиентом, и брутфорс одной учётки обходил бы
# лимит сменой фейкового IP на каждый запрос. По умолчанию выкл: берём реальный
# адрес пира. Включить за доверенным прокси: TRUST_FORWARDED_FOR=true.
_TRUST_FORWARDED = (os.getenv("TRUST_FORWARDED_FOR", "false").strip().lower()
                    in ("true", "1", "yes"))

_FAILURES: dict[str, list[float]] = {}
_LOCK = threading.Lock()


def client_ip(request) -> str:
    if _TRUST_FORWARDED:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def make_key(ip: str, login: str) -> str:
    return f"{ip}|{login}"


def is_locked(key: str) -> bool:
    now = time.time()
    with _LOCK:
        fails = [t for t in _FAILURES.get(key, []) if now - t < WINDOW_SEC]
        _FAILURES[key] = fails
        return len(fails) >= MAX_FAILS


def record_failure(key: str) -> None:
    with _LOCK:
        _FAILURES.setdefault(key, []).append(time.time())


def reset(key: str) -> None:
    with _LOCK:
        _FAILURES.pop(key, None)


def clear_login(login: str) -> None:
    """Снять блокировку по данному логину для всех IP (после админ-операции)."""
    if not login:
        return
    suffix = f"|{login}"
    with _LOCK:
        for k in [k for k in _FAILURES if k.endswith(suffix)]:
            _FAILURES.pop(k, None)
