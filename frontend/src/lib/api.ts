export type Problem = { title?: string; detail?: string; code?: string };

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unbekannter Fehler";
}

async function readPayload(response: Response): Promise<{ text: string; json: unknown }> {
  const text = await response.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  return { text, json };
}

function toError(response: Response, json: unknown): ApiError {
  const problem: Problem = json && typeof json === "object" ? (json as Problem) : {};
  const message = problem.detail || problem.title || `Anfrage fehlgeschlagen (HTTP ${response.status})`;
  return new ApiError(response.status, problem.code || "http_error", message);
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    cache: "no-store",
    credentials: "same-origin",
  });
  const { json } = await readPayload(response);
  if (!response.ok) throw toError(response, json);
  return json as T;
}

// Das CSRF-Token wird nur im Speicher dieser Sitzung gehalten, nie in Speicher oder Logs.
let csrfToken: string | null = null;
let sessionRequest: Promise<string> | null = null;

function session(force: boolean): Promise<string> {
  if (csrfToken && !force) return Promise.resolve(csrfToken);
  if (!sessionRequest || force) {
    sessionRequest = apiGet<{ csrfToken?: string }>("/api/v1/session").then(
      (payload) => {
        if (!payload.csrfToken) throw new ApiError(500, "no_csrf", "Sitzung konnte nicht gestartet werden.");
        csrfToken = payload.csrfToken;
        sessionRequest = null;
        return payload.csrfToken;
      },
      (error: unknown) => {
        sessionRequest = null;
        throw error;
      },
    );
  }
  return sessionRequest;
}

export async function apiWrite<T>(method: "POST" | "PUT" | "DELETE", path: string, body: unknown = {}): Promise<T> {
  const send = async (force: boolean): Promise<Response> => {
    const token = await session(force);
    return fetch(path, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify(body),
    });
  };
  let response = await send(false);
  let payload = await readPayload(response);
  if (response.status === 403 && payload.text.includes("csrf_rejected")) {
    response = await send(true);
    payload = await readPayload(response);
  }
  if (!response.ok) throw toError(response, payload.json);
  return payload.json as T;
}
