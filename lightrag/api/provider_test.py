"""Проверка доступности настроенных провайдеров (LLM / эмбеддинги / реранкер).

Лёгкий сетевой пинг: для LLM — ``GET {host}/models`` (без расхода токенов:
проверяет доступность хоста и валидность ключа); для эмбеддингов — реальный
``POST {host}/embeddings`` на слове «ping» (подтверждает, что embedding-модель
работает; расход ничтожен); для реранкера — минимальный ``POST`` на двух коротких
документах. По каждому провайдеру возвращаются код ответа, время в мс и краткое
объяснение ошибки.

Адреса берутся ТОЛЬКО из сохранённой/эффективной конфигурации сервера, а не из
тела запроса — это исключает SSRF (нельзя заставить сервер сходить на произвольный
хост).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from .config import global_args
from .user_llm_settings import (
    ALL_PROVIDERS,
    LOCAL_PROVIDERS,
    PROVIDER_LABELS,
    RERANK_DEFAULT_HOSTS,
    load_user_llm_settings,
    resolve_provider_host,
)

# Таймауты: короткое подключение (быстро ловим недоступный хост), общий — с
# запасом на холодный старт локальных движков (Ollama/LM Studio).
_TIMEOUT = httpx.Timeout(15.0, connect=6.0)


def _collect_targets() -> list[dict[str, Any]]:
    """Список целей для проверки из сохранённых настроек + эффективного окружения.

    Дедупликация по (тип, host, модель): активная конфигурация из .env не
    задвоит уже добавленного из файла настроек провайдера.
    """
    saved = load_user_llm_settings() or {}
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(
        kind: str, label: str, host: Any, model: Any, api_key: Any, **extra: Any
    ) -> None:
        host = (host or "").strip()
        model = (model or "").strip()
        if not host or not model:
            return
        dedup_key = (kind, host.rstrip("/"), model)
        if dedup_key in seen:
            return
        seen.add(dedup_key)
        targets.append(
            {
                "kind": kind,
                "label": label,
                "host": host,
                "model": model,
                "api_key": (api_key or "").strip(),
                **extra,
            }
        )

    # LLM-провайдеры из сохранённых настроек: тестируем все, у кого есть ключ
    # (или локальный адрес) — в т.ч. неактивные, чтобы проверить перед переключением.
    for prov in ALL_PROVIDERS:
        creds = saved.get(prov)
        if not isinstance(creds, dict):
            continue
        host = resolve_provider_host(prov, creds)
        model = creds.get("model")
        api_key = creds.get("api_key")
        is_local = prov in LOCAL_PROVIDERS
        if model and host and (api_key or is_local):
            add(
                "chat",
                f"LLM · {PROVIDER_LABELS.get(prov, prov)}",
                host,
                model,
                api_key or "local",
            )

    # Активная конфигурация LLM из окружения (покрывает деплой только через .env).
    add(
        "chat",
        "LLM · активная конфигурация",
        getattr(global_args, "llm_binding_host", None),
        getattr(global_args, "llm_model", None),
        getattr(global_args, "llm_binding_api_key", None),
    )

    # Эмбеддинги: сохранённые, иначе эффективные из окружения.
    emb = saved.get("embedding") or {}
    add(
        "embedding",
        "Эмбеддинги",
        emb.get("host") or getattr(global_args, "embedding_binding_host", None),
        emb.get("model") or getattr(global_args, "embedding_model", None),
        emb.get("api_key") or getattr(global_args, "embedding_binding_api_key", None),
    )

    # Реранкер: тестируем ТОЛЬКО если задан ключ. В ядре дефолтный rerank_binding
    # = 'cohere' (не 'null'), поэтому «эффективно включён» — ненадёжный признак:
    # на чистом деплое без ключа cohere/jina/aliyun вернут 401 и реранкер,
    # которого никто не настраивал, показался бы «провалившимся». Наличие ключа —
    # точный признак того, что админ реально сконфигурировал реранкер.
    rer = saved.get("rerank") or {}
    eff_binding = getattr(global_args, "rerank_binding", None) or "null"
    eff_enabled = eff_binding.lower() != "null"
    rer_binding = (
        rer.get("binding") or (eff_binding if eff_enabled else "cohere")
    ).strip().lower()
    rer_host = (
        (rer.get("host") or "").strip()
        or getattr(global_args, "rerank_binding_host", None)
        or RERANK_DEFAULT_HOSTS.get(rer_binding, "")
    )
    rer_model = rer.get("model") or getattr(global_args, "rerank_model", None)
    rer_key = rer.get("api_key") or getattr(global_args, "rerank_binding_api_key", None)
    if rer_host and rer_model and (rer_key or "").strip():
        add(
            "rerank",
            f"Реранкер · {rer_binding}",
            rer_host,
            rer_model,
            rer_key,
            binding=rer_binding,
        )

    return targets


def _explain_status(status_code: int, body: str) -> str:
    snippet = (body or "").strip().replace("\n", " ")
    if len(snippet) > 180:
        snippet = snippet[:180] + "…"
    if status_code in (401, 403):
        return f"Ошибка авторизации ({status_code}): проверьте API-ключ."
    if status_code == 404:
        return "Не найдено (404): проверьте адрес сервиса или модель."
    if status_code == 429:
        return "Слишком много запросов (429): достигнут лимит провайдера."
    if 500 <= status_code < 600:
        return f"Ошибка на стороне сервиса (HTTP {status_code})."
    return f"HTTP {status_code}" + (f": {snippet}" if snippet else "")


def _model_in_catalog(payload: Any, model: str) -> Optional[bool]:
    """Найдена ли модель в каталоге /models. None — определить не удалось."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    ids = {str(item.get("id")) for item in data if isinstance(item, dict)}
    if not ids:
        return None
    return model in ids


