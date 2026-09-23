"""Эндпоинты обработки текста.

* ``POST /process``      — контракт хакатона: один вход на маскирование и
                           демаскирование, направление определяется по payload_id.
* ``POST /v1/mask``      — явное маскирование с подробным отчётом (для жюри).
* ``POST /v1/demask``    — явное обратное преобразование.
* ``POST /v1/llm/chat``  — демонстрация полной цепочки
                           система-потребитель → модуль → LLM → демаскирование.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from pdguard.api.deps import AppState, get_state, resolve_policy
from pdguard.core.pipeline import estimate_tokens
from pdguard.core.policy import SystemPolicy
from pdguard.core.store import MappingRecord
from pdguard.llm import call_llm
from pdguard.obs import metrics

log = logging.getLogger("pdguard.process")
router = APIRouter()


class ProcessRequest(BaseModel):
    payload: str = Field(..., description="Строка для обработки")
    payload_id: str = Field(..., description="Идентификатор корреляции маскирование→демаскирование")


class ProcessResponse(BaseModel):
    result: str


class MaskRequest(BaseModel):
    text: str
    payload_id: str | None = None


class EntityReport(BaseModel):
    type: str
    title: str
    start: int
    end: int
    length: int
    confidence: float
    detector: str


class MaskResponse(BaseModel):
    payload_id: str
    masked_text: str
    detected: list[EntityReport]
    masked_types: list[str]
    strategy: str
    latency_ms: float


class DemaskRequest(BaseModel):
    text: str
    payload_id: str


class ChatRequest(BaseModel):
    prompt: str
    payload_id: str | None = None
    demask_response: bool = True


def _record_metrics(
    policy: SystemPolicy, direction: str, status: str, started: float, tokens: int
) -> float:
    elapsed = time.perf_counter() - started
    metrics.REQUESTS.labels(policy.id, direction, status).inc()
    metrics.LATENCY.labels(direction).observe(elapsed)
    metrics.TOKENS.labels(direction).inc(tokens)
    metrics.ROLLING.observe(elapsed, tokens)
    return elapsed * 1000


def _guard_payload_size(state: AppState, text: str) -> None:
    limit = state.settings.max_payload_chars
    if len(text) > limit:
        metrics.REJECTED.labels("payload_too_large").inc()
        raise HTTPException(
            413, {"error": "payload_too_large", "limit_chars": limit, "received_chars": len(text)}
        )


@router.post("/process", response_model=ProcessResponse, summary="Контракт хакатона")
async def process(
    body: ProcessRequest,
    request: Request,
    policy: SystemPolicy = Depends(resolve_policy),
) -> ProcessResponse:
    """Маскирует при первом обращении с данным payload_id, демаскирует при втором.

    Эндпоинт идемпотентен: повторный прямой запрос (та же исходная строка с тем
    же payload_id) снова вернёт маску, а не обработает её как обратный вызов.
    Это важно, потому что проверяющая система делает до двух ретраев.
    """
    state = get_state(request)
    started = time.perf_counter()
    _guard_payload_size(state, body.payload)
    tokens = estimate_tokens(body.payload)

    record = await state.store.get(body.payload_id)

    if record is not None:
        if body.payload == record.masked:
            if not policy.demasking_enabled:
                metrics.REJECTED.labels("demasking_disabled").inc()
                raise HTTPException(
                    403, {"error": "demasking_disabled", "system_id": policy.id}
                )
            result = state.pipeline.demask(body.payload, record)
            latency_ms = _record_metrics(policy, "demask", "ok", started, tokens)
            log.info(
                "demask",
                extra={
                    "payload_id": body.payload_id,
                    "system_id": policy.id,
                    "latency_ms": round(latency_ms, 3),
                    "restored_chars": len(result),
                },
            )
            return ProcessResponse(result=result)

        if body.payload == record.original:
            # Ретрай прямого запроса — отдаём ту же маску.
            latency_ms = _record_metrics(policy, "mask", "idempotent", started, tokens)
            log.info(
                "mask.retry",
                extra={
                    "payload_id": body.payload_id,
                    "system_id": policy.id,
                    "latency_ms": round(latency_ms, 3),
                },
            )
            return ProcessResponse(result=record.masked)

    outcome = state.pipeline.mask(body.payload, policy)
    await state.store.put(
        body.payload_id,
        MappingRecord(
            original=body.payload,
            masked=outcome.masked_text,
            mapping=outcome.mapping,
            system_id=policy.id,
        ),
    )
    for pd_type in outcome.masked_types:
        metrics.ENTITIES.labels(pd_type).inc()
    metrics.STORE_SIZE.set(state.store.size())

    latency_ms = _record_metrics(policy, "mask", "ok", started, tokens)
    log.info(
        "mask",
        extra={
            "payload_id": body.payload_id,
            "system_id": policy.id,
            "strategy": policy.masking_strategy,
            "pd_types": outcome.masked_types,
            "entities": len(outcome.entities),
            "chars": len(body.payload),
            "tokens_est": tokens,
            "latency_ms": round(latency_ms, 3),
        },
    )
    return ProcessResponse(result=outcome.masked_text)


@router.post("/v1/mask", response_model=MaskResponse, summary="Маскирование с отчётом")
async def mask(
    body: MaskRequest,
    request: Request,
    policy: SystemPolicy = Depends(resolve_policy),
) -> MaskResponse:
    """То же маскирование, но с разбором: что именно найдено и с какой уверенностью."""
    state = get_state(request)
    started = time.perf_counter()
    _guard_payload_size(state, body.text)

    payload_id = body.payload_id or uuid.uuid4().hex
    outcome = state.pipeline.mask(body.text, policy)
    await state.store.put(
        payload_id,
        MappingRecord(body.text, outcome.masked_text, outcome.mapping, policy.id),
    )
    for pd_type in outcome.masked_types:
        metrics.ENTITIES.labels(pd_type).inc()
    metrics.STORE_SIZE.set(state.store.size())

    titles = state.pipeline.policies.titles
    latency_ms = _record_metrics(policy, "mask", "ok", started, estimate_tokens(body.text))
    log.info(
        "mask.explicit",
        extra={
            "payload_id": payload_id,
            "system_id": policy.id,
            "pd_types": outcome.masked_types,
            "entities": len(outcome.entities),
            "latency_ms": round(latency_ms, 3),
        },
    )
    return MaskResponse(
        payload_id=payload_id,
        masked_text=outcome.masked_text,
        detected=[
            EntityReport(
                type=entity.type,
                title=titles.get(entity.type, entity.type),
                start=entity.start,
                end=entity.end,
                length=entity.length,
                confidence=round(entity.confidence, 2),
                detector=entity.detector,
            )
            for entity in outcome.entities
        ],
        masked_types=outcome.masked_types,
        strategy=policy.masking_strategy,
        latency_ms=round(latency_ms, 3),
    )


@router.post("/v1/demask", response_model=ProcessResponse, summary="Обратное преобразование")
async def demask(
    body: DemaskRequest,
    request: Request,
    policy: SystemPolicy = Depends(resolve_policy),
) -> ProcessResponse:
    state = get_state(request)
    started = time.perf_counter()

    if not policy.demasking_enabled:
        metrics.REJECTED.labels("demasking_disabled").inc()
        raise HTTPException(403, {"error": "demasking_disabled", "system_id": policy.id})

    record = await state.store.get(body.payload_id)
    if record is None:
        metrics.REJECTED.labels("unknown_payload_id").inc()
        raise HTTPException(
            404,
            {
                "error": "unknown_payload_id",
                "payload_id": body.payload_id,
                "hint": "Запись могла истечь по TTL или была создана другим процессом",
            },
        )

    result = state.pipeline.demask(body.text, record)
    latency_ms = _record_metrics(policy, "demask", "ok", started, estimate_tokens(body.text))
    log.info(
        "demask.explicit",
        extra={
            "payload_id": body.payload_id,
            "system_id": policy.id,
            "latency_ms": round(latency_ms, 3),
        },
    )
    return ProcessResponse(result=result)


@router.post("/v1/llm/chat", summary="Демо полной цепочки с LLM")
async def llm_chat(
    body: ChatRequest,
    request: Request,
    response: Response,
    policy: SystemPolicy = Depends(resolve_policy),
    system: str | None = Query(
        None,
        description="Демо: id системы-потребителя вместо заголовка X-API-Key "
        "(alfagen-chat — токены, loadtest — частичная маска, analytics-sandbox — синтетика)",
    ),
) -> dict[str, object]:
    """Система-потребитель → модуль → LLM → модуль → потребитель.

    Наружу уходит только замаскированный промпт; в ответе показано, что именно
    получила модель — это и есть проверка «ни один фрагмент ПД не ушёл в LLM».
    """
    state = get_state(request)
    if system:
        chosen = state.pipeline.policies.by_id(system)
        if chosen is None or not chosen.enabled:
            raise HTTPException(403, {"error": "unknown_or_disabled_system", "system_id": system})
        policy = chosen
    started = time.perf_counter()
    _guard_payload_size(state, body.prompt)

    payload_id = body.payload_id or uuid.uuid4().hex
    outcome = state.pipeline.mask(body.prompt, policy)
    await state.store.put(
        payload_id,
        MappingRecord(body.prompt, outcome.masked_text, outcome.mapping, policy.id),
    )

    llm_answer, llm_meta = await call_llm(outcome.masked_text)

    final_answer = llm_answer
    if body.demask_response and policy.demasking_enabled:
        record = await state.store.get(payload_id)
        if record is not None:
            final_answer = state.pipeline.demask(llm_answer, record)

    latency_ms = _record_metrics(policy, "llm_chain", "ok", started, estimate_tokens(body.prompt))
    response.headers["X-PDGuard-Latency-Ms"] = f"{latency_ms:.3f}"
    log.info(
        "llm.chain",
        extra={
            "payload_id": payload_id,
            "system_id": policy.id,
            "pd_types": outcome.masked_types,
            "llm_provider": llm_meta.get("provider"),
            "latency_ms": round(latency_ms, 3),
        },
    )
    return {
        "payload_id": payload_id,
        "prompt_sent_to_llm": outcome.masked_text,
        "detected_pd_types": outcome.masked_types,
        "llm_answer_raw": llm_answer,
        "answer": final_answer,
        "llm": llm_meta,
        "latency_ms": round(latency_ms, 3),
    }
