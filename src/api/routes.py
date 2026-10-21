"""API routes for telemetry ingestion/retrieval/aggregation and user identities."""

from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from motor.motor_asyncio import AsyncIOMotorCollection

from src.core.database import get_pg_pool, get_telemetry_collection
from src.models.schemas import (
    DEVICE_ID_PATTERN,
    TelemetryPayload,
    TelemetrySummary,
    UserCreate,
    UserResponse,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
users_router = APIRouter(prefix="/users", tags=["users"])


# --------------------------------------------------------------------------- #
# Telemetry
# --------------------------------------------------------------------------- #
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


def build_summary_pipeline(
    device_id: str, start: datetime | None, end: datetime | None
) -> list[dict[str, Any]]:
    """Build the aggregation pipeline for per-device curve-point statistics.

    The leading ``$match`` on ``device_id`` (+ optional ``timestamp`` range) is a
    prefix of the ``(device_id, timestamp)`` compound index, so MongoDB can use an
    index scan instead of a collection scan. Per-document min/max/sum/size are
    computed with array operators in ``$project`` (avoiding the document blow-up of
    ``$unwind`` on 10k-point curves), then combined in ``$group``. The mean is a
    point-weighted mean: total sum / total point count.
    """
    match: dict[str, Any] = {"device_id": device_id}
    if start is not None or end is not None:
        ts_range: dict[str, datetime] = {}
        if start is not None:
            ts_range["$gte"] = start
        if end is not None:
            ts_range["$lte"] = end
        match["timestamp"] = ts_range

    return [
        {"$match": match},
        {
            "$project": {
                "_id": 0,
                "doc_min": {"$min": "$curve_points"},
                "doc_max": {"$max": "$curve_points"},
                "doc_sum": {"$sum": "$curve_points"},
                "doc_count": {"$size": "$curve_points"},
            }
        },
        {
            "$group": {
                "_id": None,
                "readings": {"$sum": 1},
                "point_count": {"$sum": "$doc_count"},
                "min": {"$min": "$doc_min"},
                "max": {"$max": "$doc_max"},
                "total": {"$sum": "$doc_sum"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "readings": 1,
                "point_count": 1,
                "min": 1,
                "max": 1,
                "mean": {"$divide": ["$total", "$point_count"]},
            }
        },
    ]


@router.get("/{device_id}/summary", response_model=TelemetrySummary)
async def telemetry_summary(
    device_id: str = Path(..., pattern=DEVICE_ID_PATTERN),
    start: datetime | None = Query(None, description="Inclusive lower bound on timestamp"),
    end: datetime | None = Query(None, description="Inclusive upper bound on timestamp"),
    collection: AsyncIOMotorCollection = Depends(get_telemetry_collection),
) -> TelemetrySummary:
    """Compute min / max / mean across all curve points for a device via aggregation."""
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start must be earlier than or equal to end",
        )

    pipeline = build_summary_pipeline(device_id, start, end)
    results = await collection.aggregate(pipeline).to_list(length=1)

    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No telemetry found for device '{device_id}'",
        )

    return TelemetrySummary(device_id=device_id, **results[0])


# --------------------------------------------------------------------------- #
# Users (PostgreSQL transactional identity layer)
# --------------------------------------------------------------------------- #
@users_router.post("", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
async def create_user(
    payload: UserCreate,
    pg_pool: asyncpg.Pool = Depends(get_pg_pool),
) -> UserResponse:
    """Create a user (and optional experiment metadata) atomically in one transaction."""
    try:
        async with pg_pool.acquire() as conn:
            async with conn.transaction():
                user_row = await conn.fetchrow(
                    """
                    INSERT INTO users (username, email, full_name)
                    VALUES ($1, $2, $3)
                    RETURNING id, username, email, full_name, created_at
                    """,
                    payload.username,
                    payload.email,
                    payload.full_name,
                )

                experiment_id: int | None = None
                if payload.experiment is not None:
                    experiment_row = await conn.fetchrow(
                        """
                        INSERT INTO experiment_metadata
                            (user_id, experiment_name, device_id, description)
                        VALUES ($1, $2, $3, $4)
                        RETURNING id
                        """,
                        user_row["id"],
                        payload.experiment.experiment_name,
                        payload.experiment.device_id,
                        payload.experiment.description,
                    )
                    experiment_id = experiment_row["id"]
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this username or email already exists",
        ) from exc

    return UserResponse(**dict(user_row), experiment_id=experiment_id)
