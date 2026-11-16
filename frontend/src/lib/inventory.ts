import { apiGet } from "./api";
import { useLoad } from "./hooks";
import { isMissing } from "./format";
import type { Episode, FieldState, Inventory, Season, Series } from "./types";

export type EpisodeField = "episode.title" | "episode.overview";

// Der Sonarr-Bestand wird nur im Speicher dieser Sitzung gehalten; der Server cacht zusätzlich.
let cache: Inventory | null = null;
let pending: Promise<Inventory> | null = null;

export function invalidateInventory(): void {
  cache = null;
}

export function loadInventory(): Promise<Inventory> {
  if (cache) return Promise.resolve(cache);
  if (!pending) {
    pending = apiGet<Inventory>("/api/v1/series").then(
      (data) => {
        cache = data;
        pending = null;
        return data;
      },
      (error: unknown) => {
        pending = null;
        throw error;
      },
    );
  }
  return pending;
}

export function useInventory() {
  const state = useLoad(loadInventory, []);
  return {
    ...state,
    reload: () => {
      invalidateInventory();
      state.reload();
    },
  };
}

export function episodeState(episode: Episode, field: EpisodeField, language: string): FieldState | undefined {
  return episode.fieldStatus?.[field]?.[language];
}

export function countMissingEpisodes(series: Series, field: EpisodeField, language: string): number {
  let total = 0;
  for (const season of series.seasons) {
    for (const episode of season.episodes) {
      if (isMissing(episodeState(episode, field, language))) total += 1;
    }
  }
  return total;
}

export function seasonGapCount(season: Season, languages: string[]): number {
  let total = 0;
  for (const episode of season.episodes) {
    for (const language of languages) {
      if (isMissing(episodeState(episode, "episode.title", language))) total += 1;
      if (isMissing(episodeState(episode, "episode.overview", language))) total += 1;
    }
  }
  return total;
}

export function seriesGapCount(series: Series): number {
  let total = 0;
  for (const language of series.targetLanguages) {
    total += countMissingEpisodes(series, "episode.title", language);
    total += countMissingEpisodes(series, "episode.overview", language);
    if (isMissing(series.fieldStatus?.["series.title"]?.[language])) total += 1;
    if (isMissing(series.fieldStatus?.["series.overview"]?.[language])) total += 1;
  }
  return total;
}
