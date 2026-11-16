import { useMemo, useState, type FormEvent } from "react";
import { apiWrite, errorMessage } from "../lib/api";
import { airState, deDate, episodeCode, langLabel, parseLanguages, describeCount } from "../lib/format";
import { episodeState, seasonGapCount, seriesGapCount, useInventory } from "../lib/inventory";
import { navigate } from "../lib/router";
import type { Msg, Season, SearchResult } from "../lib/types";
import { Chip, Empty, ErrorBox, Link, Loading, Notice, PageHeader, Poster, StatusChip } from "../components/ui";

export default function SeriesDetail({ id }: { id: number }) {
  const inv = useInventory();
  const series = useMemo(() => inv.data?.series.find((item) => item.id === id) ?? null, [inv.data, id]);
  const [picked, setPicked] = useState<number | null>(null);

  if (inv.loading && !inv.data) return <Loading label="Serie wird geladen …" />;
  if (inv.error) return <ErrorBox error={inv.error} onRetry={inv.reload} />;
  if (!series) {
    return (
      <>
        <PageHeader title="Serie nicht gefunden" />
        <Notice tone="warn">Serie {id} ist nicht im Sonarr-Bestand.</Notice>
        <p>
          <Link to="/serien" className="btn quiet">
            Zur Serienliste
          </Link>
        </p>
      </>
    );
  }

  const targets = series.targetLanguages;
  const seasons = series.seasons;
  const season: Season | null = seasons.find((item) => item.seasonNumber === picked) ?? seasons[0] ?? null;

  return (
    <>
      <nav className="crumbs" aria-label="Pfad">
        <Link to="/serien">Serien</Link> / <span>{series.title}</span>
      </nav>
      <div className="series-head">
        <Poster seriesId={series.id} title={series.title} large />
        <div>
          <PageHeader eyebrow={`SERIE · SONARR-ID ${series.id}`} title={series.title} />
          <p className="meta">
            {series.year ?? "–"} · {series.network || "Sender unbekannt"} · {series.episodeCount} Folgen · Originalsprache{" "}
            {series.originalLanguage || "unbekannt"}
          </p>
          <p className="meta">
            TVDB-ID {series.tvdbId ?? "–"} · Status {series.status || "–"} · Lücken gesamt {seriesGapCount(series)}
          </p>
          <p className="meta">
            Zielsprachen: {targets.map(langLabel).join(", ") || "keine"}
            {series.targetLanguagesOverride ? " (Serienausnahme)" : " (global)"}
          </p>
        </div>
      </div>

      <section className="panel">
        <h2>Serientitel und -beschreibung</h2>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Sprache</th>
              <th scope="col">Titel</th>
              <th scope="col">Beschreibung</th>
            </tr>
          </thead>
          <tbody>
            {targets.map((lang) => (
              <tr key={lang}>
                <td data-label="Sprache">{langLabel(lang)}</td>
                <td data-label="Titel">
                  <StatusChip state={series.fieldStatus["series.title"]?.[lang]} />
                </td>
                <td data-label="Beschreibung">
                  <StatusChip state={series.fieldStatus["series.overview"]?.[lang]} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <h2>Staffeln und Folgen</h2>
        <div className="season-tabs" role="group" aria-label="Staffel wählen">
          {seasons.map((item) => (
            <button
              key={item.seasonNumber}
              type="button"
              className="btn quiet"
              aria-pressed={season?.seasonNumber === item.seasonNumber}
              onClick={() => setPicked(item.seasonNumber)}
            >
              {item.seasonNumber === 0 ? "Specials" : `Staffel ${item.seasonNumber}`}{" "}
              <span className="mut">· {seasonGapCount(item, targets)}</span>
            </button>
          ))}
        </div>
        {season && <EpisodeTable season={season} targets={targets} />}
      </section>

      <section className="panel">
        <h2>Ticket-Vorlagen</h2>
        <p className="small">
          Erstellt eine lokale Vorlage für die gewählte Staffel. Eingereicht wird nur nach deiner Bestätigung unter „TVDB-Aktionen“.
        </p>
        <TicketCreate seriesId={series.id} season={season?.seasonNumber ?? null} />
      </section>

      <section className="panel">
        <h2>Quellen abfragen</h2>
        <SourceSearch seriesId={series.id} seasons={seasons.map((item) => item.seasonNumber)} />
      </section>

      <section className="panel">
        <h2>Zielsprachen dieser Serie</h2>
        <TargetForm seriesId={series.id} current={targets} onSaved={inv.reload} />
      </section>

      <p className="small mut">
        Vorschläge und Belege werden in dieser Oberfläche noch nicht angezeigt.{" "}
        <a href={`/series/${series.id}/staffel`}>Vorschlagsliste in der bisherigen Ansicht öffnen</a>.
      </p>
    </>
  );
}

function EpisodeTable({ season, targets }: { season: Season; targets: string[] }) {
  if (season.episodes.length === 0) return <Empty>Keine Folgen in dieser Staffel.</Empty>;
  return (
    <div className="table-wrap">
      <table className="table episodes">
        <thead>
          <tr>
            <th scope="col">Folge</th>
            <th scope="col">Sendung</th>
            {targets.map((lang) => (
              <th scope="col" key={lang}>
                Titel {langLabel(lang)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {season.episodes.map((episode) => {
            const air = airState(episode.airDate);
            return (
              <tr key={`${episode.season}-${episode.episode}`}>
                <td data-label="Folge">
                  <span className="code">{episodeCode(episode.season, episode.episode)}</span>
                  {episode.hasFile && <span className="small mut"> · Datei</span>}
                </td>
                <td data-label="Sendung">
                  <Chip tone={air === "angekündigt" ? "busy" : ""}>{air}</Chip>{" "}
                  <span className="small">{deDate(episode.airDate)}</span>
                </td>
                {targets.map((lang) => (
                  <td key={lang} data-label={`Titel ${langLabel(lang)}`}>
                    <div className="cell-value">{episode.title?.trim() ? episode.title : <span className="mut">(leer)</span>}</div>
                    <StatusChip state={episodeState(episode, "episode.title", lang)} />{" "}
                    <StatusChip state={episodeState(episode, "episode.overview", lang)} prefix="Beschreibung" />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TicketCreate({ seriesId, season }: { seriesId: number; season: number | null }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async (lang: "de" | "en") => {
    if (season === null) return;
    setBusy(true);
    setError(null);
    try {
      const payload = await apiWrite<{ item: { id: number } }>("POST", "/api/v1/tickets", { seriesId, season, lang });
      navigate(`/tickets/${payload.item.id}`);
    } catch (cause) {
      setError(errorMessage(cause));
      setBusy(false);
    }
  };

  return (
    <>
      <div className="row-actions">
        <button type="button" className="btn" disabled={busy || season === null} onClick={() => create("de")}>
          Vorlage Deutsch
        </button>
        <button type="button" className="btn" disabled={busy || season === null} onClick={() => create("en")}>
          Vorlage Englisch
        </button>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
    </>
  );
}

function SourceSearch({ seriesId, seasons }: { seriesId: number; seasons: number[] }) {
  const [season, setSeason] = useState("alle");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body: { season?: number } = season === "alle" ? {} : { season: Number(season) };
      setResult(await apiWrite<SearchResult>("POST", `/api/v1/series/${seriesId}/search`, body));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p className="small">Fragt die aktiven Quellen nur auf deinen Knopfdruck ab. Es werden keine Daten geschrieben.</p>
      <div className="row-actions">
        <label className="field">
          <span>Umfang</span>
          <select value={season} onChange={(event) => setSeason(event.target.value)}>
            <option value="alle">Alle Staffeln</option>
            {seasons.map((number) => (
              <option key={number} value={String(number)}>
                {number === 0 ? "Specials" : `Staffel ${number}`}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn" onClick={run} disabled={busy}>
          {busy ? "Abfrage läuft …" : "Quellen abfragen"}
        </button>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {result && (
        <div className="notice info">
          {result.sources ? (
            <ul className="plain-list">
              {result.sources.map((source) => (
                <li key={source.id}>
                  <strong>{source.id}</strong>: {describeCount(source.count)}
                  {source.note ? ` (${source.note})` : ""}
                </li>
              ))}
            </ul>
          ) : (
            <p>
              Ergebnis: {describeCount(result.count)}
              {result.note ? ` (${result.note})` : ""}
            </p>
          )}
        </div>
      )}
    </>
  );
}

function TargetForm({ seriesId, current, onSaved }: { seriesId: number; current: string[]; onSaved: () => void }) {
  const [text, setText] = useState(current.join(", "));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const languages = parseLanguages(text);
    if (languages.length === 0) {
      setMsg({ tone: "error", text: "Mindestens eine Sprache angeben oder auf globale Einstellung zurücksetzen." });
      return;
    }
    await send(languages);
  };

  const send = async (languages: string[] | null) => {
    setBusy(true);
    setMsg(null);
    try {
      await apiWrite("PUT", `/api/v1/series/${seriesId}/target-languages`, { languages });
      setMsg({ tone: "ok", text: languages ? "Zielsprachen gespeichert." : "Auf globale Zielsprachen zurückgesetzt." });
      if (languages === null) setText("");
      onSaved();
    } catch (cause) {
      setMsg({ tone: "error", text: errorMessage(cause) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="stack">
      <label className="field">
        <span>Sprachcodes, durch Komma getrennt (z. B. de, en-US)</span>
        <input value={text} onChange={(event) => setText(event.target.value)} />
      </label>
      <div className="row-actions">
        <button type="submit" className="btn" disabled={busy}>
          Speichern
        </button>
        <button type="button" className="btn quiet" disabled={busy} onClick={() => send(null)}>
          Auf global zurücksetzen
        </button>
      </div>
      {msg && <Notice tone={msg.tone === "ok" ? "ok" : "error"}>{msg.text}</Notice>}
    </form>
  );
}
