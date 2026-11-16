import { useState } from "react";
import { apiGet, apiWrite, errorMessage } from "../lib/api";
import { useLoad } from "../lib/hooks";
import type { Msg, SourceInfo } from "../lib/types";
import { Chip, ErrorBox, Loading, Notice, PageHeader } from "../components/ui";

export default function Sources() {
  const sources = useLoad(() => apiGet<{ items: SourceInfo[] }>("/api/v1/sources"), []);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [msg, setMsg] = useState<Msg | null>(null);

  const toggle = async (source: SourceInfo) => {
    setBusyId(source.id);
    setMsg(null);
    try {
      await apiWrite("PUT", `/api/v1/sources/${source.id}/enabled`, { enabled: !source.enabled });
      setMsg({ tone: "ok", text: `${source.name} ${source.enabled ? "deaktiviert" : "aktiviert"}.` });
      sources.reload();
    } catch (cause) {
      setMsg({ tone: "error", text: errorMessage(cause) });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <PageHeader eyebrow="QUELLEN" title="Metadaten-Quellen" />
      <p className="intro">
        Quellen liefern Vorschläge für fehlende Titel. Die Konfiguration ist lokal; Zugangsdaten stehen nur in der Umgebung oder im geschützten
        Speicher und werden nie angezeigt. Ein eigener Quellen-Editor folgt in einer späteren Phase.
      </p>
      {msg && <Notice tone={msg.tone === "ok" ? "ok" : "error"}>{msg.text}</Notice>}
      {sources.loading && !sources.data && <Loading label="Quellen werden geladen …" />}
      {sources.error && <ErrorBox error={sources.error} onRetry={sources.reload} />}
      {sources.data && (
        <ul className="cards">
          {sources.data.items.map((source) => (
            <li key={source.id} className="card">
              <div className="card-head">
                <h2>{source.name}</h2>
                <button
                  type="button"
                  className={source.enabled ? "btn" : "btn quiet"}
                  aria-pressed={source.enabled}
                  disabled={busyId === source.id}
                  onClick={() => toggle(source)}
                >
                  {source.enabled ? "Aktiv" : "Inaktiv"}
                </button>
              </div>
              <div className="row-actions">
                <Chip>{source.nightlyCapable ? "nachts möglich" : "nur auf Knopfdruck"}</Chip>
                <Chip tone={source.includeInNightly ? "ok" : ""}>{source.includeInNightly ? "im Nachtlauf" : "nicht im Nachtlauf"}</Chip>
                <Chip tone={source.credentialsConfigured ? "ok" : ""}>
                  {source.credentialsConfigured ? "Zugangsdaten vorhanden" : "keine Zugangsdaten hinterlegt"}
                </Chip>
              </div>
              <p className="small">
                Erlaubte Hosts: {source.hosts.length > 0 ? source.hosts.join(", ") : "keine"}
              </p>
              <p className="small">Sprachen: {source.languages.length > 0 ? source.languages.join(", ") : "nicht festgelegt"}</p>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
