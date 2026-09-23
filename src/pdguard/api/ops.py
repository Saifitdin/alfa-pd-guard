"""Эксплуатационные эндпоинты: здоровье, метрики, сводка."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from pdguard.api.deps import get_state
from pdguard.core.store import effective_backend
from pdguard.obs import metrics
from pdguard.settings import worker_count

router = APIRouter()


@router.get("/health", summary="Проба живости")
def health(request: Request) -> dict[str, object]:
    state = get_state(request)
    return {
        "status": "ok",
        # Фактическое хранилище, а не настроенное: откат на память виден снаружи.
        "store_backend": effective_backend(state.store),
        "store_configured": state.settings.store_backend,
        "workers": worker_count(),
        "store_items": state.store.size(),
        "systems": len(state.pipeline.policies.all_systems()),
        "pd_types": len(state.pipeline.policies.titles),
    }


@router.get("/metrics", summary="Метрики Prometheus")
def prometheus() -> Response:
    return Response(metrics.render_prometheus(), media_type="text/plain; version=0.0.4")


@router.get("/stats", summary="Человекочитаемая сводка Latency/RPS/TPS")
def stats(request: Request) -> dict[str, object]:
    """То, что показывается жюри вместо чтения сырых метрик Prometheus."""
    state = get_state(request)
    snapshot = metrics.ROLLING.snapshot()
    snapshot["window_seconds"] = 60
    snapshot["store_items"] = state.store.size()
    return snapshot
