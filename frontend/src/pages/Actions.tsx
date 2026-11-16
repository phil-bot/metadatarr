import { useState } from "react";
import { ApiError, apiGet, apiWrite, errorMessage } from "../lib/api";
import { useLoad } from "../lib/hooks";
import { formatTs } from "../lib/format";
import type { ActionInfo, Msg, Ticket } from "../lib/types";
import { ticketStatusLabel } from "./Tickets";
import { ErrorBox, Link, Loading, Notice, PageHeader } from "../components/ui";

type Preview = {
  previewId: string;
  previewHash: string;
  expiresAt: number;
  effect: string;
  preview: unknown;
};

type Execution = { state: string; ticketId: number; tvdbNumber: number };

export default function Actions() {
  const actions = useLoad(() => apiGet<{ items: ActionInfo[] }>("/api/v1/actions"), []);
  const tickets = useLoad(() => apiGet<{ items: Ticket[] }>("/api/v1/tickets"), []);
  const [ticketId, setTicketId] = useState<number | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);

  const tvdb = actions.data?.items.find((item) => item.id === "tvdb-ticket");
  const drafts = (tickets.data?.items ?? []).filter((ticket) => ticket.status === "erstellt" && !ticket.tvdbNumber);
  const expired = preview ? Date.now() / 1000 > preview.expiresAt : false;

  const makePreview = async () => {
    if (ticketId === null) return;
    setBusy(true);
    setMsg(null);
    setPreview(null);
    setConfirmed(false);
    try {
      const result = await apiWrite<Preview>("POST", "/api/v1/actions/tvdb-ticket/preview", { ticketId });
      setPreview(result);
      // Der Schlüssel gehört zu genau dieser Vorschau; er verhindert doppelte Einreichungen.
      setIdempotencyKey(`ui.${result.previewId.slice(0, 24)}.${Date.now().toString(36)}`);
    } catch (cause) {
      setMsg({ tone: "error", text: errorMessage(cause) });
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!preview || !confirmed || expired) return;
    setBusy(true);
    setMsg(null);
    try {
      // Der Server akzeptiert genau diese drei Felder; previewHash wird nicht mitgesendet.
      const result = await apiWrite<Execution>("POST", "/api/v1/actions/tvdb-ticket/execute", {
        previewId: preview.previewId,
        confirm: true,
        idempotencyKey,
      });
      setMsg({ tone: "ok", text: `Ticket eingereicht. TVDB-Nummer ${result.tvdbNumber}.` });
      tickets.reload();
    } catch (cause) {
      const unknownResult = cause instanceof ApiError && cause.code === "action_result_unknown";
      setMsg({ tone: unknownResult ? "warn" : "error", text: errorMessage(cause) });
    } finally {
      // Nach jedem Versuch ist die Vorschau verbraucht; ein neuer Lauf braucht eine neue Vorschau.
      setPreview(null);
      setConfirmed(false);
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader eyebrow="AKTIONEN" title="TVDB-Aktionen" />
      <p className="intro">
        Eine Einreichung bei TheTVDB ist immer ein zweistufiger Vorgang: erst Vorschau prüfen, dann bestätigen. Automatische Einreichungen gibt es
        nicht.
      </p>
      {msg && <Notice tone={msg.tone === "ok" ? "ok" : msg.tone === "warn" ? "warn" : "error"}>{msg.text}</Notice>}
      {actions.loading && !actions.data && <Loading label="Aktionen werden geladen …" />}
      {actions.error && <ErrorBox error={actions.error} onRetry={actions.reload} />}
      {actions.data && tvdb && !tvdb.enabled && (
        <Notice tone="warn">
          Die TVDB-Ticket-Aktion ist deaktiviert oder nicht konfiguriert. Zugangsdaten werden über die Umgebung gesetzt (TVDB_USER und
          TVDB_PASSWORD). Die Aktivierung erfolgt serverseitig, nicht über diese Oberfläche.
        </Notice>
      )}

      <section className="panel">
        <h2>Ticket einreichen</h2>
        {tickets.loading && !tickets.data && <Loading label="Entwürfe werden geladen …" />}
        {tickets.error && <ErrorBox error={tickets.error} onRetry={tickets.reload} />}
        {tickets.data && drafts.length === 0 && (
          <Notice tone="info">Keine offenen Entwürfe. Lege zuerst eine Vorlage auf einer Serienseite an.</Notice>
        )}
        {drafts.length > 0 && (
          <div className="stack">
            <label className="field">
              <span>Entwurf</span>
              <select
                value={ticketId ?? ""}
                onChange={(event) => {
                  setTicketId(event.target.value ? Number(event.target.value) : null);
                  setPreview(null);
                  setConfirmed(false);
                }}
              >
                <option value="">Bitte wählen</option>
                {drafts.map((ticket) => (
                  <option key={ticket.id} value={ticket.id}>
                    #{ticket.id} · {ticket.seriesTitle} · Staffel {ticket.season} · {ticketStatusLabel(ticket.status, ticket.tvdbNumber)}
                  </option>
                ))}
              </select>
            </label>
            <div className="row-actions">
              <button type="button" className="btn" disabled={busy || ticketId === null || !tvdb?.enabled} onClick={makePreview}>
                Vorschau erstellen
              </button>
              {ticketId !== null && (
                <Link to={`/tickets/${ticketId}`} className="btn quiet">
                  Ticket ansehen
                </Link>
              )}
            </div>
          </div>
        )}
      </section>

      {preview && (
        <section className="panel" aria-labelledby="preview-title">
          <h2 id="preview-title">Vorschau</h2>
          <p>{preview.effect}</p>
          <p className="small">
            {expired
              ? "Die Vorschau ist abgelaufen. Bitte eine neue erstellen."
              : `Gültig bis ${formatTs(preview.expiresAt)}.`}
          </p>
          <pre className="block" tabIndex={0} aria-label="Inhalt der Vorschau">
            {JSON.stringify(preview.preview, null, 2)}
          </pre>
          <label className="check">
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={expired} />
            Ich habe die Vorschau geprüft und bestätige die externe Einreichung bei TheTVDB.
          </label>
          <div className="row-actions">
            <button type="button" className="btn danger" disabled={busy || !confirmed || expired} onClick={submit}>
              {busy ? "Wird eingereicht …" : "Jetzt einreichen"}
            </button>
          </div>
        </section>
      )}
    </>
  );
}
