"""Deterministic API tests.

Real MongoDB/PostgreSQL are replaced with in-memory fakes via FastAPI
dependency overrides, and httpx's ASGITransport does not run the app
lifespan, so no network connections are attempted.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from bson import ObjectId

from src.core.database import get_pg_pool, get_telemetry_collection
from src.main import app

ENDPOINT = "/api/v1/telemetry"


# --------------------------------------------------------------------------- #
# In-memory fakes
# --------------------------------------------------------------------------- #
class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, key: str, direction: int) -> "FakeCursor":
        self._docs = sorted(self._docs, key=lambda d: d[key], reverse=direction < 0)
        return self

    def limit(self, n: int) -> "FakeCursor":
        self._docs = self._docs[:n]
        return self

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def gen() -> AsyncIterator[dict[str, Any]]:
            for doc in self._docs:
                yield dict(doc)

        return gen()


class FakeInsertResult:
    def __init__(self, inserted_id: ObjectId) -> None:
        self.inserted_id = inserted_id


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertResult:
        doc = {"_id": ObjectId(), **document}
        self.docs.append(doc)
        return FakeInsertResult(doc["_id"])

    def find(self, query: dict[str, Any]) -> FakeCursor:
        matches = [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]
        return FakeCursor(matches)


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConnection()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[FakeConnection]:
        yield self.conn


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_collection() -> FakeCollection:
    return FakeCollection()


@pytest.fixture
def fake_pool() -> FakePool:
    return FakePool()


@pytest.fixture
async def client(
    fake_collection: FakeCollection, fake_pool: FakePool
) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_telemetry_collection] = lambda: fake_collection
    app.dependency_overrides[get_pg_pool] = lambda: fake_pool
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def test_post_valid_payload_returns_200(
    client: httpx.AsyncClient, fake_collection: FakeCollection, fake_pool: FakePool
) -> None:
    payload = {"device_id": "sensor-001", "curve_points": [20.5, 21.0, -40.0, 100.25]}

    response = await client.post(ENDPOINT, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == "sensor-001"
    assert body["points_received"] == 4
    assert len(fake_collection.docs) == 1
    assert fake_collection.docs[0]["curve_points"] == payload["curve_points"]
    # CREATE TABLE + INSERT audit row
    assert len(fake_pool.conn.executed) == 2


async def test_post_missing_device_id_returns_422(
    client: httpx.AsyncClient, fake_collection: FakeCollection
) -> None:
    response = await client.post(ENDPOINT, json={"curve_points": [20.5, 21.0]})

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(e["loc"][-1] == "device_id" and e["type"] == "missing" for e in errors)
    assert fake_collection.docs == []


async def test_post_below_absolute_zero_returns_422(
    client: httpx.AsyncClient, fake_collection: FakeCollection
) -> None:
    payload = {"device_id": "sensor-001", "curve_points": [20.0, -300.0, 15.0]}

    response = await client.post(ENDPOINT, json=payload)

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(
        e["loc"][-1] == "curve_points" and "below absolute zero" in e["msg"] for e in errors
    )
    assert fake_collection.docs == []


async def test_get_unknown_device_returns_empty_list(client: httpx.AsyncClient) -> None:
    response = await client.get(ENDPOINT, params={"device_id": "does-not-exist"})

    assert response.status_code == 200
    assert response.json() == []
