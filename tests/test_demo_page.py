"""Демо-страница: доступна, самодостаточна, корень ведёт на неё."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pdguard.main import create_app
from pdguard.settings import Settings

from conftest import ROOT


@pytest.fixture
def client() -> TestClient:
    settings = Settings(config_dir=ROOT / "config", data_dir=ROOT / "data", log_format="plain")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_demo_page_is_served(client: TestClient) -> None:
    response = client.get("/demo")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Замаскировать" in response.text
    # Никаких внешних скриптов и стилей: страница работает в закрытом контуре.
    assert "<script src=" not in response.text
    assert 'rel="stylesheet"' not in response.text


def test_root_redirects_to_demo(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/demo"


def test_demo_page_not_in_openapi(client: TestClient) -> None:
    assert "/demo" not in client.get("/openapi.json").json()["paths"]
