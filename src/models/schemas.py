"""Pydantic request/response schemas for the telemetry API."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

ABSOLUTE_ZERO_CELSIUS = -273.15
DEVICE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$"


class TelemetryPayload(BaseModel):
    """Incoming multi-variable telemetry reading from a scientific device."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(
        ...,
        pattern=DEVICE_ID_PATTERN,
        description="Alphanumeric device ID (3-64 chars; '-' and '_' allowed after the first char)",
        examples=["sensor-001"],
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Reading timestamp (UTC)",
    )
    curve_points: list[float] = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Temperature curve points in degrees Celsius",
    )
    unit: str = Field(
        default="K",
        min_length=1,
        max_length=16,
        description="Measurement unit for the curve series (e.g. 'K', 'C')",
    )
    parameters: dict[str, float | str] = Field(
        default_factory=dict,
        description="Multi-variable experimental parameters (e.g. pressure_kpa, sample_rate_hz, channel)",
    )

    @field_validator("curve_points")
    @classmethod
    def validate_above_absolute_zero(cls, value: list[float]) -> list[float]:
        for index, point in enumerate(value):
            if point < ABSOLUTE_ZERO_CELSIUS:
                raise ValueError(
                    f"curve_points[{index}] = {point} is below absolute zero "
                    f"({ABSOLUTE_ZERO_CELSIUS} °C)"
                )
        return value


class TelemetrySummary(BaseModel):
    """Aggregated statistics across all curve points for a device."""

    device_id: str
    readings: int = Field(..., description="Number of telemetry documents aggregated")
    point_count: int = Field(..., description="Total number of curve points aggregated")
    min: float
    max: float
    mean: float


# --------------------------------------------------------------------------- #
# PostgreSQL identity layer
# --------------------------------------------------------------------------- #
class ExperimentMetadataCreate(BaseModel):
    """Optional experiment registered atomically alongside a new user."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    experiment_name: str = Field(..., min_length=1, max_length=128)
    device_id: str = Field(..., pattern=DEVICE_ID_PATTERN)
    description: str | None = Field(default=None, max_length=2000)


class UserCreate(BaseModel):
    """Request body for creating a structured user identity."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{3,32}$", examples=["shivank"])
    email: str = Field(
        ...,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        examples=["researcher@example.com"],
    )
    full_name: str | None = Field(default=None, max_length=128)
    experiment: ExperimentMetadataCreate | None = None

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        return value.lower()


class UserResponse(BaseModel):
    """Persisted user identity returned by the API."""

    id: int
    username: str
    email: str
    full_name: str | None
    created_at: datetime
    experiment_id: int | None = None
