"""Роуты текущего пользователя: просмотр документов, доступных его отделу
(read-only). Доступ к содержимому строго ограничен ACL отдела."""

from fastapi import APIRouter, Depends, HTTPException

from lightrag.api.utils_api import get_current_user, resolve_allowed_file_paths_for_user


def create_me_routes(rag) -> APIRouter:
    router = APIRouter(prefix="/me", tags=["me"])

    @router.get("/docs", summary="Документы, доступные моему отделу")
    async def my_docs(user: dict = Depends(get_current_user)):
        allowed = resolve_allowed_file_paths_for_user(user)
        items = []
        if allowed is None:
            # Полный доступ (admin): все обработанные документы.
            from lightrag.base import DocStatus

            docs = await rag.doc_status.get_docs_by_status(DocStatus.PROCESSED)
            for d in docs.values():
                items.append({
                    "file_path": d.file_path,
                    "content_summary": d.content_summary,
                    "status": getattr(d.status, "value", str(d.status)),
                })
        else:
            for fp in sorted(allowed):
                d = await rag.doc_status.get_doc_by_file_path(fp)
                summary, status = "", ""
                if isinstance(d, dict):
                    summary = d.get("content_summary", "") or ""
                    st = d.get("status")
                    status = getattr(st, "value", str(st)) if st else ""
                items.append({"file_path": fp, "content_summary": summary, "status": status})
        items.sort(key=lambda x: x["file_path"])
        return items

    @router.get("/docs/content", summary="Содержимое доступного документа")
    async def my_doc_content(file_path: str, user: dict = Depends(get_current_user)):
        allowed = resolve_allowed_file_paths_for_user(user)
        if allowed is not None and file_path not in allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к документу")
        res = await rag.doc_status.get_doc_by_file_basename(file_path)
        if not res:
            raise HTTPException(status_code=404, detail="Документ не найден")
        doc_id, _data = res
        full = await rag.full_docs.get_by_id(doc_id)
        content = (full or {}).get("content", "") if isinstance(full, dict) else ""
        return {"file_path": file_path, "content": content}

    return router
