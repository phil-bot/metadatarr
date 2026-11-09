"""tba-tickets – Einstieg: Start des Servers."""
import threading
from http.server import ThreadingHTTPServer
from config import NIGHTLY_HOUR, NIGHTLY_SEARCH, PORT, SONARR_URL
from nachtlauf import nightly_loop
from speicher import init_db
from web import Handler


def main():
    init_db()
    print(f"tba-tickets lauscht auf Port {PORT}, Sonarr: {SONARR_URL or '<nicht gesetzt>'}", flush=True)
    if NIGHTLY_SEARCH:
        threading.Thread(target=nightly_loop, daemon=True, name="nachtlauf").start()
        print(f"Nachtlauf aktiv: täglich um {NIGHTLY_HOUR:02d}:00 Uhr", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
