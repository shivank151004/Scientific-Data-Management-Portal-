// Thin fetch wrapper for the FastAPI telemetry endpoints.
// Requests go to a relative /api path; Vite proxies them to FastAPI in dev.

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function formatDetail(detail) {
  if (Array.isArray(detail)) {
    // FastAPI/Pydantic 422 format: [{ loc: [...], msg: "..." }, ...]
    return detail.map((e) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`).join("; ");
  }
  return typeof detail === "string" ? detail : JSON.stringify(detail);
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    ...options,
  });

  let body = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body (e.g. proxy error page); fall through with body = null.
  }

  if (!response.ok) {
    const detail = body?.detail ?? response.statusText;
    throw new ApiError(formatDetail(detail), response.status, detail);
  }
  return body;
}

export function submitTelemetry({ deviceId, curvePoints }) {
  return request("/telemetry", {
    method: "POST",
    body: JSON.stringify({ device_id: deviceId, curve_points: curvePoints }),
  });
}

export function fetchTelemetrySummary(deviceId) {
  return request(`/telemetry/${encodeURIComponent(deviceId)}/summary`);
}
