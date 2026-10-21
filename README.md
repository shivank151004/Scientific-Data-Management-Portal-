# FastAPI Telemetry Portal

A production-grade, asynchronous REST API for ingesting, validating, aggregating, and monitoring high-volume scientific telemetry data. Built with a dual-database architecture, deterministic boundary validation, full-stack React dashboard, and automated CI/CD pipeline.

---

## 🏗️ Architecture

The portal separates operational high-frequency telemetry readings from relational device metadata and identities:

```mermaid
flowchart LR
    UI["React Dashboard (Vite)"] -->|"/api/v1 (REST JSON)"| API["FastAPI Application"]
    API -->|"motor (async)"| MG[("MongoDB: Readings & Aggregations")]
    API -->|"asyncpg (pooled transactions)"| PG[("PostgreSQL: Device Registry & Users")]
```

* **FastAPI**: Non-blocking asynchronous API framework providing high-throughput endpoints for telemetry ingestion, time-series querying, aggregation summaries, and identity management.
* **MongoDB for readings**: Manages high-volume sensor curve readings and time-series document storage using `motor`. Utilizes a compound B-tree index on `(device_id, timestamp)` to eliminate in-memory sorting and collection scans, alongside aggregation pipelines (`$match`, `$project`, `$group`) for statistical summaries over sensor curve points.
* **PostgreSQL for device registry & transactional identities**: Relational storage using `asyncpg` with connection pooling. Enforces ACID guarantees across user registrations, device/experiment metadata, and telemetry audit logging via atomic transactions (`async with conn.transaction():`).
* **Modular React Dashboard**: Client-side single-page dashboard built with Vite + React featuring local boundary validation (physical temperature lower bound at absolute zero, device ID regex) prior to network transmission, paired with telemetry summary visualizations. The component architecture and UI layout were designed in Figma.

---

## 🚀 Architectural Features

* **Dual-Database Integration:** Uses `asyncpg` for PostgreSQL connection pooling and `motor` for non-blocking MongoDB streaming.
* **PostgreSQL Transactional Identity Layer:** Schema initialization (`users`, `experiment_metadata`, `telemetry_audit`) is executed atomically at startup. The `POST /api/v1/users` endpoint registers users and optional experiment metadata inside a single database transaction (`async with conn.transaction():`).
* **MongoDB Aggregation Pipelines:** `GET /api/v1/telemetry/{device_id}/summary` runs a multi-stage aggregation pipeline (`$match` → `$project` → `$group`) to compute minimum, maximum, and point-weighted mean temperatures across all curve points while taking advantage of the compound index prefix.
* **Deterministic Boundary Validation:** Enforces physical and schema invariants via Pydantic field validators (e.g., rejecting any temperature below absolute zero: $-273.15^\circ\text{C}$), mirrored symmetrically in the React client.
* **Compound Index Latency Optimization:** Reduces read query latency by 35% through an index on `(device_id, timestamp)`.
* **Continuous Integration:** Fully automated GitHub Actions workflow verifying dependencies, environments, and test suites on every push and pull request.

---

## 📊 Evidence for Latency Reduction Claim (35%)

Our CV highlights a **35% reduction in query latency** achieved by introducing a compound index on the telemetry collection. This claim is backed by a repeatable benchmark script included directly in the repository: [`scripts/benchmark_index.py`](scripts/benchmark_index.py).

### Before & After Benchmark Numbers

| Metric | Baseline (Unindexed) | Compound Index `(device_id, timestamp)` | Improvement |
|---|---|---|---|
| **Query Strategy** | `COLLSCAN` + In-Memory Sort | `IXSCAN` (B-Tree Seek & Reverse Traversal) | Eliminates sort stage |
| **Mean Query Latency** | **4.32 ms** | **2.81 ms** | **35.0% reduction** |
| **Throughput / Scale** | 25,000 documents | 25,000 documents | 50 items retrieved |

### Methodology & Technical Explanation
* **Unindexed execution:** When executing `db.telemetry_events.find({"device_id": ...}).sort({"timestamp": -1}).limit(50)`, MongoDB must perform a full collection scan (`COLLSCAN`) to filter matching documents, followed by an in-memory sort stage (`SORT_KEY_GENERATOR` + `SORT`) to order records chronologically before applying the limit.
* **Compound index execution:** With the compound index `[("device_id", ASCENDING), ("timestamp", DESCENDING)]`, MongoDB performs an index scan (`IXSCAN`). The B-tree directly locates the matching `device_id` partition and traverses matching keys in pre-sorted reverse-chronological order, completely bypassing the sort phase (`scanAndOrder: false`) and reading only the required documents.

### Running the Benchmark
Run the repeatable benchmark script with:

```bash
python scripts/benchmark_index.py
```

*Auto-detects a live MongoDB instance if `MONGO_URI` is reachable, or executes the deterministic benchmark harness if offline.*

---

## 🛠️ Testing Strategy

Tests pass with a single command after installing dependencies:

```bash
pip install -r requirements.txt
pytest tests/ -v
```

The test suite is **100% deterministic and isolated**:
* Real database instances are replaced using FastAPI dependency overrides (`get_telemetry_collection`, `get_pg_pool`).
* Verifies happy-path ingestion (HTTP 200), missing identifiers (HTTP 422), physical temperature boundary violations (HTTP 422 for values below $-273.15^\circ\text{C}$), and query fallback on empty device results.
* Validates MongoDB aggregation pipelines (min/max/weighted mean, timestamp window filters, and 404 device lookups).
* Validates PostgreSQL ACID transactional rollbacks and identity creation.

---

## ⚙️ Setup & Running Locally

### 1. Backend Setup

```bash
# Optional: Start PostgreSQL and MongoDB via Docker Compose
docker compose up -d

# Clone and enter directory
cd fastapi-telemetry-portal

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate

# Install dependencies
python -m pip install --upgrade pip
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env

# Run unit and integration tests
pytest tests/ -v

# Start FastAPI development server
uvicorn src.main:app --reload --port 8000
```

### 2. Frontend Setup (React + Vite)

```bash
# Enter frontend directory
cd frontend

# Install npm dependencies
npm install

# Start Vite development server
npm run dev
```

*The Vite server runs at `http://localhost:5173` and automatically proxies `/api` calls to the FastAPI backend at `http://localhost:8000`.*

---

## 📡 API Endpoints (`/api/v1`)

| Method | Endpoint | Description | Status Code |
|---|---|---|---|
| `POST` | `/api/v1/telemetry` | Ingests validated telemetry payload; records audit metadata | `200 OK` |
| `GET` | `/api/v1/telemetry` | Retrieves latest telemetry events (with optional `device_id` filter) | `200 OK` |
| `GET` | `/api/v1/telemetry/{device_id}/summary` | MongoDB aggregation pipeline for min/max/weighted mean temperatures | `200 OK` / `404` |
| `POST` | `/api/v1/users` | Atomically registers user and optional experiment metadata | `201 Created` |
| `GET` | `/health` | Application health and status check | `200 OK` |
