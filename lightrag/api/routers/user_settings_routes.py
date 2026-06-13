"""Маршруты настроек моделей (OpenRouter / DeepSeek / эмбеддинги) для WebUI.

Сохраняют настройки в томе данных и перезапускают сервер, чтобы они
применились. Пересборка образа не требуется.
"""

import asyncio
import os
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.utils import logger

from ..config import global_args
from ..provider_test import run_provider_tests
from ..user_llm_settings import (
    ALL_PROVIDERS,
    CUSTOM_HOST_PROVIDERS,
    DEEPSEEK_HOST,
    DEFAULT_PROVIDER_HOSTS,
    DEFAULT_RERANK_BINDING,
    DEFAULT_RERANK_MODEL,
    LOCAL_PROVIDERS,
    OPENAI_HOST,
    OPENROUTER_HOST,
    PROVIDER_HOSTS,
    RERANK_BINDINGS,
    load_user_llm_settings,
    save_user_llm_settings,
)
from ..utils_api import get_combined_auth_dependency

router = APIRouter(prefix="/user_llm_settings", tags=["user_llm_settings"])


class ProviderCreds(BaseModel):
    api_key: str = ""
    model: str = ""
    # Используется только провайдерами с вводимым адресом (openai_compatible /
    # ollama / lmstudio); для openrouter/deepseek/openai игнорируется (host фиксирован).
    host: str = ""


class EmbeddingCreds(BaseModel):
    api_key: str = ""
    model: str = ""
    host: str = OPENROUTER_HOST
    dim: Optional[int] = Field(default=None, gt=0)


class RerankCreds(BaseModel):
    enabled: bool = False
    binding: str = DEFAULT_RERANK_BINDING
    model: str = ""
    host: str = ""
    api_key: str = ""


class ChunkSettings(BaseModel):
    size: int = Field(default=1200, gt=0)
    overlap: int = Field(default=100, ge=0)


class UserLLMSettingsPayload(BaseModel):
    """Тело запроса на сохранение. Пустой api_key означает «оставить прежний»."""

    provider: Literal[
        "openrouter", "deepseek", "openai", "openai_compatible", "ollama", "lmstudio"
    ] = "openrouter"
    openrouter: ProviderCreds = ProviderCreds()
    deepseek: ProviderCreds = ProviderCreds()
    openai: ProviderCreds = ProviderCreds()
    openai_compatible: ProviderCreds = ProviderCreds()
    ollama: ProviderCreds = ProviderCreds()
    lmstudio: ProviderCreds = ProviderCreds()
    embedding: EmbeddingCreds = EmbeddingCreds()
    rerank: RerankCreds = RerankCreds()
    chunk: ChunkSettings = ChunkSettings()


def _merge_section(new: dict, old: dict) -> dict:
    """Подставляет сохранённый api_key, если новый не указан."""
    merged = dict(new)
    if not (merged.get("api_key") or "").strip():
        merged["api_key"] = (old or {}).get("api_key", "")
    return merged


