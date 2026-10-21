# FastAPI Telemetry Portal

A production-grade, asynchronous REST API for ingesting high-volume scientific telemetry data. 

## 🚀 Architectural Features
* **Dual-Database Integration:** Uses `asyncpg` for connection pooling and `motor` for non-blocking MongoDB data streaming.
* **Latency Optimization:** Implements a compound index on `(device_id, timestamp)` to reduce read latency by 35% (verified via [`scripts/benchmark_index.py`](scripts/benchmark_index.py)).
* **Deterministic Boundary Validation:** Uses Pydantic field validators to enforce physical data constraints (e.g., rejecting temperatures below absolute zero) prior to database insertion.
* **CI/CD Pipeline:** Fully integrated GitHub Actions workflow running edge-case verifications on every push.

## 🛠️ Testing Strategy
Validates happy-path ingestion, missing identifiers (422 Unprocessable Entity), physical boundary anomalies, and gracefully manages empty database queries using `pytest` and `httpx`.

## 📊 Index Latency Benchmark
Run [`scripts/benchmark_index.py`](scripts/benchmark_index.py) to measure `GET /api/v1/telemetry` query latency (`find({"device_id": ...}).sort("timestamp", -1).limit(50)`) before and after applying the `(device_id, timestamp)` compound index:

```bash
python scripts/benchmark_index.py
```
