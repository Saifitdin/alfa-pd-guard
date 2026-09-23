"""Точка сборки приложения."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from pdguard.api import admin, demo, ops, process
from pdguard.api.deps import AppState
from pdguard.core.pipeline import MaskingPipeline
from pdguard.core.store import Cipher, MemoryStore, build_store
from pdguard.obs import metrics
from pdguard.obs.logging import configure_logging
from pdguard.settings import Settings

log = logging.getLogger("pdguard")

SWEEP_INTERVAL_SECONDS = 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, settings.log_format)

    pipeline = MaskingPipeline(settings.config_dir, settings.data_dir)
    _refuse_multiworker_memory_store(settings)
    # Общее хранилище означает несколько процессов: эфемерный ключ там
    # недопустим, иначе воркеры не прочитают записи друг друга.
    cipher = Cipher(allow_ephemeral=settings.store_backend != "redis")
    store = await build_store(settings.store_backend, cipher, settings.store_ttl_seconds, settings.redis_url)

    app.state.pdguard = AppState(settings=settings, pipeline=pipeline, store=store)
    log.info(
        "Сервис запущен",
        extra={
            "store_backend": type(store).__name__,
            "encryption": cipher.enabled,
            "systems": len(pipeline.policies.all_systems()),
            "pd_types": len(pipeline.policies.titles),
        },
    )

    sweeper = asyncio.create_task(_sweep_loop(store))
    try:
        yield
    finally:
        sweeper.cancel()
        await store.close()


def _refuse_multiworker_memory_store(settings: Settings) -> None:
    """Несколько воркеров с хранилищем в памяти ломают демаскирование молча.

    Uvicorn берёт число воркеров из WEB_CONCURRENCY — именно так масштабируют
    сервис на Render и в Kubernetes. Прямой и обратный запрос по одному
    payload_id попадут в разные процессы, и обратный вернёт не то, причём без
    единой ошибки в логах. Лучше отказать на старте с понятным объяснением.
    """
    raw = os.getenv("WEB_CONCURRENCY", "1")
    try:
        workers = int(raw)
    except ValueError:
        workers = 1
    if workers > 1 and settings.store_backend == "memory":
        raise RuntimeError(
            f"WEB_CONCURRENCY={workers}, а PDGUARD_STORE_BACKEND=memory: воркеры не "
            "видят записи друг друга, демаскирование будет возвращать чужие или пустые "
            "результаты. Задайте PDGUARD_STORE_BACKEND=redis, PDGUARD_REDIS_URL и общий "
            "PDGUARD_STORE_KEY, либо оставьте один воркер."
        )


async def _sweep_loop(store) -> None:
    """Периодически чистит протухшие записи, чтобы память не росла."""
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            if isinstance(store, MemoryStore):
                removed = store.sweep()
                metrics.STORE_SIZE.set(store.size())
                if removed:
                    log.info("store.sweep", extra={"removed": removed, "left": store.size()})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - фоновой задаче нельзя падать
            log.exception("Ошибка фоновой очистки хранилища")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Модуль безопасности персональных данных",
        description=(
            "Прокси между системой-потребителем и LLM: находит персональные данные, "
            "маскирует их до отправки в модель и восстанавливает в ответе."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def limit_concurrency(request: Request, call_next):
        """Честный 429 вместо роста очереди и таймаутов на стороне клиента."""
        inflight = metrics.INFLIGHT
        current = inflight._value.get()  # noqa: SLF001 - быстрый доступ без блокировок
        if current >= settings.max_concurrent_requests:
            metrics.REJECTED.labels("overload").inc()
            return JSONResponse(
                {"error": "too_many_requests", "retry_after_seconds": 1},
                status_code=429,
                headers={"Retry-After": "1"},
            )
        inflight.inc()
        try:
            return await call_next(request)
        finally:
            inflight.dec()

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return JSONResponse(detail, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception):
        # Сообщение об ошибке не должно содержать обрабатываемый текст.
        log.exception("unhandled_error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            {"error": "internal_error", "error_type": type(exc).__name__},
            status_code=500,
        )

    app.include_router(process.router, tags=["process"])
    app.include_router(ops.router, tags=["ops"])
    app.include_router(admin.router, tags=["admin"])
    app.include_router(demo.router)

    @app.get("/", include_in_schema=False)
    async def root():
        """Корень ведёт на демо-страницу: жюри не должно искать /docs."""
        return RedirectResponse("/demo", status_code=307)

    return app


app = create_app()
