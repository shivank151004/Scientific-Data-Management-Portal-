"""Database connection management for PostgreSQL (asyncpg) and MongoDB (motor)."""

import logging
from typing import Optional

import asyncpg
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from src.core.config import settings

logger = logging.getLogger(__name__)

# DDL for the PostgreSQL identity / metadata layer. Executed atomically at startup.
POSTGRES_SCHEMA_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id          SERIAL PRIMARY KEY,
        username    VARCHAR(32)  NOT NULL UNIQUE,
        email       VARCHAR(254) NOT NULL UNIQUE,
        full_name   VARCHAR(128),
        created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS experiment_metadata (
        id               SERIAL PRIMARY KEY,
        user_id          INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        experiment_name  VARCHAR(128) NOT NULL,
        device_id        VARCHAR(64)  NOT NULL,
        description      TEXT,
        created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS experiment_metadata_user_id_idx ON experiment_metadata (user_id)",
    """
    CREATE TABLE IF NOT EXISTS telemetry_audit (
        id           SERIAL PRIMARY KEY,
        device_id    VARCHAR(64) NOT NULL,
        recorded_at  TIMESTAMPTZ NOT NULL,
        point_count  INTEGER     NOT NULL
    )
    """,
)


class Database:
    """Holds shared database clients for the application lifetime."""

    pg_pool: Optional[asyncpg.Pool] = None
    mongo_client: Optional[AsyncIOMotorClient] = None


db = Database()


async def init_postgres_schema(pool: asyncpg.Pool) -> None:
    """Create the users / experiment_metadata / audit tables inside a single transaction.

    If any statement fails the whole transaction is rolled back, so the schema is
    never left half-created.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            for statement in POSTGRES_SCHEMA_DDL:
                await conn.execute(statement)
    logger.info("PostgreSQL schema (users, experiment_metadata, telemetry_audit) ensured")


async def connect_to_databases() -> None:
    """Initialise the PostgreSQL pool and MongoDB client, and ensure schema/indexes."""
    try:
        db.pg_pool = await asyncpg.create_pool(
            dsn=settings.POSTGRES_URI,
            min_size=settings.POSTGRES_POOL_MIN_SIZE,
            max_size=settings.POSTGRES_POOL_MAX_SIZE,
        )
        logger.info("PostgreSQL connection pool created")
        await init_postgres_schema(db.pg_pool)

        db.mongo_client = AsyncIOMotorClient(
            settings.MONGO_URI,
            serverSelectionTimeoutMS=5000,
            tz_aware=True,
        )
        # Force a round-trip so connection problems surface at startup.
        await db.mongo_client.admin.command("ping")
        logger.info("MongoDB client connected")

        # Compound index for efficient per-device, time-ordered queries.
        await get_telemetry_collection().create_index(
            [("device_id", ASCENDING), ("timestamp", DESCENDING)],
            name="device_id_timestamp_idx",
        )
        logger.info("MongoDB compound index (device_id, timestamp) ensured")
    except (asyncpg.PostgresError, PyMongoError, OSError) as exc:
        # Log without the URIs to avoid leaking credentials.
        logger.error("Database initialisation failed: %s", type(exc).__name__)
        await close_database_connections()
        raise RuntimeError("Failed to initialise database connections") from exc


async def close_database_connections() -> None:
    """Gracefully close all database connections."""
    if db.pg_pool is not None:
        await db.pg_pool.close()
        db.pg_pool = None
        logger.info("PostgreSQL connection pool closed")
    if db.mongo_client is not None:
        db.mongo_client.close()
        db.mongo_client = None
        logger.info("MongoDB client closed")


def get_pg_pool() -> asyncpg.Pool:
    if db.pg_pool is None:
        raise RuntimeError("PostgreSQL pool is not initialised")
    return db.pg_pool


def get_telemetry_collection() -> AsyncIOMotorCollection:
    if db.mongo_client is None:
        raise RuntimeError("MongoDB client is not initialised")
    return db.mongo_client[settings.MONGO_DB_NAME][settings.MONGO_COLLECTION]
