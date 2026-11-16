import { useMemo, useState } from "react";
import { airState, deDate, episodeCode, isMissing, langLabel } from "../lib/format";
import { episodeState, useInventory, type EpisodeField } from "../lib/inventory";
import type { FieldState } from "../lib/types";
import { Chip, Empty, ErrorBox, Link, Loading, PageHeader, StatusChip } from "../components/ui";

const PAGE_SIZE = 200;

type Row = {
  seriesId: number;
  seriesTitle: string;
  season: number;
  episode: number;
  title: string;
  airDate: string;
  air: string;
  state: FieldState;
};

export default function Missing() {
  const inv = useInventory();
  const [language, setLanguage] = useState("");
  const [field, setField] = useState<EpisodeField>("episode.title");
  const [limit, setLimit] = useState(PAGE_SIZE);

  const languages = inv.data?.targetLanguages ?? [];
  const active = language || languages[0] || "";

  const rows = useMemo(() => {
    const out: Row[] = [];
    if (!inv.data || !active) return out;
    for (const series of inv.data.series) {
      for (const season of series.seasons) {
        for (const episode of season.episodes) {
          const state = episodeState(episode, field, active);
          if (state && isMissing(state)) {
            out.push({
              seriesId: series.id,
              seriesTitle: series.title,
              season: episode.season,
              episode: episode.episode,
              title: episode.title ?? "",
              airDate: episode.airDate,
              air: airState(episode.airDate),
              state,
            });
          }
        }
      }
    }
    return out;
  }, [inv.data, field, active]);

  return (
    <>
      <PageHeader eyebrow="LÜCKEN" title="Fehlende Metadaten" />
      <p className="intro">
        Folgen, bei denen der Titel oder die Beschreibung in der gewählten Zielsprache fehlt, leer ist, ein Platzhalter ist oder in einer anderen
        Sprache vorliegt. Angekündigte Folgen sind mit eigenem Hinweis enthalten.
      </p>
      <div className="toolbar">
        <label className="field">
          <span>Zielsprache</span>
          <select value={active} onChange={(event) => setLanguage(event.target.value)} disabled={languages.length === 0}>
            {languages.map((lang) => (
              <option key={lang} value={lang}>
                {langLabel(lang)} ({lang})
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Feld</span>
          <select value={field} onChange={(event) => setField(event.target.value as EpisodeField)}>
            <option value="episode.title">Folgentitel</option>
            <option value="episode.overview">Folgenbeschreibung</option>
          </select>
        </label>
      </div>
      {inv.loading && !inv.data && <Loading label="Bestand wird geladen …" />}
      {inv.error && <ErrorBox error={inv.error} onRetry={inv.reload} />}
      {inv.data && (
        <>
          <p className="small mut" aria-live="polite">
            {rows.length} fehlende Einträge
          </p>
          {rows.length === 0 ? (
            <Empty>Keine Lücken für diese Auswahl.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Serie</th>
                    <th scope="col">Folge</th>
                    <th scope="col">Sendung</th>
                    <th scope="col">Wert aus Sonarr</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, limit).map((row) => (
                    <tr key={`${row.seriesId}-${row.season}-${row.episode}`}>
                      <td data-label="Serie">
                        <Link to={`/serien/${row.seriesId}`}>{row.seriesTitle}</Link>
                      </td>
                      <td data-label="Folge">
                        <span className="code">{episodeCode(row.season, row.episode)}</span>
                      </td>
                      <td data-label="Sendung">
                        <Chip tone={row.air === "angekündigt" ? "busy" : ""}>{row.air}</Chip>{" "}
                        <span className="small">{deDate(row.airDate)}</span>
                      </td>
                      <td data-label="Wert aus Sonarr">{row.title.trim() ? row.title : <span className="mut">(leer)</span>}</td>
                      <td data-label="Status">
                        <StatusChip state={row.state} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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
