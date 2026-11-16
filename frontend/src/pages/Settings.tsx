import { useState } from "react";
import { apiGet, apiWrite, errorMessage } from "../lib/api";
import { useLoad } from "../lib/hooks";
import { langLabel, parseLanguages } from "../lib/format";
import { useInventory } from "../lib/inventory";
import type { Msg, RuleInfo } from "../lib/types";
import { Empty, ErrorBox, Loading, Notice, PageHeader } from "../components/ui";

const FIELD_LABEL: Record<string, string> = {
  "series.title": "Serientitel",
  "series.overview": "Serienbeschreibung",
  "episode.title": "Folgentitel",
  "episode.overview": "Folgenbeschreibung",
};
const KIND_LABEL: Record<string, string> = { empty: "leer", exact: "genauer Text", regex: "Regex" };

type UiRule = { field: string; language: string; kind: "empty" | "exact" | "regex"; pattern: string; enabled: boolean };

export default function Settings() {
  const languages = useLoad(() => apiGet<{ languages: string[] }>("/api/v1/target-languages"), []);
  const rules = useLoad(() => apiGet<{ rules: RuleInfo[] }>("/api/v1/placeholder-rules"), []);
  const inv = useInventory();

  const refresh = () => {
    languages.reload();
    rules.reload();
    inv.reload();
  };

  return (
    <>
      <PageHeader eyebrow="EINSTELLUNGEN" title="Zielsprachen und Regeln" />
      <p className="intro">
        Diese Werte speichert Metadatarr lokal. Sonarr wird dabei nie verändert.
      </p>

      <section className="panel">
        <h2>Globale Zielsprachen</h2>
        {languages.loading && !languages.data && <Loading />}
        {languages.error && <ErrorBox error={languages.error} onRetry={languages.reload} />}
        {languages.data && <LanguagesForm initial={languages.data.languages} onSaved={refresh} />}
      </section>

      <section className="panel">
        <h2>Platzhalterregeln</h2>
        <p className="small">
          Regeln legen fest, wann ein Wert als Platzhalter oder leer gilt. Eigene Regeln sind unten editierbar; Regeln aus der Umgebung sind nur
          zur Ansicht.
        </p>
        {rules.loading && !rules.data && <Loading />}
        {rules.error && <ErrorBox error={rules.error} onRetry={rules.reload} />}
        {rules.data && (
          <>
            <h3>Aus der Umgebung (schreibgeschützt)</h3>
            {rules.data.rules.filter((rule) => !rule.editable).length === 0 ? (
              <Empty>Keine Regeln aus der Umgebung.</Empty>
            ) : (
              <ul className="plain-list">
                {rules.data.rules
                  .filter((rule) => !rule.editable)
                  .map((rule) => (
                    <li key={rule.id}>
                      {FIELD_LABEL[rule.field] ?? rule.field} · {rule.language ? langLabel(rule.language) : "alle Sprachen"} · {KIND_LABEL[rule.kind] ?? rule.kind}
                      {rule.pattern ? ` „${rule.pattern}“` : ""} · {rule.enabled ? "aktiv" : "inaktiv"}
                    </li>
                  ))}
              </ul>
            )}
            <h3>Eigene Regeln</h3>
            <RulesForm
              initial={rules.data.rules
                .filter((rule) => rule.editable)
                .map((rule) => ({
                  field: rule.field,
                  language: rule.language ?? "",
                  kind: rule.kind,
                  pattern: rule.pattern,
                  enabled: rule.enabled,
                }))}
              onSaved={refresh}
            />
          </>
        )}
      </section>
    </>
  );
}

function LanguagesForm({ initial, onSaved }: { initial: string[]; onSaved: () => void }) {
  const [list, setList] = useState<string[]>(initial);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);

  const add = () => {
    const parsed = parseLanguages(input);
    if (parsed.length === 0) return;
    setList((previous) => {
      const next = [...previous];
      for (const code of parsed) {
        if (!next.some((existing) => existing.toLowerCase() === code.toLowerCase())) next.push(code);
      }
      return next;
    });
    setInput("");
  };

  const save = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const result = await apiWrite<{ languages: string[] }>("PUT", "/api/v1/target-languages", { languages: list });
      setList(result.languages);
      setMsg({ tone: "ok", text: "Zielsprachen gespeichert." });
      onSaved();
    } catch (cause) {
      setMsg({ tone: "error", text: errorMessage(cause) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <ul className="chips" aria-label="Ausgewählte Sprachen">
        {list.map((code) => (
          <li key={code}>
            <span className="chip">
              {langLabel(code)} ({code})
            </span>{" "}
            <button type="button" className="btn quiet sm" aria-label={`${code} entfernen`} onClick={() => setList(list.filter((item) => item !== code))}>
              Entfernen
            </button>
          </li>
        ))}
      </ul>
      <div className="row-actions">
        <label className="field grow">
          <span>Sprachcode hinzufügen (z. B. de oder en-US)</span>
          <input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => event.key === "Enter" && (event.preventDefault(), add())} />
        </label>
        <button type="button" className="btn quiet" onClick={add}>
          Hinzufügen
        </button>
      </div>
      <div className="row-actions">
        <button type="button" className="btn" onClick={save} disabled={busy || list.length === 0}>
          Speichern
        </button>
      </div>
      {msg && <Notice tone={msg.tone === "ok" ? "ok" : "error"}>{msg.text}</Notice>}
    </div>
  );
}

