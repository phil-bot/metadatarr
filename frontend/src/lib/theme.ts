export type ThemeChoice = "system" | "light" | "dark";

const KEY = "metadatarr-theme";

export function readTheme(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Speicher nicht verfügbar: System verwenden.
  }
  return "system";
}

export function applyTheme(choice: ThemeChoice): void {
  document.documentElement.dataset.theme = choice;
  try {
    if (choice === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, choice);
  } catch {
    // Die Auswahl gilt dann nur für diese Sitzung.
  }
}
