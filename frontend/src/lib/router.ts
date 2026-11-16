import { useSyncExternalStore } from "react";

// Die Oberfläche läuft unter /app/. Die Routen sind relativ dazu.
export const BASE_PATH = "/app";
const NAVIGATE_EVENT = "metadatarr:navigate";

function currentPath(): string {
  const raw = window.location.pathname;
  const relative = raw.startsWith(BASE_PATH) ? raw.slice(BASE_PATH.length) : raw;
  return relative.replace(/\/+$/, "") || "/";
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("popstate", callback);
  window.addEventListener(NAVIGATE_EVENT, callback);
  return () => {
    window.removeEventListener("popstate", callback);
    window.removeEventListener(NAVIGATE_EVENT, callback);
  };
}

export function usePath(): string {
  return useSyncExternalStore(subscribe, currentPath, currentPath);
}

export function href(path: string): string {
  return `${BASE_PATH}${path}`;
}

export function navigate(path: string): void {
  const target = href(path);
  if (window.location.pathname !== target) window.history.pushState({}, "", target);
  window.dispatchEvent(new Event(NAVIGATE_EVENT));
  window.scrollTo({ top: 0 });
}
