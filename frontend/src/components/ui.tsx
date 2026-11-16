import { useState, type ReactNode } from "react";
import { ApiError } from "../lib/api";
import { href, navigate } from "../lib/router";
import { STATUS_LABEL, statusTone } from "../lib/format";
import type { FieldState } from "../lib/types";

export function Link({ to, className, children, title }: { to: string; className?: string; children: ReactNode; title?: string }) {
  return (
    <a
      href={href(to)}
      className={className}
      title={title}
      onClick={(event) => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

export function PageHeader({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "ok" | "warn" | "error"; children: ReactNode }) {
  return (
    <div className={`notice ${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Loading({ label = "Wird geladen …" }: { label?: string }) {
  return (
    <p className="loading" role="status" aria-live="polite">
      {label}
    </p>
  );
}

export function ErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const detail = error instanceof ApiError && error.code ? `${error.message} (${error.code})` : error.message;
  return (
    <div className="notice error" role="alert">
      <p>
        <strong>Die Daten konnten nicht geladen werden.</strong>
      </p>
      <p className="small">{detail}</p>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Erneut versuchen
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Chip({ tone = "", children, title }: { tone?: "ok" | "warn" | "err" | "busy" | ""; children: ReactNode; title?: string }) {
  return <span className={`chip ${tone}`.trim()} title={title}>{children}</span>;
}

export function StatusChip({ state, prefix }: { state: FieldState | undefined; prefix?: string }) {
  if (!state) return <Chip>{prefix ? `${prefix}: –` : "–"}</Chip>;
  const label = STATUS_LABEL[state.status] ?? state.status;
  return (
    <Chip tone={statusTone(state.status)} title={state.reason}>
      {prefix ? `${prefix}: ${label}` : label}
    </Chip>
  );
}

export function Poster({ seriesId, title, large = false }: { seriesId: number; title: string; large?: boolean }) {
  const [failed, setFailed] = useState(false);
  const initials =
    title
      .split(/\s+/)
      .filter((word) => word.length > 0)
      .slice(0, 2)
      .map((word) => word.charAt(0).toUpperCase())
      .join("") || "?";
  const className = large ? "poster poster-lg" : "poster";
  if (failed) {
    return (
      <span className={`${className} poster-fallback`} aria-hidden="true" title="Poster nicht verfügbar">
        {initials}
      </span>
    );
  }
  return <img className={className} src={`/poster/${seriesId}`} alt="" loading="lazy" onError={() => setFailed(true)} />;
}