function RulesForm({ initial, onSaved }: { initial: UiRule[]; onSaved: () => void }) {
  const [list, setList] = useState<UiRule[]>(initial);
  const [draft, setDraft] = useState<UiRule>({ field: "episode.title", language: "", kind: "empty", pattern: "", enabled: true });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg | null>(null);

  const addRule = () => {
    if (draft.kind !== "empty" && draft.pattern.trim() === "") {
      setMsg({ tone: "error", text: "Für diesen Regeltyp braucht die Regel ein Muster." });
      return;
    }
    setList((previous) => [
      ...previous,
      { ...draft, language: draft.language.trim(), pattern: draft.kind === "empty" ? "" : draft.pattern.trim() },
    ]);
    setDraft({ ...draft, language: "", pattern: "" });
    setMsg(null);
  };

  const save = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const payload = list.map((rule) => ({
        field: rule.field,
        language: rule.language ? rule.language : null,
        kind: rule.kind,
        pattern: rule.pattern,
        enabled: rule.enabled,
      }));
      await apiWrite("PUT", "/api/v1/placeholder-rules", { rules: payload });
      setMsg({ tone: "ok", text: "Platzhalterregeln gespeichert." });
      onSaved();
    } catch (cause) {
      setMsg({ tone: "error", text: errorMessage(cause) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      {list.length === 0 ? (
        <Empty>Keine eigenen Regeln.</Empty>
      ) : (
        <ul className="rule-list">
          {list.map((rule, index) => (
            <li key={index} className="rule">
              <span>
                {FIELD_LABEL[rule.field] ?? rule.field} · {rule.language ? langLabel(rule.language) : "alle Sprachen"} · {KIND_LABEL[rule.kind]}
                {rule.pattern ? ` „${rule.pattern}“` : ""}
              </span>
              <label className="check">
                <input
                  type="checkbox"
                  checked={rule.enabled}
                  onChange={(event) =>
                    setList((previous) => previous.map((item, position) => (position === index ? { ...item, enabled: event.target.checked } : item)))
                  }
                />
                aktiv
              </label>
              <button type="button" className="btn quiet sm" onClick={() => setList((previous) => previous.filter((_, position) => position !== index))}>
                Entfernen
              </button>
            </li>
          ))}
        </ul>
      )}
      <fieldset className="fieldset">
        <legend>Neue Regel</legend>
        <div className="grid-form">
          <label className="field">
            <span>Feld</span>
            <select value={draft.field} onChange={(event) => setDraft({ ...draft, field: event.target.value })}>
              {Object.entries(FIELD_LABEL).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Sprache (leer = alle)</span>
            <input value={draft.language} onChange={(event) => setDraft({ ...draft, language: event.target.value })} placeholder="z. B. de" />
          </label>
          <label className="field">
            <span>Regeltyp</span>
            <select value={draft.kind} onChange={(event) => setDraft({ ...draft, kind: event.target.value as UiRule["kind"] })}>
              <option value="empty">leer</option>
              <option value="exact">genauer Text</option>
              <option value="regex">Regex</option>
            </select>
          </label>
          <label className="field">
            <span>Muster</span>
            <input value={draft.pattern} onChange={(event) => setDraft({ ...draft, pattern: event.target.value })} disabled={draft.kind === "empty"} />
          </label>
        </div>
        <div className="row-actions">
          <button type="button" className="btn quiet" onClick={addRule}>
            Regel hinzufügen
          </button>
        </div>
      </fieldset>
      <div className="row-actions">
        <button type="button" className="btn" onClick={save} disabled={busy}>
          Regeln speichern
        </button>
      </div>
      {msg && <Notice tone={msg.tone === "ok" ? "ok" : "error"}>{msg.text}</Notice>}
    </div>
  );
}
