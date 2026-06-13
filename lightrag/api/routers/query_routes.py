"""
Маршруты поисковых запросов (RAG) API ПростоГраф.
"""

import json
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from lightrag.base import QueryParam
from lightrag.api.utils_api import (
    get_combined_auth_dependency,
    get_optional_user,
    resolve_allowed_file_paths_for_user,
    apply_doc_meta_to_param,
)
from lightrag.utils import logger
from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    query: str = Field(
        min_length=3,
        description="Текст запроса",
    )

    mode: Literal["local", "global", "hybrid", "naive", "mix", "bypass"] = Field(
        default="mix",
        description="Режим запроса",
    )

    only_need_context: Optional[bool] = Field(
        default=None,
        description="Если True, возвращается только найденный контекст без генерации ответа.",
    )

    only_need_prompt: Optional[bool] = Field(
        default=None,
        description="Если True, возвращается только сформированный промпт без генерации ответа.",
    )

    response_type: Optional[str] = Field(
        min_length=1,
        default=None,
        description="Желаемый формат ответа. Примеры: 'Multiple Paragraphs' (несколько абзацев), 'Single Paragraph' (один абзац), 'Bullet Points' (маркированный список).",
    )

    top_k: Optional[int] = Field(
        ge=1,
        default=None,
        description="Количество извлекаемых элементов: сущностей в режиме 'local' и связей в режиме 'global'.",
    )

    chunk_top_k: Optional[int] = Field(
        ge=1,
        default=None,
        description="Количество текстовых фрагментов, извлекаемых векторным поиском и оставляемых после ранжирования.",
    )

    max_entity_tokens: Optional[int] = Field(
        default=None,
        description="Максимум токенов на контекст сущностей в единой системе бюджета токенов.",
        ge=1,
    )

    max_relation_tokens: Optional[int] = Field(
        default=None,
        description="Максимум токенов на контекст связей в единой системе бюджета токенов.",
        ge=1,
    )

    max_total_tokens: Optional[int] = Field(
        default=None,
        description="Общий бюджет токенов на весь контекст запроса (сущности + связи + фрагменты + системный промпт).",
        ge=1,
    )

    hl_keywords: list[str] = Field(
        default_factory=list,
        description="Высокоуровневые ключевые слова, приоритетные при поиске. Оставьте пустым, чтобы ключевые слова сгенерировала LLM.",
    )

    ll_keywords: list[str] = Field(
        default_factory=list,
        description="Низкоуровневые ключевые слова, уточняющие фокус поиска. Оставьте пустым, чтобы ключевые слова сгенерировала LLM.",
    )

    conversation_history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="История диалога; передаётся только LLM для контекста, на поиск не влияет. Формат: [{'role': 'user/assistant', 'content': 'сообщение'}].",
    )

    user_prompt: Optional[str] = Field(
        default=None,
        description="Пользовательский промпт. Если задан, используется вместо значения по умолчанию из шаблона промпта.",
    )

    enable_rerank: Optional[bool] = Field(
        default=None,
        description="Включить ранжирование найденных фрагментов. Если True, но модель ранжирования не настроена, будет выдано предупреждение. По умолчанию True.",
    )

    include_references: Optional[bool] = Field(
        default=True,
        description="Если True, в ответ включается список источников. Влияет на /query и /query/stream; /query/data всегда включает источники.",
    )

    include_chunk_content: Optional[bool] = Field(
        default=False,
        description="Если True, в источники включается текст фрагментов. Действует только при include_references=True. Полезно для оценки качества и отладки.",
    )

    stream: Optional[bool] = Field(
        default=True,
        description="Если True, включается потоковый вывод ответа в реальном времени. Влияет только на /query/stream.",
    )

    @field_validator("query", mode="after")
    @classmethod
    def query_strip_after(cls, query: str) -> str:
        return query.strip()

    @field_validator("conversation_history", mode="after")
    @classmethod
    def conversation_history_role_check(
        cls, conversation_history: List[Dict[str, Any]] | None
    ) -> List[Dict[str, Any]] | None:
        if conversation_history is None:
            return None
        for msg in conversation_history:
            if "role" not in msg:
                raise ValueError("Each message must have a 'role' key.")
            if not isinstance(msg["role"], str) or not msg["role"].strip():
                raise ValueError("Each message 'role' must be a non-empty string.")
        return conversation_history

    def to_query_params(self, is_stream: bool) -> "QueryParam":
        """Converts a QueryRequest instance into a QueryParam instance."""
        # Use Pydantic's `.model_dump(exclude_none=True)` to remove None values automatically
        # Exclude API-level parameters that don't belong in QueryParam
        request_data = self.model_dump(
            exclude_none=True, exclude={"query", "include_chunk_content"}
        )

        # Ensure `mode` and `stream` are set explicitly
        param = QueryParam(**request_data)
        param.stream = is_stream
        return param


class ReferenceItem(BaseModel):
    """Один источник в ответе на запрос."""

    reference_id: str = Field(description="Уникальный идентификатор источника")
    file_path: str = Field(description="Путь к исходному файлу")
    content: Optional[List[str]] = Field(
        default=None,
        description="Список текстов фрагментов из этого файла (только при include_chunk_content=True)",
    )


class UsageInfo(BaseModel):
    """Реальный учёт токенов запроса (подсчитан токенайзером системы)."""

    prompt_tokens: Optional[int] = Field(
        default=None,
        description="Токены промпта генерации (контекст + запрос). None, если неизвестно (например, режим bypass).",
    )
    completion_tokens: int = Field(
        default=0, description="Токены сгенерированного ответа"
    )
    total_tokens: int = Field(
        default=0, description="Суммарно токенов: промпт + ответ"
    )


