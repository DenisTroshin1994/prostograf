"""Пользовательские настройки LLM/эмбеддингов, задаваемые из WebUI.

Настройки хранятся в JSON-файле внутри рабочего каталога (тома данных),
поэтому переживают пересоздание контейнера и применяются при старте сервера
без пересборки образа: значения перекрывают переменные окружения до того,
как конфигурация будет прочитана.
"""

import json
import os
from pathlib import Path

SETTINGS_FILENAME = "user_llm_settings.json"

OPENROUTER_HOST = "https://openrouter.ai/api/v1"
DEEPSEEK_HOST = "https://api.deepseek.com/v1"
OPENAI_HOST = "https://api.openai.com/v1"

# Провайдеры LLM с фиксированным OpenAI-совместимым эндпоинтом (host задан нами).
PROVIDER_HOSTS = {
    "openrouter": OPENROUTER_HOST,
    "deepseek": DEEPSEEK_HOST,
    "openai": OPENAI_HOST,
}

# Провайдеры, у которых host вводит пользователь (любой OpenAI-совместимый API:
# vLLM, llama.cpp, Groq, Together, Mistral и т.п. — через «openai_compatible»;
# локальные движки — Ollama и LM Studio).
CUSTOM_HOST_PROVIDERS = {"openai_compatible", "ollama", "lmstudio"}

# Локальные движки не требуют реального API-ключа (принимают любой).
LOCAL_PROVIDERS = {"ollama", "lmstudio"}

# Хосты по умолчанию для провайдеров с вводимым адресом. host.docker.internal —
# чтобы из контейнера достучаться до движка на хост-машине (Docker Desktop).
DEFAULT_PROVIDER_HOSTS = {
    "openai_compatible": "",
    "ollama": "http://host.docker.internal:11434/v1",
    "lmstudio": "http://host.docker.internal:1234/v1",
}

# Все поддерживаемые провайдеры LLM (порядок = порядок в UI).
ALL_PROVIDERS = [
    "openrouter",
    "deepseek",
    "openai",
    "openai_compatible",
    "ollama",
    "lmstudio",
]

# Человекочитаемые метки (используются в т.ч. в отчёте тестирования).
PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "openai_compatible": "OpenAI-совместимый",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
}

# Реранкер: какие провайдеры (bindings) поддерживает ядро LightRAG и их
# эндпоинты по умолчанию (можно переопределить полем host). Совместимо с
# Cohere-style /rerank (в т.ч. через прокси вроде AITunnel/OpenRouter).
RERANK_BINDINGS = {"cohere", "jina", "aliyun"}
DEFAULT_RERANK_BINDING = "cohere"
DEFAULT_RERANK_MODEL = "rerank-v3.5"

# Полные URL эндпоинтов /rerank по умолчанию для каждого binding (совпадают с
# дефолтами в lightrag/rerank.py). Нужны модулю тестирования провайдеров.
RERANK_DEFAULT_HOSTS = {
    "cohere": "https://api.cohere.com/v2/rerank",
    "jina": "https://api.jina.ai/v1/rerank",
    "aliyun": "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
}


def resolve_provider_host(provider: str, creds: dict) -> str:
    """Эффективный host провайдера LLM: фиксированный (для openrouter/deepseek/
    openai) либо введённый пользователем (для openai_compatible/ollama/lmstudio),
    с подстановкой дефолта локального движка, если поле пустое."""
    fixed = PROVIDER_HOSTS.get(provider)
    if fixed:
        return fixed
    host = (creds.get("host") or "").strip()
    return host or DEFAULT_PROVIDER_HOSTS.get(provider, "")


def settings_file_path() -> Path:
    working_dir = os.getenv("WORKING_DIR", "./rag_storage")
    return Path(working_dir) / SETTINGS_FILENAME


def load_user_llm_settings() -> dict | None:
    path = settings_file_path()
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_user_llm_settings(data: dict) -> None:
    path = settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def apply_user_llm_overrides() -> None:
    """Перекрывает env-переменные значениями из файла настроек.

    Вызывается в самом начале чтения конфигурации (parse_args), поэтому
    настройки, сохранённые через WebUI, имеют приоритет над .env.
    Неполные секции (без ключа или модели) игнорируются.
    """
    data = load_user_llm_settings()
    if not data:
        return

    provider = data.get("provider")
    creds = data.get(provider) or {}
    host = resolve_provider_host(provider, creds)
    model = (creds.get("model") or "").strip()
    api_key = (creds.get("api_key") or "").strip()
    # Все провайдеры — OpenAI-совместимые, поэтому LLM_BINDING всегда "openai".
    # Локальные движки (Ollama/LM Studio) не требуют реального ключа: ставим
    # заглушку, т.к. клиент OpenAI не принимает пустую строку.
    is_local = provider in LOCAL_PROVIDERS
    if host and model and (api_key or is_local):
        os.environ["LLM_BINDING"] = "openai"
        os.environ["LLM_BINDING_HOST"] = host
        os.environ["LLM_MODEL"] = model
        os.environ["LLM_BINDING_API_KEY"] = api_key or "local"

    emb = data.get("embedding") or {}
    emb_host = (emb.get("host") or "").strip()
    emb_model = (emb.get("model") or "").strip()
    emb_key = (emb.get("api_key") or "").strip()
    if emb_host and emb_model and emb_key:
        os.environ["EMBEDDING_BINDING"] = "openai"
        os.environ["EMBEDDING_BINDING_HOST"] = emb_host
        os.environ["EMBEDDING_MODEL"] = emb_model
        os.environ["EMBEDDING_BINDING_API_KEY"] = emb_key
        dim = emb.get("dim")
        if isinstance(dim, int) and dim > 0:
            os.environ["EMBEDDING_DIM"] = str(dim)

    # Реранкер. Если секция присутствует — она имеет приоритет над .env: при
    # выключенном реранкере принудительно ставим RERANK_BINDING=null, чтобы
    # унаследованная из .env конфигурация случайно его не включила.
    rer = data.get("rerank")
    if isinstance(rer, dict):
        binding = (rer.get("binding") or DEFAULT_RERANK_BINDING).strip().lower()
        if binding not in RERANK_BINDINGS:
            binding = DEFAULT_RERANK_BINDING
        model = (rer.get("model") or "").strip()
        host = (rer.get("host") or "").strip()
        api_key = (rer.get("api_key") or "").strip()
        if rer.get("enabled") and model and api_key:
            os.environ["RERANK_BINDING"] = binding
            os.environ["RERANK_MODEL"] = model
            os.environ["RERANK_BINDING_API_KEY"] = api_key
            # Пустой host → используется эндпоинт по умолчанию выбранного binding.
            if host:
                os.environ["RERANK_BINDING_HOST"] = host
        else:
            os.environ["RERANK_BINDING"] = "null"

    # Чанкинг (применяется к НОВЫМ документам при индексации).
    chunk = data.get("chunk")
    if isinstance(chunk, dict):
        size = chunk.get("size")
        overlap = chunk.get("overlap")
        if isinstance(size, int) and size > 0:
            os.environ["CHUNK_SIZE"] = str(size)
        # Перекрытие применяем только если оно валидно (< размера): иначе шаг
        # чанкера (size - overlap) станет 0/отрицательным. Невалидное значение
        # игнорируем, оставляя дефолт, чтобы не сломать индексацию.
        if isinstance(overlap, int) and overlap >= 0 and (
            not isinstance(size, int) or overlap < size
        ):
            os.environ["CHUNK_OVERLAP_SIZE"] = str(overlap)
