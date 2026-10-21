"""API routes for telemetry ingestion and retrieval."""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorCollection

from src.core.database import get_pg_pool, get_telemetry_collection
from src.models.schemas import TelemetryPayload

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("", status_code=status.HTTP_200_OK)
async def ingest_telemetry(
    payload: TelemetryPayload,
    collection: AsyncIOMotorCollection = Depends(get_telemetry_collection),
    pg_pool: asyncpg.Pool = Depends(get_pg_pool),
) -> dict[str, Any]:
    """Ingest a validated telemetry payload into MongoDB and record metadata in PostgreSQL."""
    document = payload.model_dump()
    result = await collection.insert_one(document)

    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_audit (
                id SERIAL PRIMARY KEY,
                device_id VARCHAR(64) NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                point_count INTEGER NOT NULL
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO telemetry_audit (device_id, recorded_at, point_count)
            VALUES ($1, $2, $3)
            """,
            payload.device_id,
            payload.timestamp,
            len(payload.curve_points),
        )

    return {
        "status": "created",
        "id": str(result.inserted_id),
        "device_id": payload.device_id,
        "timestamp": payload.timestamp,
        "points_received": len(payload.curve_points),
    }


@router.get("")
async def list_telemetry(
    device_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    collection: AsyncIOMotorCollection = Depends(get_telemetry_collection),
) -> list[dict[str, Any]]:
    """Retrieve recent telemetry events, optionally filtered by device_id."""
    query: dict[str, Any] = {"device_id": device_id} if device_id else {}

    cursor = collection.find(query).sort("timestamp", -1).limit(limit)
    items: list[dict[str, Any]] = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        items.append(doc)

    return items
