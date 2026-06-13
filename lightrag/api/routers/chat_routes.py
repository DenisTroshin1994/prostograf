"""Чат-роуты: серверные диалоги, потоковый ответ с ACL по отделу, оценки.

Единая серверная история для всех (admin и user). Ответ генерируется через
rag.aquery_llm в процессе сервера; доступ к документам ограничивается ACL
отдела пользователя (allowed_file_paths). Каждое сообщение хранит источники,
реальные токены, время ответа и переписанный запрос (если был реврайт)."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lightrag.base import QueryParam
from lightrag.api.repos import chats_db
from lightrag.api.utils_api import (
    get_current_user,
    resolve_allowed_file_paths_for_user,
    apply_doc_meta_to_param,
)
from lightrag.api.repos.dept_access import get_dept_prompt
from lightrag.api.global_prompt import get_global_prompt

logger = logging.getLogger("lightrag")


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000, description="Текст вопроса пользователя")
    search_query: Optional[str] = Field(
        default=None,
        max_length=32000,
        description="Запрос для поиска (если применён реврайт); по умолчанию = message",
    )
    mode: Literal["local", "global", "hybrid", "naive", "mix", "bypass"] = Field(default="mix")
    history_turns: int = Field(default=3, ge=0, le=50)
    user_prompt: Optional[str] = Field(default=None, max_length=8000)


class FeedbackRequest(BaseModel):
    rating: Optional[str] = Field(default=None, description="positive | negative | null")
    reason: Optional[str] = None
    comment: Optional[str] = None


def create_chat_routes(rag) -> APIRouter:
    router = APIRouter(prefix="/chats", tags=["chat"])

    def _references_to_docs(result: dict) -> List[str]:
        refs = result.get("data", {}).get("references", []) or []
        docs = []
        for r in refs:
            fp = r.get("file_path") if isinstance(r, dict) else None
            if fp and fp not in docs:
                docs.append(fp)
        return docs

    def _build_usage(metadata: Optional[dict], answer_text: str) -> dict:
        prompt_tokens = (metadata or {}).get("llm_prompt_tokens")
        try:
            completion_tokens = len(rag.tokenizer.encode(answer_text)) if answer_text else 0
        except Exception:
            completion_tokens = 0
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (prompt_tokens or 0) + completion_tokens,
        }

    def _owned_or_admin(chat: dict, user: dict):
        if not chat:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        if chat["login"] != user["login"] and user["role"] != "admin":
            raise HTTPException(status_code=404, detail="Диалог не найден")

    # ── CRUD диалогов ────────────────────────────────────────────
    @router.get("", summary="Мои диалоги")
    async def list_chats(user: dict = Depends(get_current_user)):
        chats = chats_db.get_user_chats(user["login"])
        return [
            {
                "id": c["id"],
                "title": c.get("title", ""),
                "created_at": c.get("created_at", 0),
                "updated_at": c.get("updated_at", 0),
                "message_count": len(c.get("messages", [])),
            }
            for c in chats
        ]

    @router.post("", summary="Создать диалог")
    async def new_chat(user: dict = Depends(get_current_user)):
        chat = chats_db.create_chat(user["login"])
        return {"id": chat["id"], "title": chat["title"]}

    @router.get("/{chat_id}", summary="Диалог целиком")
    async def get_chat_detail(chat_id: str, user: dict = Depends(get_current_user)):
        chat = await asyncio.to_thread(chats_db.get_chat, chat_id)
        _owned_or_admin(chat, user)
        return chat

    @router.delete("/{chat_id}", summary="Удалить диалог")
    async def remove_chat(chat_id: str, user: dict = Depends(get_current_user)):
        chat = await asyncio.to_thread(chats_db.get_chat, chat_id)
        _owned_or_admin(chat, user)
        chats_db.delete_chat(chat_id)
        return {"ok": True}

    @router.delete("", summary="Удалить все мои диалоги")
    async def remove_all_chats(user: dict = Depends(get_current_user)):
        chats_db.delete_all_chats(user["login"])
        return {"ok": True}

    # ── Потоковое сообщение ──────────────────────────────────────
    @router.post("/{chat_id}/message/stream", summary="Отправить сообщение (поток)")
    async def send_message_stream(
        chat_id: str, req: SendMessageRequest, user: dict = Depends(get_current_user)
    ):
        chat = await asyncio.to_thread(chats_db.get_chat, chat_id)
        if not chat or chat["login"] != user["login"]:
            raise HTTPException(status_code=404, detail="Диалог не найден")

        question = req.message.strip()
        search_query = (req.search_query or req.message).strip()
        if not question:
            raise HTTPException(status_code=400, detail="Пустое сообщение")

        # ACL по отделу пользователя.
        allowed = resolve_allowed_file_paths_for_user(user)

        # Приоритет промпта ответа: отдел → клиентский user_prompt → общий.
        # Промпт отдела ЗАМЕНЯЕТ общий (базовый) промпт оператора; клиентский
        # user_prompt (если задан админом в WebUI) тоже переопределяет общий;
        # если ничего более специфичного нет — действует общий промпт оператора.
        # Для admin (отдел 'all') промпта отдела нет.
        dept = user.get("department") or ""
        dept_prompt = get_dept_prompt(dept) if dept and dept != "all" else ""
        global_prompt = get_global_prompt()
        effective_user_prompt = (
            dept_prompt.strip()
            or (req.user_prompt or "").strip()
            or global_prompt.strip()
            or None
        )

        # История для мультиоборотного контекста (последние N пар).
        prior = [m for m in chat.get("messages", []) if not m.get("is_error")]
        if req.history_turns > 0:
            recent = prior[-(req.history_turns * 2):]
        else:
            recent = []
        conversation_history = [
            {"role": m["role"], "content": m.get("content", "")} for m in recent if m.get("content")
        ]

        # Сохранить пользовательское сообщение и завести pending-ассистента.
        chat["messages"].append({"role": "user", "content": question})
        if len([m for m in chat["messages"] if m["role"] == "user"]) == 1:
            chat["title"] = question[:50] + ("…" if len(question) > 50 else "")
        chat["mode"] = req.mode
        await asyncio.to_thread(chats_db.save_chat, chat)
        pending = await asyncio.to_thread(
            chats_db.update_pending_assistant,
            chat_id,
            status="searching",
            mode=req.mode,
            rewritten_query=(search_query if search_query != question else None),
        )
        mid = pending["mid"] if pending else None

        param = QueryParam(
            mode=req.mode,
            stream=True,
            response_type="Multiple Paragraphs",
            conversation_history=conversation_history,
            user_prompt=effective_user_prompt,
        )
        param.allowed_file_paths = allowed
        apply_doc_meta_to_param(param)  # METAINFO + «целиком» документов

        async def event_generator():
            import time

            t0 = time.time()
            full_answer = ""
            used_docs: List[str] = []
            had_error = False
            usage: dict = {}
            try:
                result = await rag.aquery_llm(search_query, param=param)
                metadata = result.get("metadata", {})
                used_docs = _references_to_docs(result)
                llm_response = result.get("llm_response", {})

                yield f"{json.dumps({'meta': {'mid': mid, 'chat_id': chat_id}}, ensure_ascii=False)}\n"
                yield f"{json.dumps({'references': [{'file_path': d} for d in used_docs]}, ensure_ascii=False)}\n"
                await asyncio.to_thread(
                    chats_db.update_pending_assistant, chat_id,
                    used_docs=used_docs, status="generating", mode=req.mode,
                )

                if llm_response.get("is_streaming"):
                    stream = llm_response.get("response_iterator")
                    last_flush = t0
                    async for chunk in stream or []:
                        if not chunk:
                            continue
                        full_answer += chunk
                        yield f"{json.dumps({'response': chunk}, ensure_ascii=False)}\n"
                        now = time.time()
                        if now - last_flush >= 1.0:
                            last_flush = now
                            await asyncio.to_thread(
                                chats_db.update_pending_assistant, chat_id,
                                content=full_answer, used_docs=used_docs, status="generating",
                            )
                else:
                    full_answer = llm_response.get("content", "") or "По запросу не найдено релевантного контекста."
                    yield f"{json.dumps({'response': full_answer}, ensure_ascii=False)}\n"

                usage = _build_usage(metadata, full_answer)
                yield f"{json.dumps({'usage': usage}, ensure_ascii=False)}\n"
            except Exception as e:
                logger.error(f"Ошибка генерации в чате {chat_id}: {e}", exc_info=True)
                had_error = True
                if not full_answer:
                    full_answer = "Произошла ошибка при обработке запроса. Попробуйте переформулировать."
                yield f"{json.dumps({'error': full_answer}, ensure_ascii=False)}\n"
            finally:
                # Клиент мог отключиться (перезагрузка страницы / обрыв) до того,
                # как пришёл хоть один фрагмент ответа. Тогда генерация прервана —
                # помечаем сообщение ошибкой с понятным текстом, иначе при
                # перезагрузке у пустого ответа вечно крутится индикатор.
                interrupted = not had_error and not full_answer.strip()
                if interrupted:
                    full_answer = (
                        "⚠ Генерация была прервана (соединение закрыто). "
                        "Попробуйте задать вопрос снова."
                    )
                latency_ms = int((time.time() - t0) * 1000)
                await asyncio.to_thread(
                    chats_db.finalize_pending_assistant,
                    chat_id,
                    content=full_answer,
                    used_docs=used_docs,
                    rewritten_query=(search_query if search_query != question else None),
                    mode=req.mode,
                    latency_ms=latency_ms,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    is_error=had_error or interrupted,
                )
                yield f"{json.dumps({'done': {'latency_ms': latency_ms, 'mid': mid}}, ensure_ascii=False)}\n"

        return StreamingResponse(
            event_generator(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Оценка сообщения ─────────────────────────────────────────
    @router.put("/{chat_id}/messages/{mid}/feedback", summary="Оценка ответа")
    async def set_feedback(chat_id: str, mid: str, req: FeedbackRequest, user: dict = Depends(get_current_user)):
        chat = await asyncio.to_thread(chats_db.get_chat, chat_id)
        if not chat or chat["login"] != user["login"]:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        try:
            result = await asyncio.to_thread(
                chats_db.set_message_feedback, chat_id, mid, req.rating,
                reason=req.reason, comment=req.comment,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if result is None:
            raise HTTPException(status_code=404, detail="Сообщение не найдено или не подлежит оценке")
        return {
            "ok": True,
            "rating": result.get("feedback"),
            "reason": result.get("feedback_reason"),
            "comment": result.get("feedback_comment"),
        }

    return router
