"""Pydantic request/response schemas for the telemetry API."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

ABSOLUTE_ZERO_CELSIUS = -273.15


class TelemetryPayload(BaseModel):
    """Incoming telemetry reading from a device."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$",
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
