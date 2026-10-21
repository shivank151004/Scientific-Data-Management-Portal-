"""Shared pytest configuration.

`.env` is git-ignored, so CI has no MONGO_URI / POSTGRES_URI. Provide harmless
placeholders *before* `src.core.config` is imported so Settings() validates.
The tests replace both databases with in-memory fakes, so these URIs are never
connected to. Existing environment variables are left untouched.
"""

import os

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("POSTGRES_URI", "postgresql://user:password@localhost:5432/telemetry_db")
