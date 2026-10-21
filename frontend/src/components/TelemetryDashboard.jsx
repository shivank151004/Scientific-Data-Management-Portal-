import { useState } from "react";
import {
  ApiError,
  createUser,
  fetchTelemetrySummary,
  submitTelemetry,
} from "../api/telemetryClient.js";

// Mirrors the backend Pydantic constraints in src/models/schemas.py so invalid
// input is caught before a request is ever sent.
const DEVICE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$/;
const USERNAME_PATTERN = /^[A-Za-z0-9_.-]{3,32}$/;
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
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

export function validateUserRegistration({ username, email, experimentName, experimentDeviceId }) {
  const errors = {};
  if (!USERNAME_PATTERN.test(username)) {
    errors.username = "Username must be 3–32 characters (letters, digits, '_', '.', '-').";
  }
  if (!EMAIL_PATTERN.test(email)) {
    errors.email = "Enter a valid email address.";
  }
  if (experimentName && !DEVICE_ID_PATTERN.test(experimentDeviceId)) {
    errors.experimentDeviceId = "Enter a valid Device ID (3–64 chars) for the experiment.";
  }
  return errors;
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
  // Telemetry ingestion state
  const [deviceId, setDeviceId] = useState("");
  const [rawPoints, setRawPoints] = useState("");
  const [unit, setUnit] = useState("K");
  const [pressureKpa, setPressureKpa] = useState("101.3");
  const [sampleRateHz, setSampleRateHz] = useState("1000");
  const [fieldErrors, setFieldErrors] = useState({});
  const [status, setStatus] = useState({ type: "idle", message: "" });
  const [summary, setSummary] = useState(null);

  // User identity & experiment registration state (POST /api/v1/users)
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [experimentName, setExperimentName] = useState("");
  const [experimentDeviceId, setExperimentDeviceId] = useState("");
  const [userErrors, setUserErrors] = useState({});
  const [userStatus, setUserStatus] = useState({ type: "idle", message: "" });
  const [createdUser, setCreatedUser] = useState(null);

  const isBusy = status.type === "loading";
  const isUserBusy = userStatus.type === "loading";

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

    const parameters = {};
    if (pressureKpa.trim() !== "" && Number.isFinite(Number(pressureKpa))) {
      parameters.pressure_kpa = Number(pressureKpa);
    }
    if (sampleRateHz.trim() !== "" && Number.isFinite(Number(sampleRateHz))) {
      parameters.sample_rate_hz = Number(sampleRateHz);
    }

    setStatus({ type: "loading", message: "Submitting telemetry…" });
    try {
      const result = await submitTelemetry({
        deviceId: trimmedId,
        curvePoints,
        unit: unit.trim() || "K",
        parameters,
      });
      await loadSummary(trimmedId);
      setStatus({
        type: "success",
        message: `Stored ${result.points_received} points (${result.unit}) for ${result.device_id}.`,
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

  async function handleUserSubmit(event) {
    event.preventDefault();
    const trimmedUsername = username.trim();
    const trimmedEmail = email.trim();
    const trimmedExpName = experimentName.trim();
    const trimmedExpDevice = experimentDeviceId.trim();

    const errors = validateUserRegistration({
      username: trimmedUsername,
      email: trimmedEmail,
      experimentName: trimmedExpName,
      experimentDeviceId: trimmedExpDevice,
    });
    setUserErrors(errors);
    if (Object.keys(errors).length > 0) {
      setUserStatus({ type: "error", message: "Fix user registration errors before submitting." });
      return;
    }

    const experiment = trimmedExpName
      ? { experiment_name: trimmedExpName, device_id: trimmedExpDevice }
      : undefined;

    setUserStatus({ type: "loading", message: "Registering researcher identity…" });
    try {
      const result = await createUser({
        username: trimmedUsername,
        email: trimmedEmail,
        fullName: fullName.trim() || undefined,
        experiment,
      });
      setCreatedUser(result);
      setUserStatus({
        type: "success",
        message: `Registered user #${result.id} (${result.email})${
          result.experiment_id ? ` with experiment #${result.experiment_id}` : ""
        }.`,
      });
    } catch (err) {
      setUserStatus({ type: "error", message: err.message || "User registration failed." });
    }
  }

  return (
    <div className="dashboard">
      <form className="card" onSubmit={handleSubmit} noValidate>
        <h2>Telemetry Curve Ingestion</h2>
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

        <div className="stat-grid" style={{ marginTop: "0.75rem" }}>
          <div>
            <label htmlFor="unit">Unit</label>
            <input
              id="unit"
              value={unit}
              onChange={(e) => setUnit(e.target.value)}
              placeholder="K"
              disabled={isBusy}
            />
          </div>
          <div>
            <label htmlFor="pressure-kpa">Pressure (kPa)</label>
            <input
              id="pressure-kpa"
              value={pressureKpa}
              onChange={(e) => setPressureKpa(e.target.value)}
              placeholder="101.3"
              disabled={isBusy}
            />
          </div>
          <div>
            <label htmlFor="sample-rate-hz">Sample Rate (Hz)</label>
            <input
              id="sample-rate-hz"
              value={sampleRateHz}
              onChange={(e) => setSampleRateHz(e.target.value)}
              placeholder="1000"
              disabled={isBusy}
            />
          </div>
        </div>

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

      <form className="card" onSubmit={handleUserSubmit} noValidate>
        <h2>Researcher Identity & Experiment Registration (PostgreSQL ACID)</h2>
        <label htmlFor="username">Username</label>
        <input
          id="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="shivank"
          aria-invalid={Boolean(userErrors.username)}
          disabled={isUserBusy}
        />
        {userErrors.username && <p className="field-error">{userErrors.username}</p>}

        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="researcher@example.com"
          aria-invalid={Boolean(userErrors.email)}
          disabled={isUserBusy}
        />
        {userErrors.email && <p className="field-error">{userErrors.email}</p>}

        <label htmlFor="full-name">Full Name (optional)</label>
        <input
          id="full-name"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          placeholder="Shivank Shukla"
          disabled={isUserBusy}
        />

        <div className="stat-grid" style={{ marginTop: "0.75rem" }}>
          <div>
            <label htmlFor="experiment-name">Experiment Name (optional)</label>
            <input
              id="experiment-name"
              value={experimentName}
              onChange={(e) => setExperimentName(e.target.value)}
              placeholder="Cryo Run 1"
              disabled={isUserBusy}
            />
          </div>
          <div>
            <label htmlFor="experiment-device-id">Experiment Device ID</label>
            <input
              id="experiment-device-id"
              value={experimentDeviceId}
              onChange={(e) => setExperimentDeviceId(e.target.value)}
              placeholder="sensor-001"
              aria-invalid={Boolean(userErrors.experimentDeviceId)}
              disabled={isUserBusy}
            />
            {userErrors.experimentDeviceId && (
              <p className="field-error">{userErrors.experimentDeviceId}</p>
            )}
          </div>
        </div>

        <div className="actions">
          <button type="submit" disabled={isUserBusy}>
            Register Identity
          </button>
        </div>

        {userStatus.message && (
          <p className={`status status-${userStatus.type}`}>{userStatus.message}</p>
        )}
        {createdUser && (
          <p className="status status-success" data-testid="created-user-summary">
            Active Identity: <code>{createdUser.username}</code> (ID #{createdUser.id})
          </p>
        )}
      </form>
    </div>
  );
}
