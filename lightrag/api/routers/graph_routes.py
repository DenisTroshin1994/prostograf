"""
Маршруты работы с графом знаний API ПростоГраф.
"""

from typing import Optional, Dict, Any
import traceback
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, field_validator

from lightrag.base import DeletionResult
from lightrag.utils import logger
from ..utils_api import get_combined_auth_dependency
from .document_routes import check_pipeline_busy_or_raise


class EntityUpdateRequest(BaseModel):
    entity_name: str
    updated_data: Dict[str, Any]
    allow_rename: bool = False
    allow_merge: bool = False


class RelationUpdateRequest(BaseModel):
    source_id: str
    target_id: str
    updated_data: Dict[str, Any]


class EntityMergeRequest(BaseModel):
    entities_to_change: list[str] = Field(
        ...,
        description="Список имён сущностей, которые будут объединены и удалены. Обычно это дубликаты или варианты с опечатками.",
        min_length=1,
        examples=[["Илон Мск", "Иилон Маск"]],
    )
    entity_to_change_into: str = Field(
        ...,
        description="Имя целевой сущности, которая получит все связи исходных сущностей. Эта сущность будет сохранена.",
        min_length=1,
        examples=["Илон Маск"],
    )


class EntityCreateRequest(BaseModel):
    entity_name: str = Field(
        ...,
        description="Уникальное имя новой сущности",
        min_length=1,
        examples=["Tesla"],
    )
    entity_data: Dict[str, Any] = Field(
        ...,
        description="Словарь свойств сущности. Типичные поля: 'description' и 'entity_type'.",
        examples=[
            {
                "description": "Производитель электромобилей",
                "entity_type": "ORGANIZATION",
            }
        ],
    )


class DeleteEntityRequest(BaseModel):
    entity_name: str = Field(..., description="Имя удаляемой сущности.")

    @field_validator("entity_name", mode="after")
    @classmethod
    def validate_entity_name(cls, entity_name: str) -> str:
        if not entity_name or not entity_name.strip():
            raise ValueError("Entity name cannot be empty")
        return entity_name.strip()


class DeleteRelationRequest(BaseModel):
    source_entity: str = Field(..., description="Имя исходной сущности.")
    target_entity: str = Field(..., description="Имя целевой сущности.")

    @field_validator("source_entity", "target_entity", mode="after")
    @classmethod
    def validate_entity_names(cls, entity_name: str) -> str:
        if not entity_name or not entity_name.strip():
            raise ValueError("Entity name cannot be empty")
        return entity_name.strip()


class RelationCreateRequest(BaseModel):
    source_entity: str = Field(
        ...,
        description="Имя исходной сущности. Сущность уже должна существовать в графе знаний.",
        min_length=1,
        examples=["Илон Маск"],
    )
    target_entity: str = Field(
        ...,
        description="Имя целевой сущности. Сущность уже должна существовать в графе знаний.",
        min_length=1,
        examples=["Tesla"],
    )
    relation_data: Dict[str, Any] = Field(
        ...,
        description="Словарь свойств связи. Типичные поля: 'description', 'keywords' и 'weight'.",
        examples=[
            {
                "description": "Илон Маск — генеральный директор Tesla",
                "keywords": "директор, основатель",
                "weight": 1.0,
            }
        ],
    )


