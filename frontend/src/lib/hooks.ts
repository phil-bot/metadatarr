import { useEffect, useState } from "react";

export type LoadState<T> = {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
};

// Lädt Daten neu, wenn sich die Abhängigkeiten oder der Reload-Zähler ändern. Alte Antworten werden verworfen.
export function useLoad<T>(loader: () => Promise<T>, deps: readonly unknown[]): LoadState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    loader().then(
      (value) => {
        if (alive) {
          setData(value);
          setLoading(false);
        }
      },
      (cause: unknown) => {
        if (alive) {
          setError(cause instanceof Error ? cause : new Error("Unbekannter Fehler"));
          setLoading(false);
        }
      },
    );
    return () => {
      alive = false;
    };
    // Der Loader wird bewusst nur über die Abhängigkeiten und den Reload-Zähler neu ausgeführt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  return { data, error, loading, reload: () => setTick((value) => value + 1) };
}
