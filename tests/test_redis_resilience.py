"""Redis отвалился на ходу — сервис деградирует, а не отдаёт 500 на всё.

Реальная авария на стенде: Key Value закрыл простаивавшие соединения, каждая
команда из пула стала бросать ConnectionError, и /process начал отвечать
`internal_error` на любой запрос, хотя /health был зелёный.
"""

from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from pdguard.core.store import Cipher, MappingRecord, RedisStore

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class _DeadClient:
    """Клиент, у которого любая команда падает, как при закрытом соединении."""

    def __init__(self) -> None:
        self.calls = 0

    async def setex(self, *_args, **_kwargs):
        self.calls += 1
        raise RedisConnectionError("Connection closed by server")

    async def get(self, *_args, **_kwargs):
        self.calls += 1
        raise RedisConnectionError("Connection closed by server")


class _FlakyClient(_DeadClient):
    """Падает, пока не поднимут флаг: проверяем возврат в нормальный режим."""

    def __init__(self) -> None:
        super().__init__()
        self.alive = False
        self.data: dict[str, bytes] = {}

    async def setex(self, key, _ttl, blob):
        if not self.alive:
            return await super().setex()
        self.data[key] = blob

    async def get(self, key):
        if not self.alive:
            return await super().get()
        return self.data.get(key)


def _store(monkeypatch: pytest.MonkeyPatch, client) -> RedisStore:
    monkeypatch.setenv("PDGUARD_STORE_KEY", KEY)
    store = RedisStore(Cipher(), "redis://127.0.0.1:1/0", ttl_seconds=900)
    store._client = client  # noqa: SLF001 - подменяем сеть, остальная логика настоящая
    return store


def _record() -> MappingRecord:
    return MappingRecord("Иванов Иван", "И. И.", {"[FIO_1]": "Иванов Иван"}, "loadtest")


@pytest.mark.asyncio
async def test_put_and_get_survive_dead_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(monkeypatch, _DeadClient())

    await store.put("p-1", _record())  # не должно бросить
    restored = await store.get("p-1")

    assert store.degraded is True
    assert restored is not None, "пара должна вернуться из локальной подстраховки"
    assert restored.original == "Иванов Иван"


@pytest.mark.asyncio
async def test_unknown_id_in_degraded_mode_is_a_miss_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Чужая пара из другого воркера — честное «нет записи», а не 500 и не мусор."""
    store = _store(monkeypatch, _DeadClient())

    assert await store.get("never-seen") is None
    assert store.degraded is True


@pytest.mark.asyncio
async def test_pairs_written_while_degraded_survive_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Записали в аварии, читаем после восстановления — Redis о паре не знает."""
    client = _FlakyClient()
    store = _store(monkeypatch, client)

    await store.put("p-2", _record())
    assert store.degraded is True

    client.alive = True
    restored = await store.get("p-2")

    assert restored is not None and restored.original == "Иванов Иван"
    assert store.degraded is False, "после успешной команды режим снимается"


@pytest.mark.asyncio
async def test_healthy_redis_does_not_touch_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FlakyClient()
    client.alive = True
    store = _store(monkeypatch, client)

    await store.put("p-3", _record())

    assert store.degraded is False
    assert store._fallback.size() == 0  # noqa: SLF001
    assert await store.get("p-3") is not None


def test_client_is_configured_against_stale_pooled_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Корень аварии: без health-check и ретраев протухшее соединение из пула
    роняет каждую команду до перезапуска процесса."""
    monkeypatch.setenv("PDGUARD_STORE_KEY", KEY)
    store = RedisStore(Cipher(), "redis://127.0.0.1:1/0")
    pool = store._client.connection_pool  # noqa: SLF001

    assert pool.connection_kwargs["health_check_interval"] == 30
    assert pool.connection_kwargs["retry"].get_retries() == 2


def test_connection_pool_is_bounded_and_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без верхней границы пул под нагрузкой выбирает лимит соединений
    инстанса Key Value, и дальше падает каждая команда: на прогоне 8×128 это
    дало 21 % несовпадений при восстановлении."""
    from redis.asyncio import BlockingConnectionPool

    monkeypatch.setenv("PDGUARD_STORE_KEY", KEY)
    store = RedisStore(Cipher(), "redis://127.0.0.1:1/0")
    pool = store._client.connection_pool  # noqa: SLF001

    assert isinstance(pool, BlockingConnectionPool), "переполненный пул должен ждать, а не падать"
    assert pool.max_connections == RedisStore.DEFAULT_MAX_CONNECTIONS
    assert RedisStore.DEFAULT_MAX_CONNECTIONS * 4 < 250, "4 воркера должны влезать в лимит starter"


def test_pool_size_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDGUARD_STORE_KEY", KEY)
    monkeypatch.setenv("PDGUARD_REDIS_MAX_CONNECTIONS", "12")
    store = RedisStore(Cipher(), "redis://127.0.0.1:1/0")

    assert store._client.connection_pool.max_connections == 12  # noqa: SLF001
