import pytest


async def test_root_endpoint(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["message"] == "Agentic Research API"


async def test_health_endpoint(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["database"] is True
    assert data["redis"] is True
    assert data["status"] == "ok"