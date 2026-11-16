export type FieldState = {
  status: string;
  reason: string;
  targetLanguage: string;
  sourceLanguage: string | null;
  ruleId: string | null;
};

export type FieldMap = Record<string, Record<string, FieldState>>;

export type Episode = {
  id: number | null;
  season: number;
  episode: number;
  title: string | null;
  overview: string | null;
  airDate: string;
  hasFile: boolean;
  tvdbId: number | null;
  monitored: boolean | null;
  fieldStatus?: FieldMap;
};

export type Season = {
  seasonNumber: number;
  episodeCount: number;
  episodes: Episode[];
};

export type Series = {
  id: number;
  title: string;
  overview: string | null;
  year: number | null;
  network: string;
  status: string;
  tvdbId: number | null;
  originalLanguage: string;
  seasons: Season[];
  episodeCount: number;
  targetLanguages: string[];
  targetLanguagesOverride: boolean;
  fieldStatus: FieldMap;
};

export type Inventory = {
  series: Series[];
  seriesTotal: number;
  fetched: string | null;
  targetLanguages: string[];
};

export type Ticket = {
  id: number;
  seriesId: number;
  seriesTitle: string;
  season: number;
  language: string;
  status: string;
  tvdbNumber: number | null;
  tvdbStatus: string | null;
  itemCount: number;
  created: number | string | null;
  updated: number | string | null;
};

export type TicketItem = {
  suggestionId: number | null;
  season: number;
  episode: number;
  title: string;
};

export type TicketDetail = {
  id: number;
  seriesId: number;
  seriesTitle: string;
  tvdbId: number | null;
  season: number;
  language: string;
  status: string;
  subject: string;
  body: string;
  tvdbNumber: number | null;
  tvdbStatus: string | null;
  items: TicketItem[];
};

export type SourceInfo = {
  id: string;
  name: string;
  enabled: boolean;
  languages: string[];
  includeInNightly: boolean;
  hosts: string[];
  credentialsConfigured: boolean;
  nightlyCapable: boolean;
};

export type ActionInfo = {
  id: string;
  enabled: boolean;
  credentialsConfigured: boolean;
  requiresPreview: boolean;
  requiresConfirmation: boolean;
  idempotencyProtected: boolean;
};

export type RuleInfo = {
  id: string;
  field: string;
  language: string | null;
  kind: "empty" | "exact" | "regex";
  pattern: string;
  enabled: boolean;
  origin: string;
  editable: boolean;
};

export type SearchResult = {
  seriesId: number;
  count?: unknown;
  note?: string;
  sources?: { id: string; count: unknown; note?: string }[];
};

export type Msg = { tone: "ok" | "warn" | "error"; text: string };
