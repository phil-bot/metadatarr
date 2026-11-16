import type { FieldState } from "./types";

export const STATUS_LABEL: Record<string, string> = {
  present: "vorhanden",
  missing_empty: "leer",
  missing_absent: "fehlt",
  missing_language: "andere Sprache",
  placeholder: "Platzhalter",
  not_checkable: "nicht prüfbar",
  not_provided: "nicht geliefert",
};

const MISSING = new Set(["missing_empty", "missing_absent", "missing_language", "placeholder"]);

export function isMissing(state: FieldState | undefined): boolean {
  return !!state && MISSING.has(state.status);
}

export function statusTone(status: string): "ok" | "warn" | "err" | "" {
  if (status === "present") return "ok";
  if (status === "missing_empty" || status === "missing_absent") return "err";
  if (MISSING.has(status)) return "warn";
  return "";
}

const LANGUAGE_NAMES: Record<string, string> = {
  de: "Deutsch",
  en: "Englisch",
  fr: "Französisch",
  es: "Spanisch",
  it: "Italienisch",
  ja: "Japanisch",
};

export function langLabel(code: string): string {
  const base = code.split("-")[0].toLowerCase();
  return LANGUAGE_NAMES[base] ?? code;
}

export function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function episodeCode(season: number, episode: number): string {
  return `S${pad(season)}E${pad(episode)}`;
}

export function deDate(iso: string | null | undefined): string {
  if (!iso) return "ohne Datum";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : iso;
}

export function todayIso(): string {
  const now = new Date();
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// Folgen mit Name, aber Sendedatum in der Zukunft, gelten als angekündigt und bleiben sichtbar.
export function airState(airDate: string | null | undefined, today: string = todayIso()): string {
  if (!airDate) return "ohne Datum";
  return airDate > today ? "angekündigt" : "gesendet";
}

export function formatTs(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "–";
  const date = typeof value === "number" ? new Date(value > 1e12 ? value : value * 1000) : new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

export function parseLanguages(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

export function describeCount(count: unknown): string {
  if (typeof count === "number") return String(count);
  if (count && typeof count === "object") {
    const parts = Object.entries(count as Record<string, unknown>).map(([key, value]) => `${key}: ${describeCount(value)}`);
    return parts.length > 0 ? parts.join(" · ") : "0";
  }
  return "–";
}
