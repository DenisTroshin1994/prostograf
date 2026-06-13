"""Админ-роуты: управление пользователями, отделами, доступом отделов к
документам (ACL) и просмотр истории диалогов с оценками (логи).

Весь роутер защищён ролью admin (см. регистрацию в lightrag_server с
зависимостью require_admin)."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lightrag.api.repos import chats_db, dept_access, doc_meta, users_db
from lightrag.api import login_throttle
from lightrag.api import branding as branding_repo
from lightrag.api import global_prompt as global_prompt_repo


class CreateUserRequest(BaseModel):
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)
    role: str = Field(default="user")
    department: str = Field(default="")
    display_name: str = Field(default="")


class UpdateUserRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    display_name: Optional[str] = None


class DepartmentRequest(BaseModel):
    name: str = Field(min_length=1)


class RenameDepartmentRequest(BaseModel):
    new_name: str = Field(min_length=1)


class AccessRequest(BaseModel):
    files: List[str] = Field(default_factory=list)


class PromptRequest(BaseModel):
    prompt: str = Field(default="")


class DocMetaRequest(BaseModel):
    file_path: str = Field(min_length=1)
    metainfo: Optional[str] = None
    full: Optional[bool] = None


class BrandingRequest(BaseModel):
    app_name: Optional[str] = Field(default=None, max_length=80)
    login_description: Optional[str] = Field(default=None, max_length=300)


def create_admin_routes() -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    # ── Пользователи ─────────────────────────────────────────────
    @router.get("/users", summary="Список пользователей (с пагинацией)")
    async def list_users(page: int = 1, page_size: int = 50, search: str = ""):
        return users_db.read_users_paginated(page=page, page_size=page_size, search=search)

    @router.post("/users", summary="Создать пользователя")
    async def add_user(req: CreateUserRequest):
        try:
            result = users_db.create_user(
                login=req.login,
                password=req.password,
                role=req.role,
                department=req.department,
                display_name=req.display_name,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Сброс блокировки входа: иначе после удаления и пересоздания учётки
        # с тем же логином прежние неудачные попытки держали бы её под замком.
        login_throttle.clear_login(req.login.strip())
        return result

    @router.put("/users/{login}", summary="Изменить пользователя")
    async def edit_user(login: str, req: UpdateUserRequest):
        if not users_db.find_user(login):
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        # Нельзя разжаловать последнего администратора.
        if req.role == "user":
            current = users_db.find_user(login)
            if current and current.get("role") == "admin" and _admin_count() <= 1:
                raise HTTPException(status_code=400, detail="Нельзя снять роль с последнего администратора")
        try:
            updated = users_db.update_user(
                login,
                password=req.password,
                role=req.role,
                department=req.department,
                display_name=req.display_name,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Смена пароля/прав — снять возможную блокировку входа по этому логину.
        login_throttle.clear_login(login)
        return {"login": updated["login"], "role": updated["role"], "department": updated["department"], "display_name": updated["display_name"]}

    @router.delete("/users/{login}", summary="Удалить пользователя")
    async def remove_user(login: str):
        user = users_db.find_user(login)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if user.get("role") == "admin" and _admin_count() <= 1:
            raise HTTPException(status_code=400, detail="Нельзя удалить последнего администратора")
        users_db.delete_user(login)
        login_throttle.clear_login(login)
        return {"ok": True}

    # ── Отделы ───────────────────────────────────────────────────
    @router.get("/departments", summary="Список отделов")
    async def list_departments():
        result = []
        for name in dept_access.get_departments():
            result.append({"name": name, "doc_count": len(dept_access.get_dept_access(name))})
        return result

    @router.post("/departments", summary="Создать отдел")
    async def add_department(req: DepartmentRequest):
        try:
            dept_access.create_department(req.name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "name": req.name}

    @router.put("/departments/{name}", summary="Переименовать отдел")
    async def rename_department(name: str, req: RenameDepartmentRequest):
        try:
            dept_access.rename_department(name, req.new_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Переназначить пользователей старого отдела на новый одним UPDATE
        # (без загрузки всех пользователей в память — важно при их большом числе).
        users_db.reassign_department(name, req.new_name)
        return {"ok": True, "name": req.new_name}

    @router.delete("/departments/{name}", summary="Удалить отдел")
    async def delete_department(name: str):
        try:
            dept_access.delete_department(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Сбросить отдел у пользователей, которые в нём состояли (одним UPDATE).
        users_db.reassign_department(name, "")
        return {"ok": True}

    @router.get("/departments/{name}/access", summary="Доступ отдела к документам")
    async def get_access(name: str):
        if name not in dept_access.get_departments():
            raise HTTPException(status_code=404, detail="Отдел не найден")
        return {"files": dept_access.get_dept_access(name)}

    @router.put("/departments/{name}/access", summary="Задать доступ отдела к документам")
    async def set_access(name: str, req: AccessRequest):
        if name not in dept_access.get_departments():
            raise HTTPException(status_code=404, detail="Отдел не найден")
        files = dept_access.set_dept_access(name, req.files)
        return {"files": files}

    # ── Промпт ответа отдела ─────────────────────────────────────
    @router.get("/departments/{name}/prompt", summary="Промпт ответа отдела")
    async def get_dept_prompt(name: str):
        if name not in dept_access.get_departments():
            raise HTTPException(status_code=404, detail="Отдел не найден")
        return {"prompt": dept_access.get_dept_prompt(name)}

    @router.put("/departments/{name}/prompt", summary="Задать промпт ответа отдела")
    async def set_dept_prompt(name: str, req: PromptRequest):
        if name not in dept_access.get_departments():
            raise HTTPException(status_code=404, detail="Отдел не найден")
        try:
            prompt = dept_access.set_dept_prompt(name, req.prompt)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"prompt": prompt}

    # ── METAINFO / «целиком» документов ──────────────────────────
    @router.get("/doc-meta", summary="Метаданные документов (METAINFO + «целиком»)")
    async def get_doc_meta():
        return doc_meta.get_all()

    @router.put("/doc-meta", summary="Задать METAINFO / флаг «целиком» документа")
    async def set_doc_meta(req: DocMetaRequest):
        try:
            entry = doc_meta.set_meta(req.file_path, metainfo=req.metainfo, full=req.full)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"file_path": req.file_path, **entry}

    # ── Оформление (брендинг) ────────────────────────────────────
    @router.get("/branding", summary="Название и описание (страница входа)")
    async def get_branding():
        return branding_repo.get_branding()

    @router.put("/branding", summary="Задать название/описание")
    async def set_branding(req: BrandingRequest):
        return branding_repo.set_branding(
            app_name=req.app_name, login_description=req.login_description
        )

    # ── Общий (базовый) промпт оператора ─────────────────────────
    @router.get("/global_prompt", summary="Общий промпт оператора")
    async def get_global_prompt():
        return {
            "prompt": global_prompt_repo.get_global_prompt(),
            "default": global_prompt_repo.DEFAULT_GLOBAL_PROMPT,
        }

    @router.put("/global_prompt", summary="Задать общий промпт оператора")
    async def set_global_prompt(req: PromptRequest):
        return {"prompt": global_prompt_repo.set_global_prompt(req.prompt)}

    # ── Логи диалогов / оценки ──────────────────────────────────
    @router.get("/chats", summary="Диалоги (логи) с оценками — пагинация")
    async def all_chats(
        include_archived: bool = False, page: int = 1, page_size: int = 50, rating: str = "all"
    ):
        return chats_db.get_all_chats_admin(
            include_archived=include_archived, page=page, page_size=page_size, rating=rating
        )

    # Должен идти ДО "/chats/{chat_id}", иначе FastAPI примет "stats" за chat_id.
    @router.get("/chats/stats", summary="Сводная статистика по диалогам")
    async def chats_stats(include_archived: bool = False):
        return chats_db.get_admin_stats(include_archived=include_archived)

    @router.get("/chats/{chat_id}", summary="Полный диалог по id")
    async def chat_detail(chat_id: str):
        chat = chats_db.get_chat_admin(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        return chat

    return router


def _admin_count() -> int:
    return sum(1 for u in users_db.read_users() if u.get("role") == "admin")
