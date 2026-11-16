import { useState } from "react";
import { apiGet, apiWrite, errorMessage } from "../lib/api";
import { useLoad } from "../lib/hooks";
import { langLabel } from "../lib/format";
import { navigate } from "../lib/router";
import type { Msg, TicketDetail as TicketDetailType } from "../lib/types";
import { Chip, ErrorBox, Link, Loading, Notice, PageHeader } from "../components/ui";
import { ticketStatusLabel } from "./Tickets";

export default function TicketDetail({ id }: { id: number }) {
  const ticket = useLoad(() => apiGet<{ item: TicketDetailType }>(`/api/v1/tickets/${id}`), [id]);
  const [msg, setMsg] = useState<Msg | null>(null);
  const [busy, setBusy] = useState(false);

  const copy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setMsg({ tone: "ok", text: `${label} kopiert.` });
    } catch {
      setMsg({ tone: "error", text: "Kopieren ist hier nicht möglich. Bitte den Text manuell markieren." });
    }
  };

  const remove = async () => {
    if (!window.confirm("Diesen lokalen Entwurf wirklich löschen?")) return;
    setBusy(true);
    setMsg(null);
    try {
      await apiWrite("DELETE", `/api/v1/tickets/${id}`);
      navigate("/tickets");
    } catch (cause) {
      setMsg({ tone: "error", text: errorMessage(cause) });
      setBusy(false);
    }
  };

  if (ticket.loading && !ticket.data) return <Loading label="Ticket wird geladen …" />;
  if (ticket.error) return <ErrorBox error={ticket.error} onRetry={ticket.reload} />;
  if (!ticket.data) return null;

  const item = ticket.data.item;
  const isDraft = item.status === "erstellt" && !item.tvdbNumber;

  return (
    <>
      <nav className="crumbs" aria-label="Pfad">
        <Link to="/tickets">Tickets</Link> / <span>#{item.id}</span>
      </nav>
      <PageHeader eyebrow={`TICKET #${item.id}`} title={item.seriesTitle}>
        <Chip tone={item.tvdbNumber ? "ok" : ""}>{ticketStatusLabel(item.status, item.tvdbNumber)}</Chip>
      </PageHeader>
      <p className="meta">
        Staffel {item.season === 0 ? "Specials" : item.season} · {langLabel(item.language)}
        {item.tvdbNumber ? ` · TVDB-Nr. ${item.tvdbNumber}` : ""}
        {item.tvdbStatus ? ` · Status ${item.tvdbStatus}` : ""}
      </p>
      {msg && <Notice tone={msg.tone === "ok" ? "ok" : "error"}>{msg.text}</Notice>}

      <section className="panel">
        <h2>Betreff</h2>
        <p className="code-block">{item.subject}</p>
        <div className="row-actions">
          <button type="button" className="btn quiet" onClick={() => copy(item.subject, "Betreff")}>
            Betreff kopieren
          </button>
        </div>
      </section>

      <section className="panel">
        <h2>Text</h2>
        <pre className="block" tabIndex={0} aria-label="Ticket-Text">
          {item.body}
        </pre>
        <div className="row-actions">
          <button type="button" className="btn" onClick={() => copy(item.body, "Text")}>
            Text kopieren
          </button>
          <a className="btn quiet" href={`/api/v1/tickets/${item.id}/export`} download>
            Als Textdatei laden
          </a>
        </div>
      </section>

      <section className="panel">
        <h2>Folgen im Ticket</h2>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Staffel</th>
              <th scope="col">Folge</th>
              <th scope="col">Titel</th>
            </tr>
          </thead>
          <tbody>
            {item.items.map((entry, index) => (
              <tr key={`${entry.season}-${entry.episode}-${index}`}>
                <td data-label="Staffel">{entry.season}</td>
                <td data-label="Folge">{entry.episode}</td>
                <td data-label="Titel">{entry.title}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <div className="row-actions">
          <Link to={`/serien/${item.seriesId}`} className="btn quiet">
            Zur Serie
          </Link>
          <Link to="/aktionen" className="btn quiet">
            TVDB-Aktionen
          </Link>
          {isDraft && (
            <button type="button" className="btn danger" onClick={remove} disabled={busy}>
              Entwurf löschen
            </button>
          )}
        </div>
        <p className="small mut">Die Einreichung bei TheTVDB erfolgt nur über die Seite „TVDB-Aktionen“ nach deiner ausdrücklichen Bestätigung.</p>
      </section>
    </>
  );
}
