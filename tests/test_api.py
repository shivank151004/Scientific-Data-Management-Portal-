"""Deterministic API tests.

Real MongoDB/PostgreSQL are replaced with in-memory fakes via FastAPI
dependency overrides, and httpx's ASGITransport does not run the app
lifespan, so no network connections are attempted.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from bson import ObjectId

from src.core.database import get_pg_pool, get_telemetry_collection
from src.main import app

ENDPOINT = "/api/v1/telemetry"
USERS_ENDPOINT = "/api/v1/users"


# --------------------------------------------------------------------------- #
# In-memory MongoDB fakes
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

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [dict(d) for d in self._docs[:length]]

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def gen() -> AsyncIterator[dict[str, Any]]:
            for doc in self._docs:
                yield dict(doc)

        return gen()


class FakeInsertResult:
    def __init__(self, inserted_id: ObjectId) -> None:
        self.inserted_id = inserted_id


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for field, cond in query.items():
        value = doc.get(field)
        if isinstance(cond, dict):
            if "$gte" in cond and not value >= cond["$gte"]:
                return False
            if "$lte" in cond and not value <= cond["$lte"]:
                return False
        elif value != cond:
            return False
    return True


def _eval(expr: Any, doc: dict[str, Any]) -> Any:
    """Evaluate the subset of aggregation expressions used by the summary pipeline."""
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if isinstance(expr, dict):
        (op, arg), = expr.items()
        if op in ("$min", "$max", "$sum", "$size"):
            values = _eval(arg, doc)
            return {"$min": min, "$max": max, "$sum": sum, "$size": len}[op](values)
        if op == "$divide":
            numerator, denominator = (_eval(a, doc) for a in arg)
            return numerator / denominator
        raise NotImplementedError(op)
    return expr


def _run_pipeline(docs: list[dict[str, Any]], pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for stage in pipeline:
        (name, spec), = stage.items()
        if name == "$match":
            docs = [d for d in docs if _matches(d, spec)]
        elif name == "$project":
            projected = []
            for d in docs:
                out: dict[str, Any] = {}
                for key, value in spec.items():
                    if key == "_id" and value == 0:
                        continue
                    out[key] = d.get(key) if value == 1 else _eval(value, d)
                projected.append(out)
            docs = projected
        elif name == "$group":
            if not docs:
                return []
            assert spec["_id"] is None, "fake only supports a single global group"
            grouped: dict[str, Any] = {"_id": None}
            for key, acc in spec.items():
                if key == "_id":
                    continue
                (op, arg), = acc.items()
                values = [1 if arg == 1 else _eval(arg, d) for d in docs]
                grouped[key] = {"$sum": sum, "$min": min, "$max": max}[op](values)
            docs = [grouped]
        else:
            raise NotImplementedError(name)
    return docs


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.last_pipeline: list[dict[str, Any]] | None = None

    async def insert_one(self, document: dict[str, Any]) -> FakeInsertResult:
        doc = {"_id": ObjectId(), **document}
        self.docs.append(doc)
        return FakeInsertResult(doc["_id"])

    def find(self, query: dict[str, Any]) -> FakeCursor:
        return FakeCursor([d for d in self.docs if _matches(d, query)])

    def aggregate(self, pipeline: list[dict[str, Any]]) -> FakeCursor:
        self.last_pipeline = pipeline
        return FakeCursor(_run_pipeline(list(self.docs), pipeline))


# --------------------------------------------------------------------------- #
# In-memory PostgreSQL fakes
# --------------------------------------------------------------------------- #
class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.transactions_committed = 0
        self._next_id = {"users": 1, "experiment_metadata": 1}

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        self.executed.append((sql, args))
        if "INSERT INTO users" in sql:
            row_id = self._next_id["users"]
            self._next_id["users"] += 1
            username, email, full_name = args
            return {
                "id": row_id,
                "username": username,
                "email": email,
                "full_name": full_name,
                "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
        if "INSERT INTO experiment_metadata" in sql:
            row_id = self._next_id["experiment_metadata"]
            self._next_id["experiment_metadata"] += 1
            return {"id": row_id}
        raise NotImplementedError(sql)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield
        self.transactions_committed += 1


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
# Telemetry ingestion / retrieval
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
    # Single audit INSERT (schema DDL now runs once at startup, not per request)
    assert len(fake_pool.conn.executed) == 1
    assert "INSERT INTO telemetry_audit" in fake_pool.conn.executed[0][0]


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


# --------------------------------------------------------------------------- #
# Aggregation pipeline
# --------------------------------------------------------------------------- #
async def test_summary_aggregates_min_max_weighted_mean(
    client: httpx.AsyncClient, fake_collection: FakeCollection
) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await fake_collection.insert_one(
        {"device_id": "sensor-001", "timestamp": t0, "curve_points": [10.0, 20.0, 30.0]}
    )
    await fake_collection.insert_one(
        {"device_id": "sensor-001", "timestamp": t0 + timedelta(hours=1), "curve_points": [-50.0]}
    )
    # Different device: must be excluded by $match
    await fake_collection.insert_one(
        {"device_id": "sensor-999", "timestamp": t0, "curve_points": [1000.0]}
    )

    response = await client.get(f"{ENDPOINT}/sensor-001/summary")

    assert response.status_code == 200
    assert response.json() == {
        "device_id": "sensor-001",
        "readings": 2,
        "point_count": 4,
        "min": -50.0,
        "max": 30.0,
        "mean": 2.5,  # (10 + 20 + 30 - 50) / 4 -> point-weighted, not mean-of-means
    }
    # First stage must be a $match on the compound-index prefix
    assert fake_collection.last_pipeline[0] == {"$match": {"device_id": "sensor-001"}}


async def test_summary_respects_time_window(
    client: httpx.AsyncClient, fake_collection: FakeCollection
) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await fake_collection.insert_one(
        {"device_id": "sensor-001", "timestamp": t0, "curve_points": [5.0]}
    )
    await fake_collection.insert_one(
        {"device_id": "sensor-001", "timestamp": t0 + timedelta(days=2), "curve_points": [99.0]}
    )

    response = await client.get(
        f"{ENDPOINT}/sensor-001/summary",
        params={"start": t0.isoformat(), "end": (t0 + timedelta(days=1)).isoformat()},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["readings"] == 1
    assert body["max"] == 5.0
    assert fake_collection.last_pipeline[0]["$match"]["timestamp"] == {
        "$gte": t0,
        "$lte": t0 + timedelta(days=1),
    }


async def test_summary_unknown_device_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{ENDPOINT}/does-not-exist/summary")

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Users (PostgreSQL transactional identity layer)
# --------------------------------------------------------------------------- #
async def test_create_user_with_experiment_is_transactional(
    client: httpx.AsyncClient, fake_pool: FakePool
) -> None:
    payload = {
        "username": "shivank",
        "email": "Researcher@Example.com",
        "full_name": "Shivank Shukla",
        "experiment": {"experiment_name": "Cryo run 1", "device_id": "sensor-001"},
    }

    response = await client.post(USERS_ENDPOINT, json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1
    assert body["email"] == "researcher@example.com"
    assert body["experiment_id"] == 1
    # Both inserts ran inside exactly one committed transaction
    assert fake_pool.conn.transactions_committed == 1
    statements = [sql for sql, _ in fake_pool.conn.executed]
    assert any("INSERT INTO users" in s for s in statements)
    assert any("INSERT INTO experiment_metadata" in s for s in statements)


async def test_create_user_invalid_email_returns_422(
    client: httpx.AsyncClient, fake_pool: FakePool
) -> None:
    response = await client.post(
        USERS_ENDPOINT, json={"username": "shivank", "email": "not-an-email"}
    )

    assert response.status_code == 422
    assert fake_pool.conn.executed == []
