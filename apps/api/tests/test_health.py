"""Smoke test — FastAPI app 能起，health endpoint 正常返回。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from resolveai_api.main import app


def test_root() -> None:
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_healthz() -> None:
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