def create_graph_routes(rag, api_key: Optional[str] = None):
    # Fresh router per call. A module-level instance would accumulate
    # duplicate routes when the factory is invoked more than once in the
    # same process (e.g. across tests), which triggers FastAPI's
    # "Duplicate Operation ID" warnings.
    router = APIRouter(tags=["graph"])

    combined_auth = get_combined_auth_dependency(api_key)

    @router.get(
        "/graph/label/list",
        dependencies=[Depends(combined_auth)],
        summary="Все метки графа",
    )
    async def get_graph_labels():
        """
        Получить все метки графа

        Returns:
            List[str]: список меток графа
        """
        try:
            return await rag.get_graph_labels()
        except Exception as e:
            logger.error(f"Error getting graph labels: {str(e)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка получения меток графа: {str(e)}"
            )

    @router.get(
        "/graph/label/popular",
        dependencies=[Depends(combined_auth)],
        summary="Популярные метки",
    )
    async def get_popular_labels(
        limit: int = Query(
            300, description="Максимальное количество возвращаемых меток", ge=1, le=1000
        ),
    ):
        """
        Получить популярные метки по количеству связей узла (самые связанные сущности)

        Args:
            limit (int): максимум возвращаемых меток (по умолчанию 300, максимум 1000)

        Returns:
            List[str]: список популярных меток, отсортированных по количеству связей (по убыванию)
        """
        try:
            return await rag.chunk_entity_relation_graph.get_popular_labels(limit)
        except Exception as e:
            logger.error(f"Error getting popular labels: {str(e)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка получения популярных меток: {str(e)}"
            )

    @router.get(
        "/graph/label/search",
        dependencies=[Depends(combined_auth)],
        summary="Поиск меток",
    )
    async def search_labels(
        q: str = Query(..., description="Строка поискового запроса"),
        limit: int = Query(
            50, description="Максимальное количество результатов поиска", ge=1, le=100
        ),
    ):
        """
        Нечёткий поиск по меткам

        Args:
            q (str): строка поискового запроса
            limit (int): максимум результатов (по умолчанию 50, максимум 100)

        Returns:
            List[str]: список подходящих меток, отсортированных по релевантности
        """
        try:
            return await rag.chunk_entity_relation_graph.search_labels(q, limit)
        except Exception as e:
            logger.error(f"Error searching labels with query '{q}': {str(e)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка поиска меток: {str(e)}"
            )

    @router.get(
        "/graphs",
        dependencies=[Depends(combined_auth)],
        summary="Подграф знаний по метке",
    )
    async def get_knowledge_graph(
        label: str = Query(..., description="Метка, для которой строится граф знаний"),
        max_depth: int = Query(3, description="Максимальная глубина графа", ge=1),
        max_nodes: int = Query(1000, description="Максимум возвращаемых узлов", ge=1),
    ):
        """
        Получить связный подграф узлов, метка которых включает указанную метку.
        При сокращении количества узлов приоритеты такие:
            1. Сначала — количество переходов (путь) до начального узла
            2. Затем — количество связей узла

        Args:
            label (str): метка начального узла
            max_depth (int, optional): максимальная глубина подграфа, по умолчанию 3
            max_nodes: максимум возвращаемых узлов

        Returns:
            Dict[str, List[str]]: граф знаний для метки
        """
        try:
            # Log the label parameter to check for leading spaces
            logger.debug(
                f"get_knowledge_graph called with label: '{label}' (length: {len(label)}, repr: {repr(label)})"
            )

            return await rag.get_knowledge_graph(
                node_label=label,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )
        except Exception as e:
            logger.error(f"Error getting knowledge graph for label '{label}': {str(e)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка получения графа знаний: {str(e)}"
            )

    @router.get(
        "/graph/entity/exists",
        dependencies=[Depends(combined_auth)],
        summary="Проверить существование сущности",
    )
    async def check_entity_exists(
        name: str = Query(..., description="Имя проверяемой сущности"),
    ):
        """
        Проверить, существует ли сущность с указанным именем в графе знаний

        Args:
            name (str): имя проверяемой сущности

        Returns:
            Dict[str, bool]: словарь с ключом 'exists' — существует ли сущность
        """
        try:
            exists = await rag.chunk_entity_relation_graph.has_node(name)
            return {"exists": exists}
        except Exception as e:
            logger.error(f"Error checking entity existence for '{name}': {str(e)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка проверки существования сущности: {str(e)}"
            )

    @router.post(
        "/graph/entity/edit",
        dependencies=[Depends(combined_auth)],
        summary="Обновить сущность",
    )
    async def update_entity(request: EntityUpdateRequest):
        """
        Обновить свойства сущности в графе знаний

        Эндпоинт позволяет обновлять свойства сущности, включая переименование.
        При переименовании в уже существующее имя поведение зависит от allow_merge:

        Args:
            request (EntityUpdateRequest): запрос, содержащий:
                - entity_name (str): имя обновляемой сущности
                - updated_data (Dict[str, Any]): словарь обновляемых свойств
                - allow_rename (bool): разрешить переименование (по умолчанию False)
                - allow_merge (bool): объединять с существующей сущностью при конфликте
                                     имён во время переименования (по умолчанию False)

        Returns:
            Dict со следующей структурой:
            {
                "status": "success",
                "message": "Сущность успешно обновлена" | "Сущность успешно объединена с 'имя_цели'",
                "data": {
                    "entity_name": str,        # Итоговое имя сущности
                    "description": str,        # Описание сущности
                    "entity_type": str,        # Тип сущности
                    "source_id": str,          # ID исходных фрагментов
                    ...                        # Прочие свойства сущности
                },
                "operation_summary": {
                    "merged": bool,            # Была ли сущность объединена с другой
                    "merge_status": str,       # "success" | "failed" | "not_attempted"
                    "merge_error": str | None, # Сообщение об ошибке объединения
                    "operation_status": str,   # "success" | "partial_success" | "failure"
                    "target_entity": str | None, # Целевая сущность при переименовании/объединении
                    "final_entity": str,       # Итоговое имя сущности после операции
                    "renamed": bool            # Была ли сущность переименована
                }
            }

        Значения operation_status:
            - "success": все операции выполнены успешно
                * для простых обновлений: свойства сущности обновлены
                * для переименований: сущность успешно переименована
                * для объединений: обновления применены И объединение завершено

            - "partial_success": обновление прошло, но объединение не удалось
                * обновления свойств (кроме имени) применены успешно
                * операция объединения не удалась (сущность не объединена)
                * исходная сущность существует с обновлёнными свойствами
                * детали сбоя — в merge_error

            - "failure": операция полностью не удалась
                * если merge_status == "failed": объединение пытались выполнить, но и обновление, и объединение не удались
                * если merge_status == "not_attempted": не удалось обычное обновление
                * изменения к сущности не применены

        Значения merge_status:
            - "success": сущность успешно объединена с целевой
            - "failed": объединение выполнялось, но не удалось
            - "not_attempted": объединение не выполнялось (обычное обновление/переименование)

        Поведение при переименовании в существующую сущность:
            - allow_merge=False: ValueError со статусом 400 (поведение по умолчанию)
            - allow_merge=True: исходная сущность автоматически объединяется с существующей
                                целевой; связи сохраняются, сначала применяются обновления, не касающиеся имени

        Пример запроса (простое обновление):
            POST /graph/entity/edit
            {
                "entity_name": "Tesla",
                "updated_data": {"description": "Обновлённое описание"},
                "allow_rename": false,
                "allow_merge": false
            }

        Пример ответа (успешное простое обновление):
            {
                "status": "success",
                "message": "Сущность успешно обновлена",
                "data": { ... },
                "operation_summary": {
                    "merged": false,
                    "merge_status": "not_attempted",
                    "merge_error": null,
                    "operation_status": "success",
                    "target_entity": null,
                    "final_entity": "Tesla",
                    "renamed": false
                }
            }

        Пример запроса (переименование с автообъединением):
            POST /graph/entity/edit
            {
                "entity_name": "Илон Мск",
                "updated_data": {
                    "entity_name": "Илон Маск",
                    "description": "Исправленное описание"
                },
                "allow_rename": true,
                "allow_merge": true
            }

        Пример ответа (успешное объединение):
            {
                "status": "success",
                "message": "Сущность успешно объединена с 'Илон Маск'",
                "data": { ... },
                "operation_summary": {
                    "merged": true,
                    "merge_status": "success",
                    "merge_error": null,
                    "operation_status": "success",
                    "target_entity": "Илон Маск",
                    "final_entity": "Илон Маск",
                    "renamed": true
                }
            }

        Пример ответа (частичный успех — обновление прошло, объединение нет):
            {
                "status": "success",
                "message": "Сущность успешно обновлена",
                "data": { ... },  # Данные отражают обновлённую сущность "Илон Мск"
                "operation_summary": {
                    "merged": false,
                    "merge_status": "failed",
                    "merge_error": "Целевая сущность заблокирована другой операцией",
                    "operation_status": "partial_success",
                    "target_entity": "Илон Маск",
                    "final_entity": "Илон Мск",  # Исходная сущность всё ещё существует
                    "renamed": true
                }
            }
        """
        try:
            await check_pipeline_busy_or_raise(rag)
            result = await rag.aedit_entity(
                entity_name=request.entity_name,
                updated_data=request.updated_data,
                allow_rename=request.allow_rename,
                allow_merge=request.allow_merge,
            )

            # Extract operation_summary from result, with fallback for backward compatibility
            operation_summary = result.get(
                "operation_summary",
                {
                    "merged": False,
                    "merge_status": "not_attempted",
                    "merge_error": None,
                    "operation_status": "success",
                    "target_entity": None,
                    "final_entity": request.updated_data.get(
                        "entity_name", request.entity_name
                    ),
                    "renamed": request.updated_data.get(
                        "entity_name", request.entity_name
                    )
                    != request.entity_name,
                },
            )

            # Separate entity data from operation_summary for clean response
            entity_data = dict(result)
            entity_data.pop("operation_summary", None)

            # Generate appropriate response message based on merge status
            response_message = (
                f"Сущность успешно объединена с '{operation_summary['final_entity']}'"
                if operation_summary.get("merged")
                else "Сущность успешно обновлена"
            )
            return {
                "status": "success",
                "message": response_message,
                "data": entity_data,
                "operation_summary": operation_summary,
            }
        except HTTPException:
            raise
        except ValueError as ve:
            logger.error(
                f"Validation error updating entity '{request.entity_name}': {str(ve)}"
            )
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error(f"Error updating entity '{request.entity_name}': {str(e)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка обновления сущности: {str(e)}"
            )

    @router.post(
        "/graph/relation/edit",
        dependencies=[Depends(combined_auth)],
        summary="Обновить связь",
    )
    async def update_relation(request: RelationUpdateRequest):
        """Обновить свойства связи в графе знаний

        Args:
            request (RelationUpdateRequest): запрос с ID источника, ID цели и обновляемыми данными

        Returns:
            Dict: информация об обновлённой связи
        """
        try:
            await check_pipeline_busy_or_raise(rag)
            result = await rag.aedit_relation(
                source_entity=request.source_id,
                target_entity=request.target_id,
                updated_data=request.updated_data,
            )
            return {
                "status": "success",
                "message": "Связь успешно обновлена",
                "data": result,
            }
        except HTTPException:
            raise
        except ValueError as ve:
            logger.error(
                f"Validation error updating relation between '{request.source_id}' and '{request.target_id}': {str(ve)}"
            )
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error(
                f"Error updating relation between '{request.source_id}' and '{request.target_id}': {str(e)}"
            )
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка обновления связи: {str(e)}"
            )

    @router.post(
        "/graph/entity/create",
        dependencies=[Depends(combined_auth)],
        summary="Создать сущность",
    )
    async def create_entity(request: EntityCreateRequest):
        """
        Создать новую сущность в графе знаний

        Эндпоинт создаёт новый узел сущности с указанными свойствами. Система
        автоматически генерирует векторные эмбеддинги сущности для семантического
        поиска и извлечения.

        Тело запроса:
            entity_name (str): уникальное имя сущности
            entity_data (dict): свойства сущности, включая:
                - description (str): текстовое описание сущности
                - entity_type (str): категория/тип сущности (например, PERSON, ORGANIZATION, LOCATION)
                - source_id (str): chunk_id фрагмента, из которого взято описание
                - дополнительные произвольные свойства при необходимости

        Схема ответа:
            {
                "status": "success",
                "message": "Сущность 'Tesla' успешно создана",
                "data": {
                    "entity_name": "Tesla",
                    "description": "Производитель электромобилей",
                    "entity_type": "ORGANIZATION",
                    "source_id": "chunk-123<SEP>chunk-456"
                    ... (прочие свойства сущности)
                }
            }

        Коды состояния HTTP:
            200: сущность успешно создана
            400: некорректный запрос (например, нет обязательных полей, дубликат сущности)
            500: внутренняя ошибка сервера

        Пример запроса:
            POST /graph/entity/create
            {
                "entity_name": "Tesla",
                "entity_data": {
                    "description": "Производитель электромобилей",
                    "entity_type": "ORGANIZATION"
                }
            }
        """
        try:
            await check_pipeline_busy_or_raise(rag)
            # Use the proper acreate_entity method which handles:
            # - Graph lock for concurrency
            # - Vector embedding creation in entities_vdb
            # - Metadata population and defaults
            # - Index consistency via _edit_entity_done
            result = await rag.acreate_entity(
                entity_name=request.entity_name,
                entity_data=request.entity_data,
            )

            return {
                "status": "success",
                "message": f"Сущность '{request.entity_name}' успешно создана",
                "data": result,
            }
        except HTTPException:
            raise
        except ValueError as ve:
            logger.error(
                f"Validation error creating entity '{request.entity_name}': {str(ve)}"
            )
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error(f"Error creating entity '{request.entity_name}': {str(e)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка создания сущности: {str(e)}"
            )

    @router.post(
        "/graph/relation/create",
        dependencies=[Depends(combined_auth)],
        summary="Создать связь",
    )
    async def create_relation(request: RelationCreateRequest):
        """
        Создать новую связь между двумя сущностями в графе знаний

        Эндпоинт устанавливает ненаправленную связь между двумя существующими
        сущностями. Порядок источник/цель принимается для удобства, но хранимое
        ребро ненаправленное и может вернуться с переставленными сущностями.
        Обе сущности уже должны существовать в графе знаний. Система автоматически
        генерирует векторные эмбеддинги связи для семантического поиска и обхода графа.

        Предварительные условия:
            - source_entity и target_entity должны существовать в графе знаний
            - если сущностей нет, сначала создайте их через /graph/entity/create

        Тело запроса:
            source_entity (str): имя исходной сущности (начало связи)
            target_entity (str): имя целевой сущности (конец связи)
            relation_data (dict): свойства связи, включая:
                - description (str): текстовое описание связи
                - keywords (str): ключевые слова типа связи через запятую
                - source_id (str): chunk_id фрагмента, из которого взято описание
                - weight (float): сила/важность связи (по умолчанию 1.0)
                - дополнительные произвольные свойства при необходимости

        Схема ответа:
            {
                "status": "success",
                "message": "Связь между 'Илон Маск' и 'Tesla' успешно создана",
                "data": {
                    "src_id": "Илон Маск",
                    "tgt_id": "Tesla",
                    "description": "Илон Маск — генеральный директор Tesla",
                    "keywords": "директор, основатель",
                    "source_id": "chunk-123<SEP>chunk-456"
                    "weight": 1.0,
                    ... (прочие свойства связи)
                }
            }

        Коды состояния HTTP:
            200: связь успешно создана
            400: некорректный запрос (например, сущности отсутствуют, данные неверны, связь-дубликат)
            500: внутренняя ошибка сервера

        Пример запроса:
            POST /graph/relation/create
            {
                "source_entity": "Илон Маск",
                "target_entity": "Tesla",
                "relation_data": {
                    "description": "Илон Маск — генеральный директор Tesla",
                    "keywords": "директор, основатель",
                    "weight": 1.0
                }
            }
        """
        try:
            await check_pipeline_busy_or_raise(rag)
            # Use the proper acreate_relation method which handles:
            # - Graph lock for concurrency
            # - Entity existence validation
            # - Duplicate relation checks
            # - Vector embedding creation in relationships_vdb
            # - Index consistency via _edit_relation_done
            result = await rag.acreate_relation(
                source_entity=request.source_entity,
                target_entity=request.target_entity,
                relation_data=request.relation_data,
            )

            return {
                "status": "success",
                "message": f"Связь между '{request.source_entity}' и '{request.target_entity}' успешно создана",
                "data": result,
            }
        except HTTPException:
            raise
        except ValueError as ve:
            logger.error(
                f"Validation error creating relation between '{request.source_entity}' and '{request.target_entity}': {str(ve)}"
            )
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error(
                f"Error creating relation between '{request.source_entity}' and '{request.target_entity}': {str(e)}"
            )
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка создания связи: {str(e)}"
            )

    @router.post(
        "/graph/entities/merge",
        dependencies=[Depends(combined_auth)],
        summary="Объединить сущности",
    )
    async def merge_entities(request: EntityMergeRequest):
        """
        Объединить несколько сущностей в одну с сохранением всех связей

        Эндпоинт консолидирует дублирующиеся сущности или сущности с опечатками,
        сохраняя структуру графа. Особенно полезен для чистки графа знаний после
        обработки документов и исправления разночтений в именах.

        Что делает операция объединения:
            1. Удаляет указанные исходные сущности из графа знаний
            2. Переносит все связи исходных сущностей на целевую
            3. Аккуратно объединяет дублирующиеся связи (если у нескольких исходных сущностей одинаковая связь)
            4. Обновляет векторные эмбеддинги для точного поиска
            5. Сохраняет полную структуру и связность графа
            6. Сохраняет свойства и метаданные связей

        Сценарии использования:
            - исправление опечаток в именах сущностей (например, «Илон Мск» → «Илон Маск»)
            - консолидация дубликатов, обнаруженных после обработки документов
            - объединение вариантов имён (например, «НН», «Нижний Новгород»)
            - чистка графа знаний для более быстрых запросов
            - стандартизация имён сущностей по базе знаний

        Тело запроса:
            entities_to_change (list[str]): имена сущностей для объединения и удаления
            entity_to_change_into (str): целевая сущность, получающая все связи

        Схема ответа:
            {
                "status": "success",
                "message": "Успешно объединено 2 сущностей в 'Илон Маск'",
                "data": {
                    "merged_entity": "Илон Маск",
                    "deleted_entities": ["Илон Мск", "Иилон Маск"],
                    "relationships_transferred": 15,
                    ... (детали операции объединения)
                }
            }

        Коды состояния HTTP:
            200: сущности успешно объединены
            400: некорректный запрос (например, пустой список, целевая сущность не существует)
            500: внутренняя ошибка сервера

        Пример запроса:
            POST /graph/entities/merge
            {
                "entities_to_change": ["Илон Мск", "Иилон Маск"],
                "entity_to_change_into": "Илон Маск"
            }

        Note:
            - целевая сущность (entity_to_change_into) должна существовать в графе знаний
            - исходные сущности после объединения удаляются безвозвратно
            - операцию нельзя отменить — проверяйте имена перед объединением
        """
        try:
            await check_pipeline_busy_or_raise(rag)
            result = await rag.amerge_entities(
                source_entities=request.entities_to_change,
                target_entity=request.entity_to_change_into,
            )
            return {
                "status": "success",
                "message": f"Успешно объединено {len(request.entities_to_change)} сущностей в '{request.entity_to_change_into}'",
                "data": result,
            }
        except HTTPException:
            raise
        except ValueError as ve:
            logger.error(
                f"Validation error merging entities {request.entities_to_change} into '{request.entity_to_change_into}': {str(ve)}"
            )
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error(
                f"Error merging entities {request.entities_to_change} into '{request.entity_to_change_into}': {str(e)}"
            )
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Ошибка объединения сущностей: {str(e)}"
            )

    @router.delete(
        "/graph/entity/delete",
        response_model=DeletionResult,
        dependencies=[Depends(combined_auth)],
        summary="Удалить сущность",
    )
    async def delete_entity(request: DeleteEntityRequest):
        """
        Удалить сущность и все её связи из графа знаний.

        Args:
            request (DeleteEntityRequest): тело запроса с именем сущности.

        Returns:
            DeletionResult: объект с результатом операции удаления.

        Raises:
            HTTPException: если сущность не найдена (404) или произошла ошибка (500).
        """
        try:
            await check_pipeline_busy_or_raise(rag)
            result = await rag.adelete_by_entity(entity_name=request.entity_name)
            if result.status == "not_found":
                raise HTTPException(status_code=404, detail=result.message)
            if result.status == "fail":
                raise HTTPException(status_code=500, detail=result.message)
            # Set doc_id to empty string since this is an entity operation, not document
            result.doc_id = ""
            return result
        except HTTPException:
            raise
        except Exception as e:
            error_msg = f"Ошибка удаления сущности '{request.entity_name}': {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=500, detail=error_msg)

    @router.delete(
        "/graph/relation/delete",
        response_model=DeletionResult,
        dependencies=[Depends(combined_auth)],
        summary="Удалить связь",
    )
    async def delete_relation(request: DeleteRelationRequest):
        """
        Удалить связь между двумя сущностями из графа знаний.

        Args:
            request (DeleteRelationRequest): тело запроса с именами исходной и целевой сущностей.

        Returns:
            DeletionResult: объект с результатом операции удаления.

        Raises:
            HTTPException: если связь не найдена (404) или произошла ошибка (500).
        """
        try:
            await check_pipeline_busy_or_raise(rag)
            result = await rag.adelete_by_relation(
                source_entity=request.source_entity,
                target_entity=request.target_entity,
            )
            if result.status == "not_found":
                raise HTTPException(status_code=404, detail=result.message)
            if result.status == "fail":
                raise HTTPException(status_code=500, detail=result.message)
            # Set doc_id to empty string since this is a relation operation, not document
            result.doc_id = ""
            return result
        except HTTPException:
            raise
        except Exception as e:
            error_msg = f"Ошибка удаления связи между '{request.source_entity}' и '{request.target_entity}': {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=500, detail=error_msg)

    return router
