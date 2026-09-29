/**
 * The single HTTP client every API module goes through.
 *
 * It owns the base URL, query serialization, timeouts, JSON parsing and — most
 * importantly — turning the backend's error envelope into a typed `ApiError`,
 * so no component ever has to inspect a status code or a raw response body.
 *
 * Authentication is a seam here, mirroring `require_principal()` on the
 * backend: `setAuthTokenProvider` is the one place a future credential gets
 * attached to every request.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

const DEFAULT_TIMEOUT_MS = 30_000;

/** A typed backend failure. `code` is stable; `message` is safe to display. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(
    message: string,
    options: {
      code?: string;
      status?: number;
      details?: Record<string, unknown>;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.code = options.code ?? "unknown_error";
    this.status = options.status ?? 0;
    this.details = options.details ?? {};
  }

  /** True when retrying the same request could plausibly succeed. */
  get isRetryable(): boolean {
    return (
      this.status === 0 ||
      this.status === 408 ||
      this.status === 429 ||
      this.status >= 500
    );
  }

  /** True when the backend is up but a dependency (the model) is not wired. */
  get isConfiguration(): boolean {
    return this.code === "configuration_error";
  }
}

export type QueryValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | Array<string | number>;

export type QueryParams = Record<string, QueryValue>;

/** Drops empty values so `?status=` never reaches the backend. */
export function buildQuery(params?: QueryParams): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      value.forEach((entry) => search.append(key, String(entry)));
    } else {
      search.append(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

type TokenProvider = () => string | null;

let authTokenProvider: TokenProvider = () => null;

/**
 * Register how requests get their credential.
 *
 * `installOperatorToken()` in `@/lib/auth` is what calls this. The indirection
 * earns its place by keeping this module free of any opinion about where a
 * token is kept — it is equally happy with a browser store, a cookie or a
 * session exchanged with the server.
 */
export function setAuthTokenProvider(provider: TokenProvider): void {
  authTokenProvider = provider;
}

type UnauthorizedHandler = () => void;

let onUnauthorized: UnauthorizedHandler = () => {};

/**
 * Called whenever the server answers 401.
 *
 * A credential the server has stopped accepting — rotated, mistyped, expired —
 * is worse than none: every panel fails with its own error and none of them
 * says the token is the problem. One handler, registered here, lets the app
 * discard it and ask again.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  query?: QueryParams;
  body?: unknown;
  /**
   * A body sent as-is, with `contentType`, instead of JSON.
   *
   * Audio is the reason this exists: base64 inside JSON would add a third to
   * the size of every recording on the operator's uplink, and multipart would
   * cost the backend a dependency it has no other use for. Everything else —
   * the base URL, the credential, the timeout, the 401 handling — is shared.
   */
  rawBody?: BodyInit;
  contentType?: string;
  /** Return the response body untouched rather than parsing it as JSON. */
  responseType?: "json" | "blob";
  /** Extra request headers. The credential is added separately, and wins. */
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
}

/** Absolute URL for a backend path — also used to open an `EventSource`. */
export function apiUrl(path: string, query?: QueryParams): string {
  return `${API_BASE_URL}${path}${buildQuery(query)}`;
}

async function parseError(response: Response): Promise<ApiError> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  const envelope =
    payload && typeof payload === "object" && "error" in payload
      ? (payload as { error: Record<string, unknown> }).error
      : null;

  if (envelope) {
    return new ApiError(String(envelope.message ?? response.statusText), {
      code: String(envelope.code ?? "unknown_error"),
      status: response.status,
      details: (envelope.details as Record<string, unknown>) ?? {},
    });
  }

  // FastAPI validation errors do not use the envelope.
  if (
    payload &&
    typeof payload === "object" &&
    "detail" in payload &&
    response.status === 422
  ) {
    return new ApiError("The request was rejected as invalid.", {
      code: "validation_error",
      status: 422,
      details: { detail: (payload as { detail: unknown }).detail },
    });
  }

  return new ApiError(
    response.statusText || `Request failed with status ${response.status}`,
    { status: response.status },
  );
}

/** Perform a request and return the parsed body. Throws `ApiError` on failure. */
export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = "GET",
    query,
    body,
    rawBody,
    contentType,
    responseType = "json",
    headers: extraHeaders,
    signal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  } = options;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // Caller-driven cancellation (a component unmounting) must also abort.
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...extraHeaders,
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (rawBody !== undefined && contentType) headers["Content-Type"] = contentType;
  const token = authTokenProvider();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(apiUrl(path, query), {
      method,
      headers,
      body:
        rawBody !== undefined
          ? rawBody
          : body === undefined
            ? undefined
            : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
  } catch (error) {
    clearTimeout(timer);
    if (signal?.aborted) throw error;
    const aborted = (error as Error)?.name === "AbortError";
    throw new ApiError(
      aborted
        ? "The request timed out."
        : "Could not reach the ULUGBEK AI backend.",
      { code: aborted ? "timeout" : "network_error" },
    );
  }
  clearTimeout(timer);

  if (response.status === 401) {
    onUnauthorized();
    throw await parseError(response);
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  if (responseType === "blob") return (await response.blob()) as T;
  return (await response.json()) as T;
}
