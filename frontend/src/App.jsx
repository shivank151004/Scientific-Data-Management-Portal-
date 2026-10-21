import TelemetryDashboard from "./components/TelemetryDashboard.jsx";

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>FastAPI Telemetry Portal</h1>
        <p>Ingest device curves and inspect aggregated statistics.</p>
      </header>
      <main>
        <TelemetryDashboard />
      </main>
    </div>
  );
}
