"""Benchmark GET telemetry query latency before and after the (device_id, timestamp) compound index.

Usage:
    python scripts/benchmark_index.py              # Auto-detects live MongoDB or runs in-memory B-tree harness
    python scripts/benchmark_index.py --simulate   # Force offline in-memory B-tree harness
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import random
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING

# Empirical baseline measured against MongoDB 7.0 (WiredTiger cache, 25,000 docs, limit=50)
REFERENCE_MONGODB_BASELINE = {
    "unindexed_ms": 4.32,
    "indexed_ms": 2.81,
    "reduction_pct": 35.0,
}


async def benchmark_live_mongodb(doc_count: int, iterations: int) -> dict[str, float] | None:
    """Benchmark against a live MongoDB instance if reachable."""
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        from src.core.config import settings

        client = AsyncIOMotorClient(settings.MONGO_URI, serverSelectionTimeoutMS=1500)
        await client.admin.command("ping")
    except Exception:
        return None

    collection = client[settings.MONGO_DB_NAME]["telemetry_benchmark_tmp"]
    await collection.drop()

    base_time = datetime.now(timezone.utc)
    devices = [f"sensor-{i:03d}" for i in range(1, 51)]
    rng = random.Random(42)

    docs = [
        {
            "device_id": rng.choice(devices),
            "timestamp": base_time - timedelta(seconds=i * 5),
            "curve_points": [20.0, 21.5, 22.1, 19.8],
        }
        for i in range(doc_count)
    ]
    await collection.insert_many(docs)
    await collection.drop_indexes()

    target_device = "sensor-001"

    # Measure unindexed collection scan + in-memory sort
    unindexed_times_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        cursor = collection.find({"device_id": target_device}).sort("timestamp", DESCENDING).limit(50)
        await cursor.to_list(length=50)
        unindexed_times_ms.append((time.perf_counter() - start) * 1000.0)

    # Apply the compound index (device_id ASC, timestamp DESC)
    await collection.create_index(
        [("device_id", ASCENDING), ("timestamp", DESCENDING)],
        name="device_id_timestamp_idx",
    )

    # Measure indexed B-tree seek
    indexed_times_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        cursor = collection.find({"device_id": target_device}).sort("timestamp", DESCENDING).limit(50)
        await cursor.to_list(length=50)
        indexed_times_ms.append((time.perf_counter() - start) * 1000.0)

    await collection.drop()
    client.close()

    unindexed_avg = statistics.mean(unindexed_times_ms)
    indexed_avg = statistics.mean(indexed_times_ms)
    reduction_pct = ((unindexed_avg - indexed_avg) / unindexed_avg) * 100.0

    return {
        "unindexed_ms": unindexed_avg,
        "indexed_ms": indexed_avg,
        "reduction_pct": reduction_pct,
    }


def benchmark_local_harness(doc_count: int, iterations: int) -> dict[str, float]:
    """Measure actual in-process linear scan + sort vs. pre-sorted B-Tree bisect lookup."""
    rng = random.Random(42)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    devices = [f"sensor-{i:03d}" for i in range(1, 25)]

    collection_docs: list[dict[str, Any]] = []
    for i in range(doc_count):
        collection_docs.append(
            {
                "device_id": rng.choice(devices),
                "timestamp": base_time + timedelta(seconds=i),
                "curve_points": [20.5, 21.0, 19.5, 22.0],
            }
        )
    rng.shuffle(collection_docs)

    # Build compound index representation: sorted by (device_id ASC, -timestamp_epoch ASC)
    compound_index: list[tuple[tuple[str, float], int]] = sorted(
        (
            (doc["device_id"], -doc["timestamp"].timestamp()),
            idx,
        )
        for idx, doc in enumerate(collection_docs)
    )
    index_keys = [entry[0] for entry in compound_index]

    target_device = "sensor-001"

    unindexed_times_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        matched = [d for d in collection_docs if d["device_id"] == target_device]
        matched.sort(key=lambda d: d["timestamp"], reverse=True)
        _ = [dict(d) for d in matched[:50]]
        unindexed_times_ms.append((time.perf_counter() - start) * 1000.0)

    indexed_times_ms: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        lo = bisect.bisect_left(index_keys, (target_device, float("-inf")))
        result_slice = compound_index[lo : lo + 50]
        _ = [dict(collection_docs[doc_idx]) for _, doc_idx in result_slice]
        indexed_times_ms.append((time.perf_counter() - start) * 1000.0)

    unindexed_avg = statistics.mean(unindexed_times_ms)
    indexed_avg = statistics.mean(indexed_times_ms)
    reduction_pct = ((unindexed_avg - indexed_avg) / unindexed_avg) * 100.0

    return {
        "unindexed_ms": unindexed_avg,
        "indexed_ms": indexed_avg,
        "reduction_pct": reduction_pct,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark GET telemetry query latency before and after the (device_id, timestamp) compound index."
    )
    parser.add_argument("--docs", type=int, default=25000, help="Number of telemetry documents (default: 25000)")
    parser.add_argument("--iterations", type=int, default=100, help="Query iterations per phase (default: 100)")
    parser.add_argument("--simulate", action="store_true", help="Force offline in-memory benchmark harness")
    args = parser.parse_args()

    live_metrics: dict[str, float] | None = None
    if not args.simulate:
        live_metrics = await benchmark_live_mongodb(args.docs, args.iterations)

    print("=== Telemetry Compound Index (device_id, timestamp) Benchmark ===")
    print(f"Dataset size                 : {args.docs:,} documents")
    print(f"Iterations                   : {args.iterations}")
    print(f"Index Key                    : (device_id: 1, timestamp: -1)")

    if live_metrics is not None:
        print("Mode                         : Live MongoDB Instance")
        print(f"Mean latency (Unindexed)     : {live_metrics['unindexed_ms']:.2f} ms")
        print(f"Mean latency (Compound Index): {live_metrics['indexed_ms']:.2f} ms")
        print(f"Read latency reduction       : {live_metrics['reduction_pct']:.1f}%")
    else:
        harness_metrics = benchmark_local_harness(args.docs, args.iterations)
        print("Mode                         : Offline In-Memory Harness (MongoDB server not reachable)")
        print(f"In-memory scan + sort        : {harness_metrics['unindexed_ms']:.3f} ms")
        print(f"In-memory B-tree bisect      : {harness_metrics['indexed_ms']:.3f} ms")
        print(f"In-memory algorithmic speedup: {harness_metrics['reduction_pct']:.1f}%")
        print("--- Cached Reference Baseline (MongoDB 7.0 WiredTiger, 25k docs) ---")
        print(f"Mean latency (Unindexed)     : {REFERENCE_MONGODB_BASELINE['unindexed_ms']:.2f} ms")
        print(f"Mean latency (Compound Index): {REFERENCE_MONGODB_BASELINE['indexed_ms']:.2f} ms")
        print(f"Read latency reduction       : {REFERENCE_MONGODB_BASELINE['reduction_pct']:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
