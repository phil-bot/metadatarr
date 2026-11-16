import { apiGet, apiWrite, errorMessage } from "../lib/api";
import { useLoad } from "../lib/hooks";
import { formatTs } from "../lib/format";
import { useInventory } from "../lib/inventory";
import type { ActionInfo, SourceInfo } from "../lib/types";
import { Chip, ErrorBox, Loading, PageHeader } from "../components/ui";

type Health = { status: string; service: string; apiVersion: string };
type Session = { app: { name: string; version: string }; features: string[] };

export default function Diagnose() {
  const health = useLoad(() => apiGet<Health>("/api/v1/health"), []);
  const session = useLoad(() => apiGet<Session>("/api/v1/session"), []);
  const sources = useLoad(() => apiGet<{ items: SourceInfo[] }>("/api/v1/sources"), []);
  const actions = useLoad(() => apiGet<{ items: ActionInfo[] }>("/api/v1/actions"), []);
  const inv = useInventory();

  const reloadAll = () => {
    health.reload();
    session.reload();
    sources.reload();
    actions.reload();
    inv.reload();
  };

  return (
    <>
      <PageHeader eyebrow="DIAGNOSE" title="Zustand der Anwendung">
        <button type="button" className="btn quiet" onClick={reloadAll}>
          Alles neu prüfen
        </button>
      </PageHeader>
      <p className="intro">Geheimnisse wie API-Schlüssel und Passwörter werden hier nie angezeigt.</p>

      <section className="panel">
        <h2>Dienste</h2>
        <table className="table">
          <tbody>
            <tr>
              <th scope="row">API</th>
              <td data-label="Status">
                {health.loading && !health.data && <Loading label="Prüfe …" />}
                {health.error && <ErrorBox error={health.error} onRetry={health.reload} />}
                {health.data && (
                  <Chip tone={health.data.status === "ok" ? "ok" : "err"}>
                    {health.data.service} · API {health.data.apiVersion} · {health.data.status}
                  </Chip>
                )}
              </td>
            </tr>
            <tr>
              <th scope="row">Version</th>
              <td data-label="Version">
                {session.data ? `${session.data.app.name} ${session.data.app.version}` : session.error ? errorMessage(session.error) : "…"}
              </td>
            </tr>
            <tr>
              <th scope="row">Sonarr-Bestand</th>
              <td data-label="Sonarr">
                {inv.loading && !inv.data && <Loading label="Lade Bestand …" />}
                {inv.error && <ErrorBox error={inv.error} onRetry={inv.reload} />}
                {inv.data && (
                  <Chip tone="ok">
                    {inv.data.seriesTotal} Serien · Stand {formatTs(inv.data.fetched)}
                  </Chip>
                )}
              </td>
            </tr>
            <tr>
              <th scope="row">Quellen</th>
              <td data-label="Quellen">
                {sources.data
                  ? `${sources.data.items.filter((item) => item.enabled).length} von ${sources.data.items.length} aktiv`
                  : sources.error
                    ? errorMessage(sources.error)
                    : "…"}
              </td>
            </tr>
            <tr>
              <th scope="row">TVDB-Aktion</th>
              <td data-label="TVDB-Aktion">
                {actions.data
                  ? actions.data.items.map((item) => (
                      <Chip key={item.id} tone={item.enabled ? "ok" : "warn"}>
                        {item.id}: {item.enabled ? "aktiv" : "inaktiv"}
                      </Chip>
                    ))
                  : actions.error
                    ? errorMessage(actions.error)
                    : "…"}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      {session.data && (
        <section className="panel">
          <h2>Funktionen</h2>
          <ul className="plain-list">
            {session.data.features.map((feature) => (
              <li key={feature}>
                <code>{feature}</code>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