class QueryResponse(BaseModel):
    response: str = Field(
        description="Сгенерированный ответ",
    )
    references: Optional[List[ReferenceItem]] = Field(
        default=None,
        description="Список источников (отключается при include_references=False; /query/data всегда включает источники.)",
    )
    usage: Optional[UsageInfo] = Field(
        default=None,
        description="Учёт токенов: реальные токены промпта генерации и ответа.",
    )


class QueryDataResponse(BaseModel):
    status: str = Field(description="Статус выполнения запроса")
    message: str = Field(description="Сообщение о результате")
    data: Dict[str, Any] = Field(
        description="Данные результата: сущности, связи, фрагменты и источники"
    )
    metadata: Dict[str, Any] = Field(
        description="Метаданные запроса: режим, ключевые слова, информация об обработке"
    )


class StreamChunkResponse(BaseModel):
    """Модель порции потокового ответа в формате NDJSON"""

    references: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Список источников (только в первой порции при include_references=True)",
    )
    response: Optional[str] = Field(
        default=None, description="Порция содержимого или полный ответ"
    )
    error: Optional[str] = Field(
        default=None, description="Сообщение об ошибке при сбое обработки"
    )


DEFAULT_REWRITE_TEMPLATE = """Ты переписываешь короткий уточняющий вопрос в самостоятельный поисковый запрос.
Сохрани действие из текущего вопроса и добавь недостающий объект из истории диалога.
Не отвечай на вопрос и не объясняй. Не добавляй фактов, которых нет в истории или текущем вопросе.
Если текущий вопрос уже самостоятельный — верни его без изменений.
Верни только готовый запрос одной строкой, без кавычек и пояснений.

История диалога:
{history}

Текущий вопрос: {question}

Самостоятельный запрос:"""


class RewriteRequest(BaseModel):
    query: str = Field(min_length=1, max_length=32000, description="Текущий (возможно, уточняющий) вопрос")
    conversation_history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        max_length=200,
        description="История диалога: [{'role': 'user/assistant', 'content': '...'}]",
    )
    rewrite_prompt: Optional[str] = Field(
        default=None,
        max_length=20000,
        description="Шаблон промпта реврайта с плейсхолдерами {history} и {question}",
    )
    history_turns: Optional[int] = Field(
        default=3, ge=1, le=50, description="Сколько последних пар вопрос-ответ учитывать"
    )


class RewriteResponse(BaseModel):
    rewritten: str = Field(description="Переписанный самостоятельный запрос")
    changed: bool = Field(description="Был ли запрос изменён относительно исходного")


