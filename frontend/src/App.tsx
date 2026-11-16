import { useEffect, useState } from "react";
import { applyTheme, readTheme, type ThemeChoice } from "./lib/theme";
import { usePath } from "./lib/router";
import { Link } from "./components/ui";
import Overview from "./pages/Overview";
import SeriesList from "./pages/SeriesList";
import SeriesDetail from "./pages/SeriesDetail";
import Missing from "./pages/Missing";
import Sources from "./pages/Sources";
import Tickets from "./pages/Tickets";
import TicketDetail from "./pages/TicketDetail";
import Actions from "./pages/Actions";
import Settings from "./pages/Settings";
import Diagnose from "./pages/Diagnose";

const NAV: { to: string; label: string }[] = [
  { to: "/", label: "Übersicht" },
  { to: "/serien", label: "Serien" },
  { to: "/fehlend", label: "Fehlende Metadaten" },
  { to: "/quellen", label: "Quellen" },
  { to: "/tickets", label: "Tickets" },
  { to: "/aktionen", label: "TVDB-Aktionen" },
  { to: "/einstellungen", label: "Einstellungen" },
  { to: "/diagnose", label: "Diagnose" },
];

const THEME_LABEL: Record<ThemeChoice, string> = { system: "System", light: "Hell", dark: "Dunkel" };

function renderRoute(path: string) {
  const seriesMatch = /^\/serien\/(\d+)$/.exec(path);
  if (seriesMatch) return <SeriesDetail id={Number(seriesMatch[1])} />;
  const ticketMatch = /^\/tickets\/(\d+)$/.exec(path);
  if (ticketMatch) return <TicketDetail id={Number(ticketMatch[1])} />;
  switch (path) {
    case "/":
      return <Overview />;
    case "/serien":
      return <SeriesList />;
    case "/fehlend":
      return <Missing />;
    case "/quellen":
      return <Sources />;
    case "/tickets":
      return <Tickets />;
    case "/aktionen":
      return <Actions />;
    case "/einstellungen":
      return <Settings />;
    case "/diagnose":
      return <Diagnose />;
    default:
      return (
        <>
          <h1>Seite nicht gefunden</h1>
          <p>
            <Link to="/" className="btn quiet">
              Zur Übersicht
            </Link>
          </p>
        </>
      );
  }
}

export default function App() {
  const path = usePath();
  const [theme, setTheme] = useState<ThemeChoice>(readTheme);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => setMenuOpen(false), [path]);

  const current = NAV.find((item) => item.to === path)?.label ?? "Seite nicht gefunden";
  useEffect(() => {
    document.title = `${current} · Metadatarr`;
  }, [current]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Zum Inhalt springen
      </a>
      <aside className={`sidebar ${menuOpen ? "open" : ""}`} id="site-nav" aria-label="Hauptnavigation">
        <Link to="/" className="brand" title="Metadatarr Übersicht">
          <img src="/app/brand/metadatarr-icon.svg" alt="" width="36" height="36" />
          <span>Metadatarr</span>
        </Link>
        <nav>
          <ul className="nav-list">
            {NAV.map((item) => {
              const active = path === item.to || (item.to !== "/" && path.startsWith(`${item.to}/`));
              return (
                <li key={item.to}>
                  <Link to={item.to} className={active ? "nav-link active" : "nav-link"}>
                    <span aria-current={active ? "page" : undefined}>{item.label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>
      <div className="content">
        <header className="topbar">
          <button
            type="button"
            className="menu-toggle"
            aria-expanded={menuOpen}
            aria-controls="site-nav"
            onClick={() => setMenuOpen((value) => !value)}
          >
            Menü
          </button>
          <span className="topbar-title">{current}</span>
          <label className="theme-picker">
            <span className="sr-only">Farbschema</span>
            <select value={theme} onChange={(event) => setTheme(event.target.value as ThemeChoice)}>
              {(Object.keys(THEME_LABEL) as ThemeChoice[]).map((choice) => (
                <option key={choice} value={choice}>
                  {THEME_LABEL[choice]}
                </option>
              ))}
            </select>
          </label>
        </header>
        <main id="main" tabIndex={-1} className="main" key={path}>
          {renderRoute(path)}
        </main>
        <footer className="footer">Metadatarr · Lesender Sonarr-Zugriff · Schreibt nur lokale Einstellungen und Tickets</footer>
      </div>
    </div>
  );
}
