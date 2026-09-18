import type { ApiError, Envelope } from "./types";

export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

const TOKEN_KEY = "docuparse.session";

/**
 * The session token lives in localStorage.
 *
 * Trade-off worth knowing: an httpOnly cookie would survive XSS, this does
 * not. The API issues bearer tokens, and the dashboard renders no
 * user-supplied HTML, so the exposure is small — but it is not zero, and
 * moving to cookie auth is the right next step if this grows.
 */
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (token === null) window.localStorage.removeItem(TOKEN_KEY);
    else window.localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* Private browsing with storage blocked: the session is in-memory only. */
  }
}

/** An API error carrying the server's own code and request id. */
export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details?: Record<string, unknown>;

  constructor(
    status: number,
    error: ApiError,
    requestId: string | null,
  ) {
    super(error.message);
    this.name = "ApiRequestError";
    this.code = error.code;
    this.status = status;
    this.requestId = requestId;
    this.details = error.details;
  }
}

async function parse(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Skip the Authorization header (signup and login). */
  anonymous?: boolean;
  signal?: AbortSignal;
}

async function call(path: string, options: RequestOptions = {}): Promise<unknown> {
  const headers: Record<string, string> = {};
  const token = options.anonymous ? null : getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (options.body instanceof FormData) {
    // Let the browser set the multipart boundary.
    body = options.body;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: options.method ?? "GET",
      headers,
      body,
      signal: options.signal,
    });
  } catch {
    throw new ApiRequestError(
      0,
      {
        code: "network_error",
        message: `Could not reach the API at ${API_URL}. Is it running?`,
      },
      null,
    );
  }

  const payload = await parse(response);
  const requestId = response.headers.get("X-Request-Id");

  if (!response.ok) {
    const envelope = payload as { error?: ApiError } | null;
    throw new ApiRequestError(
      response.status,
      envelope?.error ?? {
        code: "unexpected_error",
        message: `The API returned ${response.status}.`,
      },
      requestId,
    );
  }
  return payload;
}

/** Calls an endpoint that wraps its result in { success, request_id, data }. */
export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return ((await call(path, { signal })) as Envelope<T>).data;
}

export async function apiSend<T>(
  path: string,
  body: unknown,
  method = "POST",
  anonymous = false,
): Promise<T> {
  return ((await call(path, { method, body, anonymous })) as Envelope<T>).data;
}

export async function apiDelete<T>(path: string): Promise<T> {
  return ((await call(path, { method: "DELETE" })) as Envelope<T>).data;
}

/** The extraction endpoint returns its result at the top level, not under `data`. */
export async function apiUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return (await call(path, { method: "POST", body: form })) as T;
}
