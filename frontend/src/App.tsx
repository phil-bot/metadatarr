import { useEffect, useState } from "react";

type ApiHealth = { status: "ok"; service: string; apiVersion: string };

export default function App() {
  const [health, setHealth] = useState<ApiHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetch("/api/v1/health", {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = (await response.json()) as ApiHealth;
        if (payload.status !== "ok") throw new Error("Unerwartete Antwort der API");
        setHealth(payload);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : "Verbindung fehlgeschlagen");
        setHealth(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt]);

  const stateLabel = loading ? "Verbindung wird geprüft" : error ? "API nicht erreichbar" : "API erreichbar";

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="/app/" aria-label="Metadatarr Startseite">
          <img src="/app/brand/metadatarr-icon.svg" alt="" width="42" height="42" />
          <span>Metadatarr</span>
        </a>
        <span className="phase-tag">Grundgerüst</span>
      </header>

      <main className="main-content">
        <p className="eyebrow">SERIE · SPRACHE · METADATEN</p>
        <h1>Das neue Fundament steht.</h1>
        <p className="intro">
          Hier entsteht die Metadatarr-Oberfläche. In diesem Grundgerüst prüfen wir,
          ob React und die Python-JSON-API miteinander sprechen.
        </p>
        <section className={`status-panel ${error ? "is-error" : ""}`} aria-live="polite" aria-busy={loading}>
          <div className="status-mark" aria-hidden="true">{loading ? "…" : error ? "!" : "OK"}</div>
          <div className="status-copy">
            <p className="status-kicker">PYTHON-JSON-API</p>
            <h2>{stateLabel}</h2>
            {loading && <p>Die Verbindung wird geprüft.</p>}
            {!loading && error && <p>{error}. Bitte Server und Build prüfen.</p>}
            {!loading && health && <p>{health.service} · API {health.apiVersion}</p>}
          </div>
          {!loading && (
            <button className="retry-button" type="button" onClick={() => setAttempt((value) => value + 1)}>
              Erneut prüfen
            </button>
          )}
        </section>
        <p className="note">
          Diese Vorschau ruft noch keine Sonarr-Daten ab. Die bestehende Anwendung bleibt unter <code>/</code> erreichbar.
        </p>
      </main>
      <footer className="footer">Metadatarr · Phase 1 · Nur ein lokaler Verbindungscheck</footer>
    </div>
  );
}
