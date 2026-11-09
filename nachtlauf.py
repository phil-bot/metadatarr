"""tba-tickets – Nächtliche Hintergrundsuche."""
import time
from datetime import datetime, timedelta
from config import NIGHTLY_HOUR, SONARR_API_KEY, SONARR_URL, TMDB_API_KEY, TVDB_STATUS_NIGHTLY
from sonarr import get_data
from speicher import cache_put
from ticket import refresh_tvdb_statuses
from vorschlaege import NIGHTLY_STAGES, search_chain


def seconds_until_next_run(now=None):
    now = now or datetime.now()
    target = now.replace(hour=NIGHTLY_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def redact(text):
    return text.replace(TMDB_API_KEY, "***") if TMDB_API_KEY else text


def run_nightly():
    """Sucht nachts für alle Serien mit TBA-Folgen neu in Mediathek und TMDB (ohne Zwischenspeicher).

    Meldungen enthalten keine Schlüssel. Ein Fehler bei einer Serie bricht den Lauf nicht ab.
    """
    if not SONARR_URL or not SONARR_API_KEY:
        print("Nachtlauf übersprungen: Sonarr ist nicht konfiguriert.", flush=True)
        return
    data = get_data(force=True)
    done = 0
    for s in data["series"]:
        if not any(e.get("airDate") for e in s["tba"]):
            continue
        try:
            counts, note = search_chain(s, None, force=True, stages=NIGHTLY_STAGES)
            done += 1
            print(f"Nachtlauf {s['title']}: {counts}. {note}".rstrip(), flush=True)
        except Exception as exc:  # noqa: BLE001 - Lauf geht bei Fehlern weiter
            print(f"Nachtlauf: {s['title']} fehlgeschlagen: "
                  f"{type(exc).__name__}: {redact(str(exc))}", flush=True)
    cache_put("nightly", {"time": time.time(), "rows": done})
    print(f"Nachtlauf beendet: {done} Serien geprüft.", flush=True)
    if TVDB_STATUS_NIGHTLY:
        print(refresh_tvdb_statuses(), flush=True)


def nightly_loop():
    while True:
        time.sleep(seconds_until_next_run())
        try:
            run_nightly()
        except Exception as exc:  # noqa: BLE001 - die Schleife darf nicht sterben
            print(f"Nachtlauf fehlgeschlagen: {type(exc).__name__}: {redact(str(exc))}", flush=True)
