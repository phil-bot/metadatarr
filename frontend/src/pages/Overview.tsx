import { useMemo } from "react";
import { apiGet } from "../lib/api";
import { useLoad } from "../lib/hooks";
import { countMissingEpisodes, seriesGapCount, useInventory } from "../lib/inventory";
import { formatTs, langLabel } from "../lib/format";
import type { Ticket } from "../lib/types";
import { Chip, ErrorBox, Link, Loading, Notice, PageHeader, Poster } from "../components/ui";

function Kpi({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="kpi">
      <span className="kpi-value">{value}</span>
      <span className="kpi-label">{label}</span>
    </div>
  );
}

export default function Overview() {
  const inv = useInventory();
  const tickets = useLoad(() => apiGet<{ items: Ticket[] }>("/api/v1/tickets"), []);

  const summary = useMemo(() => {
    if (!inv.data) return null;
    const series = inv.data.series;
    const ranked = series
      .map((item) => ({ item, gaps: seriesGapCount(item) }))
      .filter((row) => row.gaps > 0)
      .sort((a, b) => b.gaps - a.gaps);
    const perLanguage = inv.data.targetLanguages.map((lang) => ({
      lang,
      titles: series.reduce((sum, item) => sum + countMissingEpisodes(item, "episode.title", lang), 0),
      overviews: series.reduce((sum, item) => sum + countMissingEpisodes(item, "episode.overview", lang), 0),
    }));
    return { withGaps: ranked.length, top: ranked.slice(0, 8), perLanguage };
  }, [inv.data]);

  const drafts = tickets.data ? tickets.data.items.filter((ticket) => ticket.status === "erstellt" && !ticket.tvdbNumber).length : null;

  return (
    <>
      <PageHeader eyebrow="ÜBERSICHT" title="Metadaten-Lücken">
        <button type="button" className="btn quiet" onClick={inv.reload} disabled={inv.loading}>
          Neu laden
        </button>
      </PageHeader>
      <p className="intro">
        Fehlende oder nicht zielsprachige Titel und Beschreibungen aus dem lesenden Sonarr-Bestand. Metadatarr schreibt nie in Sonarr.
      </p>
      {inv.loading && !inv.data && <Loading label="Sonarr-Bestand wird geladen …" />}
      {inv.error && <ErrorBox error={inv.error} onRetry={inv.reload} />}
      {inv.data && summary && (
        <>
          <section className="kpis" aria-label="Kennzahlen">
            <Kpi label="Serien in Sonarr" value={inv.data.seriesTotal} />
            <Kpi label="Serien mit Lücken" value={summary.withGaps} />
            <Kpi label="Offene Ticket-Entwürfe" value={drafts ?? "–"} />
          </section>
          <section className="panel">
            <h2>Je Zielsprache</h2>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Sprache</th>
                  <th scope="col">Folgentitel fehlen</th>
                  <th scope="col">Folgenbeschreibungen fehlen</th>
                </tr>
              </thead>
              <tbody>
                {summary.perLanguage.map((row) => (
                  <tr key={row.lang}>
                    <td data-label="Sprache">
                      {langLabel(row.lang)} ({row.lang})
                    </td>
                    <td data-label="Folgentitel fehlen">{row.titles}</td>
                    <td data-label="Folgenbeschreibungen fehlen">{row.overviews}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="panel">
            <h2>Größte Lücken</h2>
            {summary.top.length === 0 ? (
              <Notice tone="ok">In den Zielsprachen fehlt nichts.</Notice>
            ) : (
              <ul className="rank-list">
                {summary.top.map(({ item, gaps }) => (
                  <li key={item.id}>
                    <Poster seriesId={item.id} title={item.title} />
                    <Link to={`/serien/${item.id}`} className="rank-title">
                      {item.title}
                    </Link>
                    <Chip tone="warn">{gaps} Lücken</Chip>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <p className="small mut">
            Stand des Sonarr-Bestands: {formatTs(inv.data.fetched)}. Der Bestand wird serverseitig zwischengespeichert; „Neu laden“ holt ihn erneut.
          </p>
        </>
      )}
    </>
  );
}
