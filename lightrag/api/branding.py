"""Брендинг WebUI: название приложения и описание на странице входа.

Хранится в WORKING_DIR/branding.json и читается ДИНАМИЧЕСКИ (без рестарта
сервера), поэтому администратор может менять название/описание на лету.
Значения отдаются клиенту в /auth-status и /login и применяются на странице
входа и в шапке."""

import json
import os
from pathlib import Path

BRANDING_FILENAME = "branding.json"
DEFAULT_APP_NAME = "ПростоГраф"
DEFAULT_LOGIN_DESCRIPTION = (
    "Пожалуйста, введите ваш аккаунт и пароль для входа в систему"
)


def _branding_path() -> Path:
    working_dir = os.getenv("WORKING_DIR", "./rag_storage")
    return Path(working_dir) / BRANDING_FILENAME


def get_branding() -> dict:
    """Текущий брендинг с подстановкой значений по умолчанию."""
    data: dict = {}
    path = _branding_path()
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    app_name = (data.get("app_name") or "").strip() or DEFAULT_APP_NAME
    login_description = (data.get("login_description") or "").strip() or DEFAULT_LOGIN_DESCRIPTION
    return {"app_name": app_name, "login_description": login_description}


def set_branding(app_name: str | None = None, login_description: str | None = None) -> dict:
    """Сохраняет брендинг (атомарно). Пустые/None поля сбрасываются на дефолт."""
    current = get_branding()
    if app_name is not None:
        current["app_name"] = (app_name.strip()[:80]) or DEFAULT_APP_NAME
    if login_description is not None:
        current["login_description"] = (login_description.strip()[:300]) or DEFAULT_LOGIN_DESCRIPTION
    path = _branding_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return current
