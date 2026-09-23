"""Хранилище соответствий: шифрование, TTL, требования многопроцессного режима."""

from __future__ import annotations

import base64
import os

import pytest

from pdguard.core.store import (
    Cipher,
    MappingRecord,
    MemoryStore,
    SharedKeyRequired,
    _safe_decode,
)

RECORD = MappingRecord(
    original="Клиент Иванов Иван Иванович",
    masked="Клиент И. И. И.",
    mapping={"И. И. И.": "Иванов Иван Иванович"},
    system_id="loadtest",
)


@pytest.mark.asyncio
async def test_roundtrip_through_encrypted_store() -> None:
    store = MemoryStore(Cipher(), ttl_seconds=60)
    await store.put("p-1", RECORD)
    restored = await store.get("p-1")
    assert restored is not None
    assert restored.original == RECORD.original
    assert restored.mapping == RECORD.mapping


@pytest.mark.asyncio
async def test_expired_record_disappears() -> None:
    store = MemoryStore(Cipher(), ttl_seconds=-1)
    await store.put("p-2", RECORD)
    assert await store.get("p-2") is None


@pytest.mark.asyncio
async def test_lru_eviction_bounds_memory() -> None:
    store = MemoryStore(Cipher(), ttl_seconds=60, max_items=3)
    for index in range(5):
        await store.put(f"p-{index}", RECORD)
    assert store.size() == 3
    assert await store.get("p-0") is None
    assert await store.get("p-4") is not None


def test_stored_blob_is_not_plaintext() -> None:
    cipher = Cipher()
    if not cipher.enabled:
        pytest.skip("cryptography недоступна")
    blob = cipher.encrypt(RECORD.to_json())
    assert b"\xd0\x98\xd0\xb2\xd0\xb0\xd0\xbd\xd0\xbe\xd0\xb2" not in blob  # «Иванов»
    assert cipher.decrypt(blob) == RECORD.to_json()


def test_shared_store_refuses_ephemeral_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Регрессия: у каждого воркера был свой ключ, и демаскирование падало.

    Дефект проявлялся только при нескольких процессах: запись, созданную
    одним воркером, другой не мог расшифровать (InvalidTag), и запрос
    завершался ошибкой 500.
    """
    monkeypatch.delenv("PDGUARD_STORE_KEY", raising=False)
    with pytest.raises(SharedKeyRequired):
        Cipher(allow_ephemeral=False)


def test_shared_store_accepts_explicit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PDGUARD_STORE_KEY", key)
    assert Cipher(allow_ephemeral=False).enabled


def test_same_key_decrypts_across_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    """Общий ключ — это и есть условие работы нескольких воркеров."""
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PDGUARD_STORE_KEY", key)
    worker_a, worker_b = Cipher(), Cipher()
    if not worker_a.enabled:
        pytest.skip("cryptography недоступна")
    assert worker_b.decrypt(worker_a.encrypt(RECORD.to_json())) == RECORD.to_json()


def test_unreadable_record_degrades_to_missing() -> None:
    """Чужая или битая запись не должна ронять запрос."""
    cipher = Cipher()
    if not cipher.enabled:
        pytest.skip("cryptography недоступна")
    foreign = Cipher(key=os.urandom(32))
    assert _safe_decode(cipher, foreign.encrypt(RECORD.to_json()), "p-x") is None
