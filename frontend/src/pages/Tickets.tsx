import { apiGet } from "../lib/api";
import { useLoad } from "../lib/hooks";
import { formatTs, langLabel } from "../lib/format";
import type { Ticket } from "../lib/types";
import { Chip, Empty, ErrorBox, Link, Loading, PageHeader } from "../components/ui";

export function ticketStatusLabel(status: string, tvdbNumber: number | null): string {
  if (tvdbNumber) return "eingereicht";
  return status === "erstellt" ? "Entwurf" : status;
}

export default function Tickets() {
  const tickets = useLoad(() => apiGet<{ items: Ticket[] }>("/api/v1/tickets"), []);

  return (
    <>
      <PageHeader eyebrow="TICKETS" title="Ticket-Vorlagen">
        <Link to="/aktionen" className="btn quiet">
          TVDB-Aktionen
        </Link>
      </PageHeader>
      <p className="intro">Lokale Vorlagen für TheTVDB. Sie werden nie automatisch eingereicht.</p>
      {tickets.loading && !tickets.data && <Loading label="Tickets werden geladen …" />}
      {tickets.error && <ErrorBox error={tickets.error} onRetry={tickets.reload} />}
      {tickets.data &&
        (tickets.data.items.length === 0 ? (
          <Empty>Noch keine Vorlagen. Lege eine Vorlage auf der Seite einer Serie an.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Serie</th>
                  <th scope="col">Staffel</th>
                  <th scope="col">Sprache</th>
                  <th scope="col">Status</th>
                  <th scope="col">TVDB-Nr.</th>
                  <th scope="col">Einträge</th>
                  <th scope="col">Stand</th>
                </tr>
              </thead>
              <tbody>
                {tickets.data.items.map((ticket) => (
                  <tr key={ticket.id}>
                    <td data-label="Serie">
                      <Link to={`/tickets/${ticket.id}`}>{ticket.seriesTitle}</Link>
                    </td>
                    <td data-label="Staffel">{ticket.season === 0 ? "Specials" : ticket.season}</td>
                    <td data-label="Sprache">{langLabel(ticket.language)}</td>
                    <td data-label="Status">
                      <Chip tone={ticket.tvdbNumber ? "ok" : ""}>{ticketStatusLabel(ticket.status, ticket.tvdbNumber)}</Chip>
                    </td>
                    <td data-label="TVDB-Nr.">{ticket.tvdbNumber ?? "–"}</td>
                    <td data-label="Einträge">{ticket.itemCount}</td>
                    <td data-label="Stand">{formatTs(ticket.updated ?? ticket.created)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </>
  );
}
