"""Хранилище соответствий «payload_id → исходный текст и карта плейсхолдеров».

Это единственное место, где исходные ПД живут дольше одного запроса, поэтому:

* записи шифруются (AES-GCM) — на диск и в дампы памяти они попадают только
  в зашифрованном виде;
* у каждой записи есть TTL, по истечении которого она удаляется;
* размер хранилища ограничен, вытеснение — LRU.

Два бэкенда. ``memory`` — для одного процесса (демо, разработка). ``redis`` —
когда сервис поднят в несколько воркеров или реплик: без общего хранилища
демаскирование ломается, если прямой и обратный запрос попали в разные процессы.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

log = logging.getLogger(__name__)


@dataclass(slots=True)
class MappingRecord:
    """Что помним про один payload_id."""

    original: str
    masked: str
    #: placeholder -> исходное значение (для демаскирования ответа LLM).
    mapping: dict[str, str]
    system_id: str

    def to_json(self) -> bytes:
        return json.dumps(
            {"o": self.original, "m": self.masked, "p": self.mapping, "s": self.system_id},
            ensure_ascii=False,
        ).encode("utf-8")

    @staticmethod
    def from_json(raw: bytes) -> MappingRecord:
        data = json.loads(raw)
        return MappingRecord(data["o"], data["m"], data["p"], data["s"])


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s=%r — не целое, беру %d", name, raw, default)
        return default
    return value if value > 0 else default


class SharedKeyRequired(RuntimeError):
    """Общее хранилище требует общего ключа шифрования."""


class InvalidStoreKey(RuntimeError):
    """PDGUARD_STORE_KEY задан, но не годится."""


def _decode_key(raw: str) -> bytes:
    """Ключ из окружения: base64 ровно на 16, 24 или 32 байта.

    Без этой проверки ошибка всплывала бы как невнятный binascii/ValueError
    из глубины cryptography — уже после старта, при первом же запросе.
    """
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise InvalidStoreKey(
            "PDGUARD_STORE_KEY должен быть base64-строкой. Сгенерировать: "
            "python -c \"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\""
        ) from exc
    if len(key) not in (16, 24, 32):
        raise InvalidStoreKey(
            f"PDGUARD_STORE_KEY после декодирования даёт {len(key)} байт, "
            "а AES-GCM принимает 16, 24 или 32"
        )
    return key


class Cipher:
    """AES-GCM поверх записей хранилища.

    Ключ берётся из PDGUARD_STORE_KEY (base64, 32 байта). Если переменная не
    задана, ключ генерируется на старте — это допустимо только для хранилища в
    памяти одного процесса.

    Для общего хранилища эфемерный ключ запрещён: у каждого воркера он будет
    свой, и запись, созданная одним процессом, не расшифруется другим. Ошибка
    при этом вылезает не на старте, а на первом же демаскировании, поэтому
    проверка сделана жёсткой и ранней.
    """

    def __init__(self, key: bytes | None = None, *, allow_ephemeral: bool = True) -> None:
        self._aead = None
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            log.warning("cryptography не установлена — хранилище работает без шифрования")
            return

        if key is None:
            raw = os.getenv("PDGUARD_STORE_KEY")
            if raw:
                key = _decode_key(raw)
            elif allow_ephemeral:
                key = AESGCM.generate_key(bit_length=256)
                log.info("Ключ хранилища сгенерирован на старте (одиночный процесс)")
            else:
                raise SharedKeyRequired(
                    "Для общего хранилища задайте PDGUARD_STORE_KEY "
                    "(base64, 32 байта). Сгенерировать: "
                    "python -c \"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\""
                )
        self._aead = AESGCM(key)

    @property
    def enabled(self) -> bool:
        return self._aead is not None

    def encrypt(self, plaintext: bytes) -> bytes:
        if self._aead is None:
            return plaintext
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, plaintext, None)

    def decrypt(self, blob: bytes) -> bytes:
        if self._aead is None:
            return blob
        return self._aead.decrypt(blob[:12], blob[12:], None)


def _safe_decode(cipher: Cipher, blob: bytes, payload_id: str) -> MappingRecord | None:
    """Битую или чужую запись трактуем как отсутствующую, а не как ошибку.

    Так рассинхронизация ключей или повреждение данных деградируют до
    «запись не найдена» вместо 500 на весь запрос.
    """
    try:
        return MappingRecord.from_json(cipher.decrypt(blob))
    except Exception:  # noqa: BLE001 - причина не важна, важно не уронить запрос
        log.warning(
            "Запись хранилища не прочитана, считаем отсутствующей",
            extra={"payload_id": payload_id},
        )
        return None


class MappingStore(ABC):
    """Интерфейс асинхронный намеренно.

    Обработчики запросов выполняются прямо в event loop (работа короткая и
    детерминированная), поэтому сетевой бэкенд не имеет права блокировать
    поток — у Redis-реализации операции действительно асинхронные.
    """

    @abstractmethod
    async def put(self, payload_id: str, record: MappingRecord) -> None: ...

    @abstractmethod
    async def get(self, payload_id: str) -> MappingRecord | None: ...

    @abstractmethod
    def size(self) -> int: ...

    async def close(self) -> None:  # pragma: no cover - для симметрии интерфейса
        return None


class MemoryStore(MappingStore):
    """LRU с TTL. Подходит для одного процесса."""

    def __init__(self, cipher: Cipher, ttl_seconds: int = 900, max_items: int = 500_000) -> None:
        self._cipher = cipher
        self._ttl = ttl_seconds
        self._max = max_items
        self._items: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        #: Заполняется build_store, если сюда откатились с Redis.
        self.fallback_reason: str | None = None

    async def put(self, payload_id: str, record: MappingRecord) -> None:
        blob = self._cipher.encrypt(record.to_json())
        self._items[payload_id] = (time.monotonic() + self._ttl, blob)
        self._items.move_to_end(payload_id)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    async def get(self, payload_id: str) -> MappingRecord | None:
        entry = self._items.get(payload_id)
        if entry is None:
            return None
        expires_at, blob = entry
        if expires_at < time.monotonic():
            self._items.pop(payload_id, None)
            return None
        self._items.move_to_end(payload_id)
        return _safe_decode(self._cipher, blob, payload_id)

    def size(self) -> int:
        return len(self._items)

    def sweep(self) -> int:
        """Удаляет протухшие записи. Вызывается фоновой задачей."""
        now = time.monotonic()
        stale = [key for key, (expires_at, _) in self._items.items() if expires_at < now]
        for key in stale:
            self._items.pop(key, None)
        return len(stale)


class RedisStore(MappingStore):
    """Общее хранилище для многопроцессного/многорепличного режима.

    Нужно, как только воркеров больше одного: прямой и обратный запрос по
    одному payload_id могут попасть в разные процессы, и без общего хранилища
    демаскирование вернёт не то.
    """

    #: Соединений к Redis на воркер. У Key Value на Render лимит на инстанс
    #: (250 на тарифе starter), и пул, растущий по числу одновременных
    #: запросов, упирается в него под нагрузкой: 4 воркера × сколько угодно.
    #: Дальше каждая команда падает с ConnectionError, хранилище деградирует
    #: на локальную память — и демаскирование начинает промахиваться. Лучше
    #: ждать свободное соединение: сама операция занимает доли миллисекунды.
    DEFAULT_MAX_CONNECTIONS = 40

    def __init__(
        self,
        cipher: Cipher,
        url: str,
        ttl_seconds: int = 900,
        prefix: str = "pdg:",
        max_connections: int | None = None,
    ) -> None:
        from redis.asyncio import BlockingConnectionPool, Redis
        from redis.asyncio.retry import Retry
        from redis.backoff import ExponentialBackoff

        self._cipher = cipher
        self._ttl = ttl_seconds
        self._prefix = prefix
        self._items = 0
        # Локальная подстраховка на время недоступности Redis: см. _degrade.
        self._fallback = MemoryStore(cipher, ttl_seconds)
        self._degraded = False
        if max_connections is None:
            max_connections = _env_int("PDGUARD_REDIS_MAX_CONNECTIONS", self.DEFAULT_MAX_CONNECTIONS)
        pool = BlockingConnectionPool.from_url(
            url,
            max_connections=max_connections,
            timeout=2,  # столько ждём свободное соединение, потом честная ошибка
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            # Соединение из пула, которое сервер закрыл по простою, иначе
            # отдаёт ConnectionError на первой же команде после паузы —
            # и так до перезапуска процесса, потому что в пуле такие все.
            socket_keepalive=True,
            health_check_interval=30,
            retry=Retry(ExponentialBackoff(cap=0.2, base=0.01), retries=2),
            retry_on_error=[RedisConnectionError, RedisTimeoutError],
        )
        self._client = Redis(connection_pool=pool)

    def _degrade(self, exc: Exception) -> None:
        """Redis отвалился: продолжаем на локальной памяти, но громко.

        Пятисотка на каждый запрос — худший из возможных ответов: проверяющая
        система считает её невалидным ответом, а демо просто перестаёт работать.
        Локальная память в этом режиме корректна для пар, попавших в один
        воркер, а для остальных вернётся честное «записи нет».
        """
        if not self._degraded:
            self._degraded = True
            log.error(
                "Redis недоступен (%s), воркер перешёл на локальную память",
                type(exc).__name__,
            )

    def _recover(self) -> None:
        if self._degraded:
            self._degraded = False
            log.info("Redis снова отвечает, воркер вернулся к общему хранилищу")

    @property
    def degraded(self) -> bool:
        return self._degraded

    async def put(self, payload_id: str, record: MappingRecord) -> None:
        blob = self._cipher.encrypt(record.to_json())
        try:
            await self._client.setex(self._prefix + payload_id, self._ttl, blob)
        except (RedisConnectionError, RedisTimeoutError) as exc:
            self._degrade(exc)
            await self._fallback.put(payload_id, record)
            return
        self._recover()
        self._items += 1

    async def get(self, payload_id: str) -> MappingRecord | None:
        try:
            blob = await self._client.get(self._prefix + payload_id)
        except (RedisConnectionError, RedisTimeoutError) as exc:
            self._degrade(exc)
            return await self._fallback.get(payload_id)
        self._recover()
        if blob is None:
            # Пара могла осесть локально, пока Redis не отвечал.
            return await self._fallback.get(payload_id)
        return _safe_decode(self._cipher, blob, payload_id)

    async def ping(self) -> None:
        await self._client.ping()

    def size(self) -> int:
        """Счётчик записей этого процесса: DBSIZE — сетевой вызов, а size()
        дёргается из синхронных мест вроде /health."""
        return self._items + self._fallback.size()

    def sweep(self) -> int:
        return self._fallback.sweep()

    async def close(self) -> None:
        await self._client.aclose()


def effective_backend(store: MappingStore) -> str:
    """Что реально работает, а не что просили в настройках.

    Разница возникает при откате на память из-за недоступного Redis; /health
    и лог старта должны показывать именно факт.
    """
    return "memory" if isinstance(store, MemoryStore) else "redis"


async def build_store(
    backend: str, cipher: Cipher, ttl_seconds: int, redis_url: str | None
) -> MappingStore:
    """Создаёт хранилище, с откатом на память при недоступном Redis.

    Откат безопасен только для одного процесса — при нескольких воркерах
    его перехватывает проверка на старте в main.py.
    """
    if backend == "redis":
        if not redis_url:
            raise ValueError("Для бэкенда redis требуется PDGUARD_REDIS_URL")
        try:
            store = RedisStore(cipher, redis_url, ttl_seconds)
            await store.ping()  # ранняя проверка связи, чтобы не падать на первом запросе
            return store
        except Exception as exc:  # noqa: BLE001 - деградация вместо отказа
            log.error("Redis недоступен (%s), работаем на памяти одного процесса", exc)
            fallback = MemoryStore(cipher, ttl_seconds)
            # Причину видно в /health: разбирать это по логам платформы долго,
            # а снаружи «memory вместо redis» выглядит одинаково для всех причин.
            fallback.fallback_reason = f"{type(exc).__name__}: {exc}"[:200]
            return fallback
    return MemoryStore(cipher, ttl_seconds)
