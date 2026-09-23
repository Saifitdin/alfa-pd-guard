"""Разделяемое состояние приложения и разрешение политики по запросу."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from pdguard.core.pipeline import MaskingPipeline
from pdguard.core.policy import SystemPolicy
from pdguard.core.store import MappingStore
from pdguard.settings import Settings


@dataclass(slots=True)
class AppState:
    settings: Settings
    pipeline: MaskingPipeline
    store: MappingStore


def get_state(request: Request) -> AppState:
    return request.app.state.pdguard


def resolve_policy(request: Request) -> SystemPolicy:
    """Определяет систему-потребителя по X-API-Key или X-System-Id.

    Список допущенных систем — это и есть «ограниченный список систем, которые
    имеют возможность обращаться в сервис» из ТЗ. Отключённая система получает
    403 и не доходит до обработки текста.
    """
    state = get_state(request)
    policies = state.pipeline.policies

    api_key = request.headers.get("x-api-key")
    policy = policies.by_api_key(api_key)

    if policy is None:
        system_id = request.headers.get("x-system-id")
        if system_id:
            policy = policies.by_id(system_id)
            if policy is None:
                raise HTTPException(403, {"error": "unknown_system", "system_id": system_id})
        elif state.settings.require_api_key:
            raise HTTPException(401, {"error": "api_key_required"})
        else:
            policy = policies.by_id(state.settings.default_system_id)
            if policy is None:
                raise HTTPException(500, {"error": "default_system_not_configured"})

    if not policy.enabled:
        raise HTTPException(403, {"error": "system_disabled", "system_id": policy.id})
    return policy
