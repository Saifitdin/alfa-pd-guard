"""Контракт POST /process и поведение API под проверяющей системой."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pdguard.main import create_app
from pdguard.settings import Settings

from conftest import ROOT

SAMPLE = "Клиент Иванов Иван Иванович, паспорт 4509 123456, карта 4276 3800 1234 5679"


@pytest.fixture
def client() -> TestClient:
    settings = Settings(config_dir=ROOT / "config", data_dir=ROOT / "data", log_format="plain")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_mask_then_demask_roundtrip(client: TestClient) -> None:
    """Прямой и обратный проход по одному payload_id."""
    masked = client.post("/process", json={"payload": SAMPLE, "payload_id": "pair-1"})
    assert masked.status_code == 200
    mask_text = masked.json()["result"]
    assert mask_text != SAMPLE
    assert "Иванов Иван Иванович" not in mask_text
    assert "4509 123456" not in mask_text

    restored = client.post("/process", json={"payload": mask_text, "payload_id": "pair-1"})
    assert restored.status_code == 200
    assert restored.json()["result"] == SAMPLE


def test_direct_request_is_idempotent(client: TestClient) -> None:
    """Ретрай прямого запроса обязан вернуть ту же маску, а не демаскировать.

    Проверяющая система делает до двух ретраев — без этого повтор прямого
    запроса был бы принят за обратный вызов.
    """
    first = client.post("/process", json={"payload": SAMPLE, "payload_id": "idem-1"}).json()
    second = client.post("/process", json={"payload": SAMPLE, "payload_id": "idem-1"}).json()
    assert first["result"] == second["result"]
    assert SAMPLE not in second["result"]


def test_unknown_payload_id_on_demask_is_treated_as_new_masking(client: TestClient) -> None:
    """Если запись истекла, обратный запрос не должен падать с 5xx."""
    response = client.post("/process", json={"payload": SAMPLE, "payload_id": "fresh-id"})
    assert response.status_code == 200


def test_response_shape_matches_contract(client: TestClient) -> None:
    response = client.post("/process", json={"payload": "тестовая строка", "payload_id": "shape-1"})
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["result"]
    assert isinstance(body["result"], str)


def test_missing_fields_return_422(client: TestClient) -> None:
    assert client.post("/process", json={"payload": "нет id"}).status_code == 422


def test_oversized_payload_is_rejected(client: TestClient) -> None:
    huge = "а" * 700_000
    response = client.post("/process", json={"payload": huge, "payload_id": "big-1"})
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


def test_disabled_system_is_forbidden(client: TestClient) -> None:
    response = client.post(
        "/process",
        json={"payload": SAMPLE, "payload_id": "legacy-1"},
        headers={"X-API-Key": "demo-key-legacy"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "system_disabled"


def test_unknown_system_is_forbidden(client: TestClient) -> None:
    response = client.post(
        "/process",
        json={"payload": SAMPLE, "payload_id": "nope-1"},
        headers={"X-System-Id": "no-such-system"},
    )
    assert response.status_code == 403


def test_demasking_can_be_disabled_per_system(client: TestClient) -> None:
    """Аналитической песочнице демаскирование запрещено политикой."""
    headers = {"X-API-Key": "demo-key-analytics"}
    masked = client.post(
        "/process", json={"payload": SAMPLE, "payload_id": "analytics-1"}, headers=headers
    ).json()["result"]

    response = client.post(
        "/process", json={"payload": masked, "payload_id": "analytics-1"}, headers=headers
    )
    assert response.status_code == 403
    assert response.json()["error"] == "demasking_disabled"


def test_per_system_strategy_differs(client: TestClient) -> None:
    token_mask = client.post(
        "/process",
        json={"payload": SAMPLE, "payload_id": "sys-token"},
        headers={"X-API-Key": "demo-key-alfagen-chat"},
    ).json()["result"]
    partial_mask = client.post(
        "/process",
        json={"payload": SAMPLE, "payload_id": "sys-partial"},
        headers={"X-API-Key": "demo-key-loadtest"},
    ).json()["result"]

    assert "[FIO_1:M]" in token_mask  # токен несёт подсказку рода
    assert "И. И. И." in partial_mask


def test_explicit_mask_endpoint_reports_entities(client: TestClient) -> None:
    response = client.post("/v1/mask", json={"text": SAMPLE})
    assert response.status_code == 200
    body = response.json()
    assert body["masked_types"]
    assert all({"type", "title", "start", "end", "confidence"} <= set(e) for e in body["detected"])
    assert body["latency_ms"] >= 0


def test_llm_chain_does_not_leak(client: TestClient) -> None:
    """В LLM уходит только маска — ключевая проверка жюри."""
    response = client.post("/v1/llm/chat", json={"prompt": SAMPLE})
    assert response.status_code == 200
    body = response.json()
    assert "Иванов Иван Иванович" not in body["prompt_sent_to_llm"]
    assert "4509 123456" not in body["prompt_sent_to_llm"]
    assert "4276 3800 1234 5679" not in body["prompt_sent_to_llm"]


def test_ops_endpoints(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert "pdguard_requests_total" in client.get("/metrics").text
    stats = client.get("/stats").json()
    assert {"rps", "tps", "latency_avg_ms", "latency_p95_ms"} <= set(stats)


def test_admin_endpoints(client: TestClient) -> None:
    systems = client.get("/admin/systems").json()["systems"]
    assert {s["id"] for s in systems} >= {"alfagen-chat", "loadtest", "analytics-sandbox"}

    toggled = client.post("/admin/systems/loadtest/toggle", json={"enabled": False})
    assert toggled.json()["enabled"] is False
    blocked = client.post("/process", json={"payload": "тест", "payload_id": "off-1"})
    assert blocked.status_code == 403

    client.post("/admin/reload")
    assert client.post("/process", json={"payload": "тест", "payload_id": "off-2"}).status_code == 200