def _rerank_body(target: dict[str, Any]) -> dict[str, Any]:
    if target.get("binding") == "aliyun":
        return {
            "model": target["model"],
            "input": {"query": "ping", "documents": ["a", "b"]},
            "parameters": {"top_n": 1},
        }
    return {"model": target["model"], "query": "ping", "documents": ["a", "b"], "top_n": 1}


async def _probe(client: httpx.AsyncClient, target: dict[str, Any]) -> dict[str, Any]:
    kind = target["kind"]
    host = target["host"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if target.get("api_key"):
        headers["Authorization"] = f"Bearer {target['api_key']}"
    base = {
        "kind": kind,
        "label": target["label"],
        "host": target["host"],
        "model": target["model"],
    }
    t0 = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - t0) * 1000)

    try:
        if kind == "chat":
            # LLM: запрос каталога моделей — без расхода токенов.
            resp = await client.get(host + "/models", headers=headers)
        elif kind == "embedding":
            # Эмбеддинги: реальный пробный вектор — подтверждает, что модель
            # действительно работает (а не только что хост/ключ валидны). Это
            # дешёвый вызов и устраняет ложное «модель не в каталоге»: каталог
            # /models у многих провайдеров не содержит embedding-моделей.
            resp = await client.post(
                host + "/embeddings",
                headers=headers,
                json={"model": target["model"], "input": "ping"},
            )
        else:
            # Реранкер: host — это полный URL эндпоинта /rerank, не дописываем путь.
            resp = await client.post(
                target["host"], headers=headers, json=_rerank_body(target)
            )
        latency = elapsed()
        ok = 200 <= resp.status_code < 300
        out = {
            **base,
            "ok": ok,
            "status_code": resp.status_code,
            "latency_ms": latency,
            "error": None,
            "warning": None,
        }
        if not ok:
            out["error"] = _explain_status(resp.status_code, resp.text)
        elif kind == "chat":
            # Каталог /models надёжно перечисляет chat-модели. Для эмбеддингов
            # эту проверку НЕ делаем: провайдеры часто не отдают embedding-модели
            # в /models, и ложное «модель не найдена» могло бы подтолкнуть к смене
            # модели эмбеддингов — а это дорогая переиндексация всех документов.
            try:
                present = _model_in_catalog(resp.json(), target["model"])
            except Exception:
                present = None
            if present is False:
                out["warning"] = (
                    f"Подключение успешно, но модель «{target['model']}» "
                    "не найдена в каталоге провайдера."
                )
        return out
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout):
        return {
            **base,
            "ok": False,
            "status_code": None,
            "latency_ms": elapsed(),
            "error": "Таймаут: сервис не ответил вовремя.",
            "warning": None,
        }
    except httpx.ConnectError:
        return {
            **base,
            "ok": False,
            "status_code": None,
            "latency_ms": elapsed(),
            "error": f"Не удалось подключиться к {base['host']} (хост недоступен).",
            "warning": None,
        }
    except Exception as e:  # noqa: BLE001 — любая иная сетевая/парс-ошибка → в отчёт
        return {
            **base,
            "ok": False,
            "status_code": None,
            "latency_ms": elapsed(),
            "error": str(e)[:200] or "Неизвестная ошибка.",
            "warning": None,
        }


async def run_provider_tests() -> list[dict[str, Any]]:
    """Параллельно проверяет все настроенные провайдеры. Пустой список — если
    ничего не настроено."""
    targets = _collect_targets()
    if not targets:
        return []
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        return list(await asyncio.gather(*[_probe(client, t) for t in targets]))