def create_query_routes(rag, api_key: Optional[str] = None, top_k: int = 60):
    # Fresh router per call. A module-level instance would accumulate
    # duplicate routes when the factory is invoked more than once in the
    # same process (e.g. across tests), which triggers FastAPI's
    # "Duplicate Operation ID" warnings.
    router = APIRouter(tags=["query"])

    combined_auth = get_combined_auth_dependency(api_key)

    def _build_usage(metadata: Optional[Dict[str, Any]], answer_text: str) -> Dict[str, Any]:
        """Собирает реальный учёт токенов: промпт берётся из метаданных
        (посчитан в пайплайне), ответ токенизируется тем же токенайзером."""
        prompt_tokens = (metadata or {}).get("llm_prompt_tokens")
        try:
            completion_tokens = (
                len(rag.tokenizer.encode(answer_text)) if answer_text else 0
            )
        except Exception:
            completion_tokens = 0
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (prompt_tokens or 0) + completion_tokens,
        }

    @router.post(
        "/query",
        response_model=QueryResponse,
        dependencies=[Depends(combined_auth)],
        summary="RAG-запрос (полный ответ)",
        responses={
            200: {
                "description": "Успешный ответ RAG-запроса",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "response": {
                                    "type": "string",
                                    "description": "Сгенерированный ответ RAG-системы",
                                },
                                "references": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "reference_id": {"type": "string"},
                                            "file_path": {"type": "string"},
                                            "content": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                                "description": "Тексты фрагментов из этого файла (только при include_chunk_content=True)",
                                            },
                                        },
                                    },
                                    "description": "Список источников (только при include_references=True)",
                                },
                            },
                            "required": ["response"],
                        },
                        "examples": {
                            "with_references": {
                                "summary": "Ответ с источниками",
                                "description": "Пример ответа при include_references=True",
                                "value": {
                                    "response": "Искусственный интеллект (ИИ) — это раздел информатики, цель которого — создание интеллектуальных машин, способных выполнять задачи, обычно требующие человеческого интеллекта: обучение, рассуждение и решение задач.",
                                    "references": [
                                        {
                                            "reference_id": "1",
                                            "file_path": "/documents/obzor_ii.pdf",
                                        },
                                        {
                                            "reference_id": "2",
                                            "file_path": "/documents/mashinnoe_obuchenie.txt",
                                        },
                                    ],
                                },
                            },
                            "with_chunk_content": {
                                "summary": "Ответ с текстами фрагментов",
                                "description": "Пример ответа при include_references=True и include_chunk_content=True. Обратите внимание: content — массив фрагментов одного файла.",
                                "value": {
                                    "response": "Искусственный интеллект (ИИ) — это раздел информатики, цель которого — создание интеллектуальных машин, способных выполнять задачи, обычно требующие человеческого интеллекта: обучение, рассуждение и решение задач.",
                                    "references": [
                                        {
                                            "reference_id": "1",
                                            "file_path": "/documents/obzor_ii.pdf",
                                            "content": [
                                                "Искусственный интеллект (ИИ) — преобразующее направление информатики, сосредоточенное на создании систем, способных выполнять задачи, требующие интеллекта, подобного человеческому: обучение на опыте, понимание естественного языка, распознавание образов и принятие решений.",
                                                "Системы ИИ делятся на узкий ИИ, созданный для конкретных задач, и общий ИИ, стремящийся сравняться с когнитивными способностями человека в широком круге областей.",
                                            ],
                                        },
                                        {
                                            "reference_id": "2",
                                            "file_path": "/documents/mashinnoe_obuchenie.txt",
                                            "content": [
                                                "Машинное обучение — подраздел ИИ, позволяющий компьютерам учиться и совершенствоваться на основе опыта без явного программирования. Оно сосредоточено на разработке алгоритмов, которые получают данные и учатся на них самостоятельно."
                                            ],
                                        },
                                    ],
                                },
                            },
                            "without_references": {
                                "summary": "Ответ без источников",
                                "description": "Пример ответа при include_references=False",
                                "value": {
                                    "response": "Искусственный интеллект (ИИ) — это раздел информатики, цель которого — создание интеллектуальных машин, способных выполнять задачи, обычно требующие человеческого интеллекта: обучение, рассуждение и решение задач."
                                },
                            },
                            "different_modes": {
                                "summary": "Режимы запроса",
                                "description": "Чем отличаются режимы запроса",
                                "value": {
                                    "local_mode": "Фокус на конкретных сущностях и их связях",
                                    "global_mode": "Широкий контекст на основе паттернов связей",
                                    "hybrid_mode": "Сочетание локального и глобального подходов",
                                    "naive_mode": "Простой векторный поиск по сходству",
                                    "mix_mode": "Объединение графа знаний и векторного поиска",
                                },
                            },
                        },
                    }
                },
            },
            400: {
                "description": "Некорректный запрос — неверные входные параметры",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Текст запроса должен содержать не менее 3 символов"
                        },
                    }
                },
            },
            500: {
                "description": "Внутренняя ошибка сервера — сбой обработки запроса",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Не удалось обработать запрос: LLM-сервис недоступен"
                        },
                    }
                },
            },
        },
    )
    async def query_text(
        request: QueryRequest, user: Optional[Dict] = Depends(get_optional_user)
    ):
        """
        Основной RAG-запрос без потоковой передачи. Параметр «stream» игнорируется.

        Эндпоинт выполняет запросы с дополненной поиском генерацией (RAG) в разных
        режимах и формирует осмысленные ответы на основе вашей базы знаний.

        **Режимы запроса:**
        - **local**: фокус на конкретных сущностях и их прямых связях
        - **global**: анализ широких паттернов и связей по всему графу знаний
        - **hybrid**: сочетание локального и глобального подходов
        - **naive**: простой векторный поиск по сходству, без графа знаний
        - **mix**: объединение поиска по графу знаний с векторным поиском (рекомендуется)
        - **bypass**: прямой запрос к LLM без поиска по базе знаний

        Параметр conversation_history передаётся только LLM и не влияет на результаты поиска.

        **Примеры использования:**

        Простой запрос:
        ```json
        {
            "query": "Что такое машинное обучение?",
            "mode": "mix"
        }
        ```

        Пропуск первого обращения к LLM за счёт готовых ключевых слов:
        ```json
        {
            "query": "Что такое генерация с дополненным поиском?",
            "hl_keywords": ["машинное обучение", "информационный поиск", "обработка естественного языка"],
            "ll_keywords": ["генерация с дополненным поиском", "RAG", "база знаний"],
            "mode": "mix"
        }
        ```

        Расширенный запрос с источниками:
        ```json
        {
            "query": "Объясни нейронные сети",
            "mode": "hybrid",
            "include_references": true,
            "response_type": "Multiple Paragraphs",
            "top_k": 10
        }
        ```

        Диалог с историей:
        ```json
        {
            "query": "Расскажи подробнее",
            "conversation_history": [
                {"role": "user", "content": "Что такое ИИ?"},
                {"role": "assistant", "content": "ИИ — это искусственный интеллект..."}
            ]
        }
        ```

        Args:
            request (QueryRequest): объект запроса с параметрами:
                - **query**: вопрос или промпт (минимум 3 символа)
                - **mode**: стратегия поиска — для лучших результатов рекомендуется «mix»
                - **include_references**: включать ли ссылки на источники
                - **response_type**: желаемый формат (например, «Multiple Paragraphs»)
                - **top_k**: количество извлекаемых сущностей/связей
                - **conversation_history**: контекст предыдущего диалога
                - **max_total_tokens**: бюджет токенов на весь ответ

        Returns:
            QueryResponse: JSON-ответ, содержащий:
                - **response**: сгенерированный ответ на запрос
                - **references**: ссылки на источники (при include_references=True)

        Raises:
            HTTPException:
                - 400: неверные входные параметры (например, слишком короткий запрос)
                - 500: внутренняя ошибка обработки (например, LLM-сервис недоступен)
        """
        try:
            param = request.to_query_params(
                False
            )  # Ensure stream=False for non-streaming endpoint
            # Force stream=False for /query endpoint regardless of include_references setting
            param.stream = False
            # ACL по отделу пользователя (None — полный доступ для admin/API-ключа)
            param.allowed_file_paths = resolve_allowed_file_paths_for_user(user)
            apply_doc_meta_to_param(param)  # METAINFO + «целиком» документов

            # Unified approach: always use aquery_llm for both cases
            result = await rag.aquery_llm(request.query, param=param)

            # Extract LLM response and references from unified result
            llm_response = result.get("llm_response", {})
            data = result.get("data", {})
            references = data.get("references", [])

            # Get the non-streaming response content
            response_content = llm_response.get("content", "")
            if not response_content:
                response_content = "По запросу не найдено релевантного контекста."

            # Enrich references with chunk content if requested
            if request.include_references and request.include_chunk_content:
                chunks = data.get("chunks", [])
                # Create a mapping from reference_id to chunk content
                ref_id_to_content = {}
                for chunk in chunks:
                    ref_id = chunk.get("reference_id", "")
                    content = chunk.get("content", "")
                    if ref_id and content:
                        # Collect chunk content; join later to avoid quadratic string concatenation
                        ref_id_to_content.setdefault(ref_id, []).append(content)

                # Add content to references
                enriched_references = []
                for ref in references:
                    ref_copy = ref.copy()
                    ref_id = ref.get("reference_id", "")
                    if ref_id in ref_id_to_content:
                        # Keep content as a list of chunks (one file may have multiple chunks)
                        ref_copy["content"] = ref_id_to_content[ref_id]
                    enriched_references.append(ref_copy)
                references = enriched_references

            usage = UsageInfo(
                **_build_usage(result.get("metadata"), response_content)
            )

            # Return response with or without references based on request
            if request.include_references:
                return QueryResponse(
                    response=response_content, references=references, usage=usage
                )
            else:
                return QueryResponse(
                    response=response_content, references=None, usage=usage
                )
        except Exception as e:
            logger.error(f"Error processing query: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post(
        "/query/stream",
        dependencies=[Depends(combined_auth)],
        summary="RAG-запрос (потоковый)",
        responses={
            200: {
                "description": "Гибкий ответ RAG-запроса — формат зависит от параметра stream",
                "content": {
                    "application/x-ndjson": {
                        "schema": {
                            "type": "string",
                            "format": "ndjson",
                            "description": "Формат NDJSON (JSON с разделением переводами строк) используется и для потоковых, и для непотоковых ответов. Потоковый режим: несколько строк с отдельными JSON-объектами. Непотоковый: одна строка с полным JSON-объектом.",
                            "example": '{"references": [{"reference_id": "1", "file_path": "/documents/ii.pdf"}]}\n{"response": "Искусственный интеллект — это"}\n{"response": " область информатики,"}\n{"response": " посвящённая созданию интеллектуальных машин."}',
                        },
                        "examples": {
                            "streaming_with_references": {
                                "summary": "Потоковый режим с источниками (stream=true)",
                                "description": "Несколько строк NDJSON при stream=True и include_references=True. Первая строка содержит источники, последующие — порции ответа.",
                                "value": '{"references": [{"reference_id": "1", "file_path": "/documents/obzor_ii.pdf"}, {"reference_id": "2", "file_path": "/documents/osnovy_mo.txt"}]}\n{"response": "Искусственный интеллект (ИИ) — раздел информатики,"}\n{"response": " цель которого — создание интеллектуальных машин,"}\n{"response": " способных выполнять задачи, обычно требующие человеческого интеллекта:"}\n{"response": " обучение, рассуждение и решение задач."}',
                            },
                            "streaming_with_chunk_content": {
                                "summary": "Потоковый режим с текстами фрагментов (stream=true, include_chunk_content=true)",
                                "description": "Несколько строк NDJSON при stream=True, include_references=True и include_chunk_content=True. Первая строка содержит источники с массивами content (у одного файла может быть несколько фрагментов), последующие — порции ответа.",
                                "value": '{"references": [{"reference_id": "1", "file_path": "/documents/obzor_ii.pdf", "content": ["Искусственный интеллект (ИИ) — преобразующее направление информатики...", "Системы ИИ делятся на узкий ИИ и общий ИИ..."]}, {"reference_id": "2", "file_path": "/documents/osnovy_mo.txt", "content": ["Машинное обучение — подраздел ИИ, позволяющий компьютерам учиться..."]}]}\n{"response": "Искусственный интеллект (ИИ) — раздел информатики,"}\n{"response": " цель которого — создание интеллектуальных машин,"}\n{"response": " способных выполнять сложные задачи."}',
                            },
                            "streaming_without_references": {
                                "summary": "Потоковый режим без источников (stream=true)",
                                "description": "Несколько строк NDJSON при stream=True и include_references=False. Передаются только порции ответа.",
                                "value": '{"response": "Машинное обучение — подраздел искусственного интеллекта,"}\n{"response": " позволяющий компьютерам учиться и совершенствоваться на основе опыта"}\n{"response": " без явного программирования каждой задачи."}',
                            },
                            "non_streaming_with_references": {
                                "summary": "Непотоковый режим с источниками (stream=false)",
                                "description": "Одна строка NDJSON при stream=False и include_references=True. Полный ответ с источниками одним сообщением.",
                                "value": '{"references": [{"reference_id": "1", "file_path": "/documents/neironnye_seti.pdf"}], "response": "Нейронные сети — вычислительные модели, вдохновлённые биологическими нейронными сетями: взаимосвязанные узлы (нейроны), организованные слоями. Они лежат в основе глубокого обучения и способны выучивать сложные закономерности в данных в процессе обучения."}',
                            },
                            "non_streaming_without_references": {
                                "summary": "Непотоковый режим без источников (stream=false)",
                                "description": "Одна строка NDJSON при stream=False и include_references=False. Только полный ответ.",
                                "value": '{"response": "Глубокое обучение — подраздел машинного обучения, использующий многослойные (отсюда «глубокие») нейронные сети для моделирования сложных закономерностей в данных. Оно произвело революцию в компьютерном зрении, обработке естественного языка и распознавании речи."}',
                            },
                            "error_response": {
                                "summary": "Ошибка во время потоковой передачи",
                                "description": "Обработка ошибок в формате NDJSON при сбое во время обработки.",
                                "value": '{"references": [{"reference_id": "1", "file_path": "/documents/ii.pdf"}]}\n{"response": "Искусственный интеллект — это"}\n{"error": "LLM-сервис временно недоступен"}',
                            },
                        },
                    }
                },
            },
            400: {
                "description": "Некорректный запрос — неверные входные параметры",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Текст запроса должен содержать не менее 3 символов"
                        },
                    }
                },
            },
            500: {
                "description": "Внутренняя ошибка сервера — сбой обработки запроса",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Не удалось обработать потоковый запрос: граф знаний недоступен"
                        },
                    }
                },
            },
        },
    )
    async def query_text_stream(
        request: QueryRequest, user: Optional[Dict] = Depends(get_optional_user)
    ):
        """
        RAG-запрос с гибкой потоковой передачей ответа.

        Самый гибкий вариант запроса: поддерживает и передачу ответа в реальном
        времени, и доставку полного ответа одним сообщением — в зависимости от
        потребностей вашей интеграции.

        **Режимы ответа:**
        - Передача ответа в реальном времени по мере генерации
        - Формат NDJSON: каждая строка — отдельный JSON-объект
        - Первая строка: `{"references": [...]}` (при include_references=True)
        - Последующие строки: `{"response": "порция содержимого"}`
        - Ошибки: `{"error": "сообщение об ошибке"}`

        > Если параметр stream равен False или запрос попал в кэш LLM, полный ответ передаётся одним потоковым сообщением.

        **Детали формата ответа**
        - **Content-Type**: `application/x-ndjson` (JSON с разделением переводами строк)
        - **Структура**: каждая строка — независимый корректный JSON-объект
        - **Разбор**: обрабатывайте построчно, каждая строка самодостаточна
        - **Заголовки**: включают управление кэшированием и соединением

        **Режимы запроса (как у /query)**
        - **local**: поиск с фокусом на сущностях и их прямых связях
        - **global**: анализ паттернов по всему графу знаний
        - **hybrid**: сочетание локальной и глобальной стратегий
        - **naive**: только векторный поиск по сходству
        - **mix**: объединение графа знаний и векторного поиска (рекомендуется)
        - **bypass**: прямой запрос к LLM без поиска по базе знаний

        Параметр conversation_history передаётся только LLM и не влияет на результаты поиска.

        **Примеры использования**

        Потоковый запрос в реальном времени:
        ```json
        {
            "query": "Объясни алгоритмы машинного обучения",
            "mode": "mix",
            "stream": true,
            "include_references": true
        }
        ```

        Пропуск первого обращения к LLM за счёт готовых ключевых слов:
        ```json
        {
            "query": "Что такое генерация с дополненным поиском?",
            "hl_keywords": ["машинное обучение", "информационный поиск", "обработка естественного языка"],
            "ll_keywords": ["генерация с дополненным поиском", "RAG", "база знаний"],
            "mode": "mix"
        }
        ```

        Запрос полного ответа:
        ```json
        {
            "query": "Что такое глубокое обучение?",
            "mode": "hybrid",
            "stream": false,
            "response_type": "Multiple Paragraphs"
        }
        ```

        Диалог с контекстом:
        ```json
        {
            "query": "Можешь рассказать подробнее?",
            "stream": true,
            "conversation_history": [
                {"role": "user", "content": "Что такое нейронная сеть?"},
                {"role": "assistant", "content": "Нейронная сеть — это..."}
            ]
        }
        ```

        **Обработка ответа:**

        ```python
        async for line in response.iter_lines():
            data = json.loads(line)
            if "references" in data:
                # Источники (первое сообщение)
                references = data["references"]
            if "response" in data:
                # Порция содержимого
                content_chunk = data["response"]
            if "error" in data:
                # Ошибка
                error_message = data["error"]
        ```

        **Обработка ошибок:**
        - Ошибки потока передаются строками `{"error": "сообщение"}`
        - Непотоковые ошибки выбрасывают HTTP-исключения
        - В потоковом режиме до ошибки могут прийти частичные данные
        - Всегда проверяйте наличие объектов error при разборе потока

        Args:
            request (QueryRequest): объект запроса с параметрами:
                - **query**: вопрос или промпт (минимум 3 символа)
                - **mode**: стратегия поиска — для лучших результатов рекомендуется «mix»
                - **stream**: потоковая передача (True) или полный ответ (False)
                - **include_references**: включать ли ссылки на источники
                - **response_type**: желаемый формат (например, «Multiple Paragraphs»)
                - **top_k**: количество извлекаемых сущностей/связей
                - **conversation_history**: контекст предыдущего диалога
                - **max_total_tokens**: бюджет токенов на весь ответ

        Returns:
            StreamingResponse: потоковый NDJSON-ответ:
                - **Потоковый режим**: несколько JSON-объектов, по одному в строке
                  - Объект источников (если запрошен): `{"references": [...]}`
                  - Порции содержимого: `{"response": "порция"}`
                  - Объекты ошибок: `{"error": "сообщение"}`
                - **Непотоковый режим**: один JSON-объект
                  - Полный ответ: `{"references": [...], "response": "полное содержимое"}`

        Raises:
            HTTPException:
                - 400: неверные входные параметры (например, слишком короткий запрос)
                - 500: внутренняя ошибка обработки (например, LLM-сервис недоступен)

        Note:
            Потоковый режим подходит для интерфейсов реального времени,
            непотоковый — для пакетной обработки.
        """
        try:
            # Use the stream parameter from the request, defaulting to True if not specified
            stream_mode = request.stream if request.stream is not None else True
            param = request.to_query_params(stream_mode)
            # ACL по отделу пользователя (None — полный доступ для admin/API-ключа)
            param.allowed_file_paths = resolve_allowed_file_paths_for_user(user)
            apply_doc_meta_to_param(param)  # METAINFO + «целиком» документов

            from fastapi.responses import StreamingResponse

            # Unified approach: always use aquery_llm for all cases
            result = await rag.aquery_llm(request.query, param=param)

            async def stream_generator():
                # Extract references and LLM response from unified result
                references = result.get("data", {}).get("references", [])
                llm_response = result.get("llm_response", {})
                metadata = result.get("metadata", {})

                # Enrich references with chunk content if requested
                if request.include_references and request.include_chunk_content:
                    data = result.get("data", {})
                    chunks = data.get("chunks", [])
                    # Create a mapping from reference_id to chunk content
                    ref_id_to_content = {}
                    for chunk in chunks:
                        ref_id = chunk.get("reference_id", "")
                        content = chunk.get("content", "")
                        if ref_id and content:
                            # Collect chunk content
                            ref_id_to_content.setdefault(ref_id, []).append(content)

                    # Add content to references
                    enriched_references = []
                    for ref in references:
                        ref_copy = ref.copy()
                        ref_id = ref.get("reference_id", "")
                        if ref_id in ref_id_to_content:
                            # Keep content as a list of chunks (one file may have multiple chunks)
                            ref_copy["content"] = ref_id_to_content[ref_id]
                        enriched_references.append(ref_copy)
                    references = enriched_references

                if llm_response.get("is_streaming"):
                    # Streaming mode: send references first, then stream response chunks
                    if request.include_references:
                        yield f"{json.dumps({'references': references})}\n"

                    response_stream = llm_response.get("response_iterator")
                    answer_parts: List[str] = []
                    if response_stream:
                        try:
                            async for chunk in response_stream:
                                if chunk:  # Only send non-empty content
                                    answer_parts.append(chunk)
                                    yield f"{json.dumps({'response': chunk})}\n"
                        except Exception as e:
                            logger.error(f"Streaming error: {str(e)}")
                            yield f"{json.dumps({'error': str(e)})}\n"
                    # Финальная строка с реальным учётом токенов (промпт + ответ)
                    usage = _build_usage(metadata, "".join(answer_parts))
                    yield f"{json.dumps({'usage': usage})}\n"
                else:
                    # Non-streaming mode: send complete response in one message
                    response_content = llm_response.get("content", "")
                    if not response_content:
                        response_content = "По запросу не найдено релевантного контекста."

                    # Create complete response object
                    complete_response = {"response": response_content}
                    if request.include_references:
                        complete_response["references"] = references

                    yield f"{json.dumps(complete_response)}\n"
                    usage = _build_usage(metadata, response_content)
                    yield f"{json.dumps({'usage': usage})}\n"

            return StreamingResponse(
                stream_generator(),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "application/x-ndjson",
                    "X-Accel-Buffering": "no",  # Ensure proper handling of streaming response when proxied by Nginx
                },
            )
        except Exception as e:
            logger.error(f"Error processing streaming query: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post(
        "/query/data",
        response_model=QueryDataResponse,
        dependencies=[Depends(combined_auth)],
        summary="Данные поиска без генерации",
        responses={
            200: {
                "description": "Успешный ответ со структурированными данными поиска",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "status": {
                                    "type": "string",
                                    "enum": ["success", "failure"],
                                    "description": "Статус выполнения запроса",
                                },
                                "message": {
                                    "type": "string",
                                    "description": "Сообщение о результате",
                                },
                                "data": {
                                    "type": "object",
                                    "properties": {
                                        "entities": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "entity_name": {"type": "string"},
                                                    "entity_type": {"type": "string"},
                                                    "description": {"type": "string"},
                                                    "source_id": {"type": "string"},
                                                    "file_path": {"type": "string"},
                                                    "reference_id": {"type": "string"},
                                                },
                                            },
                                            "description": "Сущности, извлечённые из графа знаний",
                                        },
                                        "relationships": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "src_id": {"type": "string"},
                                                    "tgt_id": {"type": "string"},
                                                    "description": {"type": "string"},
                                                    "keywords": {"type": "string"},
                                                    "weight": {"type": "number"},
                                                    "source_id": {"type": "string"},
                                                    "file_path": {"type": "string"},
                                                    "reference_id": {"type": "string"},
                                                },
                                            },
                                            "description": "Связи, извлечённые из графа знаний",
                                        },
                                        "chunks": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "content": {"type": "string"},
                                                    "file_path": {"type": "string"},
                                                    "chunk_id": {"type": "string"},
                                                    "reference_id": {"type": "string"},
                                                },
                                            },
                                            "description": "Текстовые фрагменты из векторной базы",
                                        },
                                        "references": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "reference_id": {"type": "string"},
                                                    "file_path": {"type": "string"},
                                                },
                                            },
                                            "description": "Список источников для цитирования",
                                        },
                                    },
                                    "description": "Структурированные данные поиска: сущности, связи, фрагменты и источники",
                                },
                                "metadata": {
                                    "type": "object",
                                    "properties": {
                                        "query_mode": {"type": "string"},
                                        "keywords": {
                                            "type": "object",
                                            "properties": {
                                                "high_level": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                                "low_level": {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                },
                                            },
                                        },
                                        "processing_info": {
                                            "type": "object",
                                            "properties": {
                                                "total_entities_found": {
                                                    "type": "integer"
                                                },
                                                "total_relations_found": {
                                                    "type": "integer"
                                                },
                                                "entities_after_truncation": {
                                                    "type": "integer"
                                                },
                                                "relations_after_truncation": {
                                                    "type": "integer"
                                                },
                                                "final_chunks_count": {
                                                    "type": "integer"
                                                },
                                            },
                                        },
                                    },
                                    "description": "Метаданные запроса: режим, ключевые слова, информация об обработке",
                                },
                            },
                            "required": ["status", "message", "data", "metadata"],
                        },
                        "examples": {
                            "successful_local_mode": {
                                "summary": "Данные в режиме local",
                                "description": "Пример структурированных данных запроса в режиме local с фокусом на конкретных сущностях",
                                "value": {
                                    "status": "success",
                                    "message": "Запрос выполнен успешно",
                                    "data": {
                                        "entities": [
                                            {
                                                "entity_name": "Нейронные сети",
                                                "entity_type": "CONCEPT",
                                                "description": "Вычислительные модели, вдохновлённые биологическими нейронными сетями",
                                                "source_id": "chunk-123",
                                                "file_path": "/documents/osnovy_ii.pdf",
                                                "reference_id": "1",
                                            }
                                        ],
                                        "relationships": [
                                            {
                                                "src_id": "Нейронные сети",
                                                "tgt_id": "Машинное обучение",
                                                "description": "Нейронные сети — подмножество алгоритмов машинного обучения",
                                                "keywords": "подмножество, алгоритм, обучение",
                                                "weight": 0.85,
                                                "source_id": "chunk-123",
                                                "file_path": "/documents/osnovy_ii.pdf",
                                                "reference_id": "1",
                                            }
                                        ],
                                        "chunks": [
                                            {
                                                "content": "Нейронные сети — вычислительные модели, имитирующие работу биологических нейронных сетей...",
                                                "file_path": "/documents/osnovy_ii.pdf",
                                                "chunk_id": "chunk-123",
                                                "reference_id": "1",
                                            }
                                        ],
                                        "references": [
                                            {
                                                "reference_id": "1",
                                                "file_path": "/documents/osnovy_ii.pdf",
                                            }
                                        ],
                                    },
                                    "metadata": {
                                        "query_mode": "local",
                                        "keywords": {
                                            "high_level": ["нейронные", "сети"],
                                            "low_level": [
                                                "вычисление",
                                                "модель",
                                                "алгоритм",
                                            ],
                                        },
                                        "processing_info": {
                                            "total_entities_found": 5,
                                            "total_relations_found": 3,
                                            "entities_after_truncation": 1,
                                            "relations_after_truncation": 1,
                                            "final_chunks_count": 1,
                                        },
                                    },
                                },
                            },
                            "global_mode": {
                                "summary": "Данные в режиме global",
                                "description": "Пример структурированных данных запроса в режиме global с анализом широких паттернов",
                                "value": {
                                    "status": "success",
                                    "message": "Запрос выполнен успешно",
                                    "data": {
                                        "entities": [],
                                        "relationships": [
                                            {
                                                "src_id": "Искусственный интеллект",
                                                "tgt_id": "Машинное обучение",
                                                "description": "ИИ включает машинное обучение как ключевую составляющую",
                                                "keywords": "включает, составляющая, область",
                                                "weight": 0.92,
                                                "source_id": "chunk-456",
                                                "file_path": "/documents/obzor_ii.pdf",
                                                "reference_id": "2",
                                            }
                                        ],
                                        "chunks": [],
                                        "references": [
                                            {
                                                "reference_id": "2",
                                                "file_path": "/documents/obzor_ii.pdf",
                                            }
                                        ],
                                    },
                                    "metadata": {
                                        "query_mode": "global",
                                        "keywords": {
                                            "high_level": [
                                                "искусственный",
                                                "интеллект",
                                                "обзор",
                                            ],
                                            "low_level": [],
                                        },
                                    },
                                },
                            },
                            "naive_mode": {
                                "summary": "Данные в режиме naive",
                                "description": "Пример структурированных данных в режиме naive — только векторный поиск",
                                "value": {
                                    "status": "success",
                                    "message": "Запрос выполнен успешно",
                                    "data": {
                                        "entities": [],
                                        "relationships": [],
                                        "chunks": [
                                            {
                                                "content": "Глубокое обучение — подраздел машинного обучения, использующий многослойные нейронные сети...",
                                                "file_path": "/documents/glubokoe_obuchenie.pdf",
                                                "chunk_id": "chunk-789",
                                                "reference_id": "3",
                                            }
                                        ],
                                        "references": [
                                            {
                                                "reference_id": "3",
                                                "file_path": "/documents/glubokoe_obuchenie.pdf",
                                            }
                                        ],
                                    },
                                    "metadata": {
                                        "query_mode": "naive",
                                        "keywords": {"high_level": [], "low_level": []},
                                    },
                                },
                            },
                        },
                    }
                },
            },
            400: {
                "description": "Некорректный запрос — неверные входные параметры",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Текст запроса должен содержать не менее 3 символов"
                        },
                    }
                },
            },
            500: {
                "description": "Внутренняя ошибка сервера — сбой получения данных",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"detail": {"type": "string"}},
                        },
                        "example": {
                            "detail": "Не удалось получить данные: граф знаний недоступен"
                        },
                    }
                },
            },
        },
    )
    async def query_data(
        request: QueryRequest, user: Optional[Dict] = Depends(get_optional_user)
    ):
        """
        Получение структурированных данных поиска без генерации ответа.

        Эндпоинт возвращает «сырые» результаты поиска без обращения к LLM. Подходит для:
        - **Анализа данных**: посмотреть, какая информация будет использована в RAG
        - **Интеграции**: получить структурированные данные для собственной обработки
        - **Отладки**: понять поведение и качество поиска
        - **Исследований**: анализ структуры графа знаний и связей

        **Ключевые особенности:**
        - Без генерации LLM — только поиск данных
        - Полный структурированный вывод: сущности, связи и фрагменты
        - Источники для цитирования включаются всегда
        - Подробные метаданные об обработке и ключевых словах
        - Совместим со всеми режимами и параметрами запроса

        **Поведение режимов:**
        - **local**: сущности с их прямыми связями + связанные фрагменты
        - **global**: паттерны связей по всему графу знаний
        - **hybrid**: сочетание локальной и глобальной стратегий
        - **naive**: только фрагменты из векторного поиска (без графа знаний)
        - **mix**: данные графа знаний вместе с фрагментами векторного поиска
        - **bypass**: пустые массивы данных (режим прямых запросов к LLM)

        **Структура данных:**
        - **entities**: сущности графа знаний с описаниями и метаданными
        - **relationships**: связи между сущностями с весами и описаниями
        - **chunks**: текстовые фрагменты документов с информацией об источнике
        - **references**: сопоставление идентификаторов источников путям файлов
        - **metadata**: информация об обработке, ключевые слова и статистика

        **Примеры использования:**

        Анализ связей сущностей:
        ```json
        {
            "query": "алгоритмы машинного обучения",
            "mode": "local",
            "top_k": 10
        }
        ```

        Исследование глобальных паттернов:
        ```json
        {
            "query": "тенденции искусственного интеллекта",
            "mode": "global",
            "max_relation_tokens": 2000
        }
        ```

        Векторный поиск по сходству:
        ```json
        {
            "query": "архитектуры нейронных сетей",
            "mode": "naive",
            "chunk_top_k": 5
        }
        ```

        Пропуск первого обращения к LLM за счёт готовых ключевых слов:
        ```json
        {
            "query": "Что такое генерация с дополненным поиском?",
            "hl_keywords": ["машинное обучение", "информационный поиск", "обработка естественного языка"],
            "ll_keywords": ["генерация с дополненным поиском", "RAG", "база знаний"],
            "mode": "mix"
        }
        ```

        **Анализ ответа:**
        - **Пустые массивы**: нормальны для некоторых режимов (например, в naive нет сущностей/связей)
        - **processing_info**: статистика поиска и использования токенов
        - **keywords**: высоко- и низкоуровневые ключевые слова, извлечённые из запроса
        - **references**: привязка всех данных к исходным документам

        Args:
            request (QueryRequest): объект запроса с параметрами:
                - **query**: поисковый запрос для анализа (минимум 3 символа)
                - **mode**: стратегия поиска, определяющая типы возвращаемых данных
                - **top_k**: количество извлекаемых сущностей/связей
                - **chunk_top_k**: количество извлекаемых текстовых фрагментов
                - **max_entity_tokens**: лимит токенов на контекст сущностей
                - **max_relation_tokens**: лимит токенов на контекст связей
                - **max_total_tokens**: общий бюджет токенов на поиск

        Returns:
            QueryDataResponse: структурированный JSON-ответ:
                - **status**: «success» или «failure»
                - **message**: понятное описание результата
                - **data**: полные результаты поиска — сущности, связи, фрагменты, источники
                - **metadata**: информация об обработке запроса и статистика

        Raises:
            HTTPException:
                - 400: неверные входные параметры (например, слишком короткий запрос)
                - 500: внутренняя ошибка обработки (например, граф знаний недоступен)

        Note:
            Этот эндпоинт всегда включает источники независимо от параметра
            include_references: анализ структурированных данных обычно требует атрибуции.
        """
        try:
            param = request.to_query_params(False)  # No streaming for data endpoint
            param.allowed_file_paths = resolve_allowed_file_paths_for_user(user)
            apply_doc_meta_to_param(param)  # METAINFO + «целиком» документов
            response = await rag.aquery_data(request.query, param=param)

            # aquery_data returns the new format with status, message, data, and metadata
            if isinstance(response, dict):
                return QueryDataResponse(**response)
            else:
                # Handle unexpected response format
                return QueryDataResponse(
                    status="failure",
                    message="Некорректный тип ответа",
                    data={},
                    metadata={},
                )
        except Exception as e:
            logger.error(f"Error processing data query: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post(
        "/query/rewrite",
        response_model=RewriteResponse,
        dependencies=[Depends(combined_auth)],
        summary="Переписать follow-up вопрос в самостоятельный",
    )
    async def rewrite_query(request: RewriteRequest):
        """
        Доменно-нейтральное переписывание уточняющего вопроса в самостоятельный
        поисковый запрос с учётом истории диалога. Используется чат-вкладкой,
        когда включён режим «Реврайт» и в диалоге уже есть предыдущие сообщения.

        Если истории нет или запрос уже самостоятельный — вернётся исходный текст.
        """
        question = (request.query or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="Пустой запрос")

        history = request.conversation_history or []
        if not history:
            # Нечего уточнять — возвращаем как есть.
            return RewriteResponse(rewritten=question, changed=False)

        # Берём последние N пар (history_turns пар = 2*N сообщений)
        turns = request.history_turns if request.history_turns and request.history_turns > 0 else 3
        recent = history[-(turns * 2):]
        history_text = "\n".join(
            f"{'Пользователь' if m.get('role') == 'user' else 'Ассистент'}: {m.get('content', '')}"
            for m in recent
            if m.get("content")
        )

        template = request.rewrite_prompt or DEFAULT_REWRITE_TEMPLATE
        try:
            prompt = template.format(history=history_text, question=question)
        except (KeyError, IndexError, ValueError):
            # Промпт без нужных плейсхолдеров — собираем безопасно вручную.
            prompt = (
                f"{template}\n\nИстория диалога:\n{history_text}\n\n"
                f"Текущий вопрос: {question}\n\nСамостоятельный запрос:"
            )

        try:
            rewritten = await rag.llm_model_func(prompt)
        except Exception as e:
            logger.warning(f"Сбой реврайта запроса, используем исходный: {e}")
            return RewriteResponse(rewritten=question, changed=False)

        rewritten = (rewritten or "").strip().strip('"').strip()
        # Защита от пустого/слишком длинного/«размышляющего» ответа
        if not rewritten or len(rewritten) > 500:
            return RewriteResponse(rewritten=question, changed=False)
        return RewriteResponse(
            rewritten=rewritten, changed=(rewritten.lower() != question.lower())
        )

    return router
