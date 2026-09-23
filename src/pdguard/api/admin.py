"""Управление конфигурацией без перезапуска сервиса.

Демонстрирует критерий «гибкая настройка»: список систем, их состояние,
перечень типов ПД и горячая перезагрузка конфигурации со словарями.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from pdguard.api.deps import get_state

log = logging.getLogger("pdguard.admin")
router = APIRouter(prefix="/admin")


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("/systems", summary="Системы-потребители и их политики")
def list_systems(request: Request) -> dict[str, object]:
    state = get_state(request)
    return {
        "systems": [
            {
                "id": policy.id,
                "name": policy.name,
                "enabled": policy.enabled,
                "masking_strategy": policy.masking_strategy,
                "demasking_enabled": policy.demasking_enabled,
                "min_confidence": policy.min_confidence,
                "pd_types": "all" if policy.all_types else sorted(policy.pd_types),
                "combination_rules": {k: list(v) for k, v in policy.combination_rules.items()},
            }
            for policy in state.pipeline.policies.all_systems()
        ]
    }


@router.get("/pd-types", summary="Справочник типов ПД")
def list_pd_types(request: Request) -> dict[str, object]:
    state = get_state(request)
    policies = state.pipeline.policies
    return {
        "types": [
            {
                "id": pd_type,
                "title": title,
                "partial_rule": {
                    "head": policies.partial_rules.get(pd_type).head if pd_type in policies.partial_rules else 0,
                    "tail": policies.partial_rules.get(pd_type).tail if pd_type in policies.partial_rules else 0,
                },
            }
            for pd_type, title in sorted(policies.titles.items())
        ],
        "custom_patterns": [pattern.pd_type for pattern in policies.custom_patterns],
    }


@router.post("/systems/{system_id}/toggle", summary="Включить/отключить систему")
def toggle_system(system_id: str, body: ToggleRequest, request: Request) -> dict[str, object]:
    """Меняет доступ системы в рантайме.

    Изменение живёт до перезагрузки конфигурации; постоянное — правкой
    systems.yaml и вызовом /admin/reload.
    """
    state = get_state(request)
    policy = state.pipeline.policies.by_id(system_id)
    if policy is None:
        raise HTTPException(404, {"error": "unknown_system", "system_id": system_id})
    policy.enabled = body.enabled
    log.info("system.toggle", extra={"system_id": system_id, "enabled": body.enabled})
    return {"system_id": system_id, "enabled": policy.enabled}


@router.post("/reload", summary="Перечитать конфигурацию и словари")
def reload_config(request: Request) -> dict[str, object]:
    state = get_state(request)
    state.pipeline.reload()
    log.info("config.reload")
    return {
        "status": "reloaded",
        "systems": len(state.pipeline.policies.all_systems()),
        "pd_types": len(state.pipeline.policies.titles),
    }
