import axios, { AxiosError, AxiosHeaders } from 'axios';

// API base URL from environment variable (prefer VITE_API_URL so login and data share one base).
// Use same-origin fallback to avoid mixed-content issues on HTTPS pages.
const API_BASE_URL = String(
  import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL || "/api/v2"
).replace(/\/+$/, "") || "/api/v2";

// Create axios instance with default configuration.
//
// xsrfCookieName / xsrfHeaderName are aligned with Django's
// CsrfViewMiddleware defaults (cookie 'csrftoken' -> header 'X-CSRFToken')
// so that any unsafe (POST/PUT/PATCH/DELETE) request from this client
// automatically includes the CSRF token. Without this, write endpoints
// guarded by SessionAuthentication would 403 even though the user is
// logged in.
//
// `withXSRFToken: true` is REQUIRED on axios 1.x for cross-origin
// requests. The previous behavior — "attach the xsrf header whenever
// withCredentials is true" — was changed in axios 1.x; now the xsrf
// header only auto-attaches for same-origin requests unless
// withXSRFToken is explicitly set. In dev the SPA usually goes through
// the Vite proxy (same-origin) so this wouldn't matter, but in any
// deployment where VITE_API_BASE_URL points at a different host (e.g.
// http://localhost:8000/api/v2 from a Vite server on :5173/:8080)
// axios would otherwise silently drop X-CSRFToken and Django would
// reject the write. See axios v1 release notes / issue #6209.
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  withXSRFToken: true,
  xsrfCookieName: 'csrftoken',
  xsrfHeaderName: 'X-CSRFToken',
  // Do NOT set a default `Content-Type: application/json` here. Axios's
  // default transformRequest checks that header: if it sees `application/json`
  // while `data` is a FormData, it **replaces** the body with
  // `JSON.stringify(formDataToJSON(data))`, which breaks multipart uploads
  // (Django then has no file; serializer reports missing storage_filename,
  // mime_type, etc.). JSON requests still get `application/json` from the
  // built-in transformRequest when posting plain objects.
  timeout: 10000, // 10 second timeout
});

/**
 * Same as axios `resolveConfig`: in the browser, FormData must leave
 * Content-Type unset so the runtime adds `multipart/form-data; boundary=...`.
 */
apiClient.interceptors.request.use((config) => {
  if (typeof FormData !== "undefined" && config.data instanceof FormData) {
    const headers = AxiosHeaders.from(config.headers);
    headers.setContentType(undefined);
    config.headers = headers;
  }
  return config;
});

function extractErrorMessage(data: unknown): string | null {
  if (!data) return null;
  if (typeof data === 'string') {
    const trimmed = data.trim();
    if (/^<!doctype html/i.test(trimmed) || /^<html[\s>]/i.test(trimmed)) {
      return 'The server returned an unexpected error page. Please try again.';
    }
    return trimmed.length > 500 ? `${trimmed.slice(0, 500)}...` : trimmed;
  }
  if (Array.isArray(data)) {
    const first = data.find((v) => typeof v === 'string');
    return typeof first === 'string' ? first : null;
  }
  if (typeof data !== 'object') return null;

  const payload = data as Record<string, unknown>;
  const direct =
    (typeof payload.detail === 'string' && payload.detail) ||
    (typeof payload.message === 'string' && payload.message) ||
    (typeof payload.error === 'string' && payload.error) ||
    (typeof payload.non_field_errors === 'string' && payload.non_field_errors);
  if (direct) return direct;

  if (Array.isArray(payload.non_field_errors)) {
    const first = payload.non_field_errors.find((v) => typeof v === 'string');
    if (typeof first === 'string') return first;
  }

  for (const value of Object.values(payload)) {
    if (typeof value === 'string' && value.trim()) return value;
    if (Array.isArray(value)) {
      const first = value.find((v) => typeof v === 'string' && v.trim());
      if (typeof first === 'string') return first;
    }
  }

  return null;
}

// Response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const parsedMessage = extractErrorMessage(error.response?.data);
    const message = parsedMessage || error.message || 'Network error';

    console.error('API Error:', message);
    // Preserve the HTTP status on the rejected Error so callers can branch on it
    // (e.g. Contribute.tsx shows a tailored toast for 413 oversized uploads).
    // Backward compatible: existing callers that only read `.message` ignore it.
    const normalizedError = new Error(message);
    (normalizedError as { status?: number }).status = error.response?.status;
    return Promise.reject(normalizedError);
  }
);

// Filter options types
export interface ColorOption {
  value: string;
  label: string;
}

export interface FilterOptions {
  colors: ColorOption[];
  states?: { value: string; label: string }[];
  types?: { value: string; label: string }[];
  valuations?: { value: string; label: string }[];
}

/**
 * Returns the current Django csrftoken cookie value, or null if unset.
 * Browsers strip the cookie from client-side reads when it is set with
 * HttpOnly, but Django's default csrftoken cookie is readable so this is
 * the SPA-friendly way to introspect it.
 */
export function getCsrfTokenFromCookie(): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[2]) : null;
}

/**
 * Best-effort: trigger Django to set the csrftoken cookie if it is missing.
 * GET /me/ goes through CsrfViewMiddleware which sets the cookie on its
 * response. Used by callers that are about to issue unsafe writes from a
 * page that hasn't talked to the server yet.
 */
export async function ensureCsrfToken(): Promise<string | null> {
  const existing = getCsrfTokenFromCookie();
  if (existing) return existing;
  try {
    await apiClient.get('/me/');
  } catch {
    // /me/ may 401 for anonymous users — that's fine, the response still
    // sets the csrftoken cookie. Swallow other errors so we don't block
    // the caller's primary request flow.
  }
  return getCsrfTokenFromCookie();
}

// API functions
export const fetchColorOptions = async (): Promise<ColorOption[]> => {
  const response = await apiClient.get<ColorOption[]>('/filters/colors');
  return response.data;
};

export const fetchFilterOptions = async (): Promise<FilterOptions> => {
  const response = await apiClient.get<FilterOptions>('/filters');
  return response.data;
};

export default apiClient;
