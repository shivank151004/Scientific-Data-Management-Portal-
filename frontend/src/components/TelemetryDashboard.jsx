import { useState } from "react";
import { ApiError, fetchTelemetrySummary, submitTelemetry } from "../api/telemetryClient.js";

// Mirrors the backend Pydantic constraints in src/models/schemas.py so invalid
// input is caught before a request is ever sent.
const DEVICE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$/;
const ABSOLUTE_ZERO_C = -273.15;
const MAX_POINTS = 10000;

export function validateTelemetry(deviceId, rawPoints) {
  const errors = {};

  if (!DEVICE_ID_PATTERN.test(deviceId)) {
    errors.deviceId =
      "Device ID must be 3–64 characters: letters, digits, '-' or '_', starting with a letter or digit.";
  }

  const tokens = rawPoints
    .split(/[\s,]+/)
    .map((t) => t.trim())
    .filter(Boolean);
  const curvePoints = tokens.map(Number);

  if (tokens.length === 0) {
    errors.curvePoints = "Enter at least one curve point.";
  } else if (tokens.length > MAX_POINTS) {
    errors.curvePoints = `At most ${MAX_POINTS.toLocaleString()} curve points are allowed.`;
  } else {
    const badIndex = curvePoints.findIndex((p) => !Number.isFinite(p));
    const coldIndex = curvePoints.findIndex((p) => p < ABSOLUTE_ZERO_C);
    if (badIndex !== -1) {
      errors.curvePoints = `Point #${badIndex + 1} ("${tokens[badIndex]}") is not a number.`;
    } else if (coldIndex !== -1) {
      errors.curvePoints = `Point #${coldIndex + 1} (${curvePoints[coldIndex]} °C) is below absolute zero (${ABSOLUTE_ZERO_C} °C).`;
    }
  }

  return { errors, curvePoints };
}

function StatCard({ label, value, unit }) {
  return (
    <div className="stat-card">
      <span className="stat-label">{label}</span>
      <span className="stat-value">
        {value}
        {unit && <small> {unit}</small>}
      </span>
    </div>
  );
}

function SummaryView({ summary }) {
  const fmt = (n) => Number(n).toFixed(2);
  return (
    <section className="summary" aria-live="polite">
      <h2>
        Summary for <code>{summary.device_id}</code>
      </h2>
      <div className="stat-grid">
        <StatCard label="Readings" value={summary.readings.toLocaleString()} />
        <StatCard label="Curve points" value={summary.point_count.toLocaleString()} />
        <StatCard label="Min" value={fmt(summary.min)} unit="°C" />
        <StatCard label="Max" value={fmt(summary.max)} unit="°C" />
        <StatCard label="Mean" value={fmt(summary.mean)} unit="°C" />
      </div>
    </section>
  );
}

export default function TelemetryDashboard() {
  const [deviceId, setDeviceId] = useState("");
  const [rawPoints, setRawPoints] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [status, setStatus] = useState({ type: "idle", message: "" });
  const [summary, setSummary] = useState(null);

  const isBusy = status.type === "loading";

  async function loadSummary(id) {
    try {
      setSummary(await fetchTelemetrySummary(id));
    } catch (err) {
      setSummary(null);
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const trimmedId = deviceId.trim();
    const { errors, curvePoints } = validateTelemetry(trimmedId, rawPoints);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      setStatus({ type: "error", message: "Fix the highlighted fields before submitting." });
      return; // Never fire the POST with invalid data.
    }

    setStatus({ type: "loading", message: "Submitting telemetry…" });
    try {
      const result = await submitTelemetry({ deviceId: trimmedId, curvePoints });
      await loadSummary(trimmedId);
      setStatus({
        type: "success",
        message: `Stored ${result.points_received} points for ${result.device_id}.`,
      });
    } catch (err) {
      setStatus({ type: "error", message: err.message || "Request failed." });
    }
  }

  async function handleRefresh() {
    const trimmedId = deviceId.trim();
    if (!DEVICE_ID_PATTERN.test(trimmedId)) {
      setFieldErrors({ deviceId: "Enter a valid device ID to load its summary." });
      return;
    }
    setFieldErrors({});
    setStatus({ type: "loading", message: "Loading summary…" });
    try {
      await loadSummary(trimmedId);
      setStatus({ type: "idle", message: "" });
    } catch (err) {
      setStatus({ type: "error", message: err.message || "Request failed." });
    }
  }

  return (
    <div className="dashboard">
      <form className="card" onSubmit={handleSubmit} noValidate>
        <label htmlFor="device-id">Device ID</label>
        <input
          id="device-id"
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value)}
          placeholder="sensor-001"
          aria-invalid={Boolean(fieldErrors.deviceId)}
          disabled={isBusy}
        />
        {fieldErrors.deviceId && <p className="field-error">{fieldErrors.deviceId}</p>}

        <label htmlFor="curve-points">Curve points (°C, comma or space separated)</label>
        <textarea
          id="curve-points"
          rows={4}
          value={rawPoints}
          onChange={(e) => setRawPoints(e.target.value)}
          placeholder="20.5, 21.0, 19.8, -40"
          aria-invalid={Boolean(fieldErrors.curvePoints)}
          disabled={isBusy}
        />
        {fieldErrors.curvePoints && <p className="field-error">{fieldErrors.curvePoints}</p>}

        <div className="actions">
          <button type="submit" disabled={isBusy}>
            Submit telemetry
          </button>
          <button type="button" className="secondary" onClick={handleRefresh} disabled={isBusy}>
            Load summary
          </button>
        </div>

        {status.message && <p className={`status status-${status.type}`}>{status.message}</p>}
      </form>

      {summary ? (
        <SummaryView summary={summary} />
      ) : (
        <p className="empty">No summary loaded yet.</p>
      )}
    </div>
  );
}
