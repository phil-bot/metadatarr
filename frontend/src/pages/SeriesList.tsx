import { useMemo, useState } from "react";
import { langLabel } from "../lib/format";
import { seriesGapCount, useInventory } from "../lib/inventory";
import { Chip, Empty, ErrorBox, Link, Loading, PageHeader, Poster } from "../components/ui";

const PAGE_SIZE = 100;

export default function SeriesList() {
  const inv = useInventory();
  const [query, setQuery] = useState("");
  const [onlyGaps, setOnlyGaps] = useState(false);
  const [limit, setLimit] = useState(PAGE_SIZE);

  const rows = useMemo(() => {
    if (!inv.data) return [];
    const needle = query.trim().toLowerCase();
    return inv.data.series
      .map((item) => ({ item, gaps: seriesGapCount(item) }))
      .filter((row) => (!onlyGaps || row.gaps > 0) && (!needle || row.item.title.toLowerCase().includes(needle)))
      .sort((a, b) => b.gaps - a.gaps || a.item.title.localeCompare(b.item.title, "de"));
  }, [inv.data, query, onlyGaps]);

  return (
    <>
      <PageHeader eyebrow="BESTAND" title="Serien" />
      <div className="toolbar">
        <label className="field grow">
          <span>Serie suchen</span>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Titel eingeben" />
        </label>
        <label className="check">
          <input type="checkbox" checked={onlyGaps} onChange={(event) => setOnlyGaps(event.target.checked)} /> Nur mit Lücken
        </label>
      </div>
      {inv.loading && !inv.data && <Loading label="Serien werden geladen …" />}
      {inv.error && <ErrorBox error={inv.error} onRetry={inv.reload} />}
      {inv.data && (
        <>
          <p className="small mut" aria-live="polite">
            {rows.length} von {inv.data.series.length} Serien
          </p>
          {rows.length === 0 ? (
            <Empty>Keine Serien passen zu dieser Auswahl.</Empty>
          ) : (
            <ul className="series-list">
              {rows.slice(0, limit).map(({ item, gaps }) => (
                <li key={item.id}>
                  <Poster seriesId={item.id} title={item.title} />
                  <div className="series-main">
                    <Link to={`/serien/${item.id}`} className="series-title">
                      {item.title}
                    </Link>
                    <p className="small mut">
                      {item.year ?? "–"} · {item.network || "Sender unbekannt"} · {item.episodeCount} Folgen
                    </p>
                    <p className="small">Zielsprachen: {item.targetLanguages.map(langLabel).join(", ") || "keine"}</p>
                  </div>
                  <Chip tone={gaps > 0 ? "warn" : "ok"}>{gaps > 0 ? `${gaps} Lücken` : "vollständig"}</Chip>
                </li>
              ))}
            </ul>
          )}
          {rows.length > limit && (
            <button type="button" className="btn quiet" onClick={() => setLimit((value) => value + PAGE_SIZE)}>
              Weitere anzeigen
            </button>
          )}
        </>
      )}
    </>
  );
}
