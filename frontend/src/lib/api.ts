// Small typed client for the FastAPI backend (same origin, proxied by next.config.ts).
import type { components } from "./api-types";

export type Schemas = components["schemas"];

// Every write must carry this header; the backend rejects writes without it (CSRF guard).
export const CSRF_HEADER = { "X-Requested-With": "aise" } as const;

export class ApiError extends Error {
  status: number;
  /** Itemised reasons from the API, e.g. marking-scheme row problems or approval blockers. */
  problems: string[];
  code?: "password_change_required";

  constructor(status: number, message: string, problems: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problems = problems;
    if (status === 403 && message === "password_change_required") {
      this.code = "password_change_required";
    }
  }
}

type ValidationItem = { loc?: (string | number)[]; msg?: string };

/** Turn FastAPI error bodies into one readable message plus a list of problems. */
export function errorFromBody(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return new ApiError(status, detail);
  if (Array.isArray(detail)) {
    // Request validation (422): [{loc, msg}, ...]
    const problems = (detail as ValidationItem[]).map((d) => {
      const field = d.loc?.filter((p) => p !== "body").join(".");
      return field ? `${field}: ${d.msg}` : String(d.msg);
    });
    return new ApiError(status, "Please check the highlighted values.", problems);
  }
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; problems?: string[] };
    return new ApiError(status, d.message ?? "Request failed.", d.problems ?? []);
  }
  return new ApiError(status, `Request failed (${status}).`);
}

type Options = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
};

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const { method = options.json || options.form ? "POST" : "GET", json, form, signal } = options;
  const headers: Record<string, string> = { ...CSRF_HEADER };
  let body: BodyInit | undefined;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = form; // the browser sets the multipart boundary
  }
  const response = await fetch(`/api${path}`, {
    method, headers, body, signal, credentials: "same-origin",
  });
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw errorFromBody(response.status, data);
  return data as T;
}

/** URL of a scanned page image (served only to the script's teacher). */
export function pageUrl(scriptId: number, page: number, thumb = false): string {
  return `/api/my/scripts/${scriptId}/pages/${page}${thumb ? "?size=thumb" : ""}`;
}