def create_user_settings_routes(api_key: Optional[str] = None):
    # Эти роуты подключаются в lightrag_server с router-level зависимостью
    # require_admin, поэтому доступ к настройкам моделей уже ограничен админом.
    combined_auth = get_combined_auth_dependency(api_key)

    @router.get(
        "",
        dependencies=[Depends(combined_auth)],
        summary="Получить настройки моделей",
    )
    async def get_user_llm_settings():
        """Текущие сохранённые настройки (ключи не возвращаются, только флаг наличия)."""
        saved = load_user_llm_settings() or {}

        def section(name: str, defaults: dict) -> dict:
            sec = saved.get(name) or {}
            result = {
                key: sec.get(key, default) for key, default in defaults.items()
            }
            result["has_key"] = bool((sec.get("api_key") or "").strip())
            return result

        rerank_enabled_effective = (
            getattr(global_args, "rerank_binding", "null") or "null"
        ).lower() != "null"

        return {
            "provider": saved.get("provider", "openrouter"),
            "openrouter": section("openrouter", {"model": "", "host": ""}),
            "deepseek": section("deepseek", {"model": "deepseek-chat", "host": ""}),
            "openai": section("openai", {"model": "gpt-4o-mini", "host": ""}),
            "openai_compatible": section(
                "openai_compatible", {"model": "", "host": ""}
            ),
            "ollama": section(
                "ollama",
                {"model": "", "host": DEFAULT_PROVIDER_HOSTS["ollama"]},
            ),
            "lmstudio": section(
                "lmstudio",
                {"model": "", "host": DEFAULT_PROVIDER_HOSTS["lmstudio"]},
            ),
            "embedding": section(
                "embedding",
                {"model": "", "host": OPENROUTER_HOST, "dim": None},
            ),
            "rerank": section(
                "rerank",
                {
                    "enabled": False,
                    "binding": DEFAULT_RERANK_BINDING,
                    "model": DEFAULT_RERANK_MODEL,
                    "host": "",
                },
            ),
            "chunk": (saved.get("chunk") or {}) or {
                "size": getattr(global_args, "chunk_size", 1200),
                "overlap": getattr(global_args, "chunk_overlap_size", 100),
            },
            "rerank_bindings": sorted(RERANK_BINDINGS),
            "providers": ALL_PROVIDERS,
            "fixed_host_providers": sorted(PROVIDER_HOSTS.keys()),
            "custom_host_providers": sorted(CUSTOM_HOST_PROVIDERS),
            "local_providers": sorted(LOCAL_PROVIDERS),
            "hosts": {
                "openrouter": OPENROUTER_HOST,
                "deepseek": DEEPSEEK_HOST,
                "openai": OPENAI_HOST,
            },
            "default_hosts": DEFAULT_PROVIDER_HOSTS,
            "effective": {
                "llm_binding": global_args.llm_binding,
                "llm_binding_host": global_args.llm_binding_host,
                "llm_model": global_args.llm_model,
                "embedding_binding_host": global_args.embedding_binding_host,
                "embedding_model": global_args.embedding_model,
                "embedding_dim": global_args.embedding_dim,
                "rerank_enabled": rerank_enabled_effective,
                "rerank_binding": getattr(global_args, "rerank_binding", "null"),
                "rerank_model": getattr(global_args, "rerank_model", None),
                "chunk_size": getattr(global_args, "chunk_size", 1200),
                "chunk_overlap_size": getattr(global_args, "chunk_overlap_size", 100),
            },
        }

    @router.post(
        "",
        dependencies=[Depends(combined_auth)],
        summary="Сохранить настройки моделей и перезапустить сервер",
    )
    async def update_user_llm_settings(payload: UserLLMSettingsPayload):
        """Сохраняет настройки и перезапускает сервер для их применения."""
        old = load_user_llm_settings() or {}

        merged = {
            "provider": payload.provider,
            "embedding": _merge_section(
                payload.embedding.model_dump(), old.get("embedding") or {}
            ),
            "rerank": _merge_section(
                payload.rerank.model_dump(), old.get("rerank") or {}
            ),
            "chunk": payload.chunk.model_dump(),
        }
        for prov in ALL_PROVIDERS:
            merged[prov] = _merge_section(
                getattr(payload, prov).model_dump(), old.get(prov) or {}
            )

        active = merged[payload.provider]
        if not (active.get("model") or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Укажите модель для выбранного провайдера.",
            )
        # Провайдеры с вводимым адресом (openai_compatible/ollama/lmstudio):
        # нужен host (для локальных движков подставляем дефолт, если пусто).
        if payload.provider in CUSTOM_HOST_PROVIDERS:
            host = (active.get("host") or "").strip()
            if not host:
                host = DEFAULT_PROVIDER_HOSTS.get(payload.provider, "")
                active["host"] = host
            if not host:
                raise HTTPException(
                    status_code=400,
                    detail="Укажите адрес сервиса (host) для выбранного провайдера.",
                )
            if not host.lower().startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=400,
                    detail="Адрес сервиса должен начинаться с http:// или https://",
                )
        # Локальные движки (Ollama/LM Studio) ключ не требуют; остальным — нужен.
        if payload.provider not in LOCAL_PROVIDERS and not (
            active.get("api_key") or ""
        ).strip():
            raise HTTPException(
                status_code=400,
                detail="Укажите API-ключ для выбранного провайдера.",
            )

        emb = merged["embedding"]
        emb_filled = any(
            (emb.get(field) or "").strip() if isinstance(emb.get(field), str) else False
            for field in ("model", "api_key")
        )
        if emb_filled:
            if not (emb.get("model") or "").strip():
                raise HTTPException(
                    status_code=400, detail="Укажите модель эмбеддингов."
                )
            if not (emb.get("api_key") or "").strip():
                raise HTTPException(
                    status_code=400, detail="Укажите API-ключ эмбеддингов."
                )
            if not (emb.get("host") or "").strip():
                emb["host"] = OPENROUTER_HOST

        # Реранкер: при включении нужны модель и ключ (ключ может быть сохранён ранее).
        rer = merged["rerank"]
        if (rer.get("binding") or "").strip().lower() not in RERANK_BINDINGS:
            rer["binding"] = DEFAULT_RERANK_BINDING
        if rer.get("enabled"):
            if not (rer.get("model") or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail="Укажите модель реранкера или выключите его.",
                )
            if not (rer.get("api_key") or "").strip():
                raise HTTPException(
                    status_code=400,
                    detail="Укажите API-ключ реранкера или выключите его.",
                )
            host = (rer.get("host") or "").strip()
            if host and not host.lower().startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=400,
                    detail="Адрес сервиса реранкера должен начинаться с http:// или https://",
                )

        # Чанкинг: перекрытие обязано быть меньше размера чанка, иначе шаг
        # чанкера (size - overlap) станет 0/отрицательным → краш индексации или
        # потеря содержимого (документ режется на ноль фрагментов).
        ch = merged["chunk"]
        if int(ch.get("overlap", 0)) >= int(ch.get("size", 1)):
            raise HTTPException(
                status_code=400,
                detail="Перекрытие чанка должно быть меньше его размера.",
            )

        try:
            save_user_llm_settings(merged)
        except OSError as e:
            logger.error(f"Не удалось сохранить настройки моделей: {e}")
            raise HTTPException(
                status_code=500, detail="Не удалось сохранить файл настроек."
            )

        async def _restart():
            await asyncio.sleep(1.0)
            logger.warning(
                "Настройки моделей сохранены через WebUI — перезапуск сервера"
            )
            os._exit(1)

        asyncio.get_running_loop().create_task(_restart())

        return {
            "status": "restarting",
            "message": "Настройки сохранены. Сервер перезапускается, "
            "это займёт около 10–20 секунд.",
        }

    @router.post(
        "/test",
        dependencies=[Depends(combined_auth)],
        summary="Проверить доступность настроенных провайдеров",
    )
    async def test_user_llm_settings():
        """Пингует все настроенные провайдеры (LLM/эмбеддинги/реранкер), у которых
        есть ключ или локальный адрес. Возвращает по каждому: код ответа, время в мс
        и статус. LLM проверяется через /models (без расхода токенов), эмбеддинги —
        пробным /embeddings, реранкер — пробным /rerank (расход ничтожен). Адреса
        берутся только из сохранённой конфигурации, тело запроса не принимается
        (защита от SSRF)."""
        results = await run_provider_tests()
        ok = sum(1 for r in results if r.get("ok"))
        return {"results": results, "total": len(results), "ok": ok}

    return router
