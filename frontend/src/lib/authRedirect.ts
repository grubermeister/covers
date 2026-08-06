type AuthLocationState = {
  from?: string | {
    pathname?: string;
    search?: string;
    hash?: string;
  };
};

function sanitizeRedirectPath(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//")) return "/";
  const origin = typeof window === "undefined" ? "http://localhost" : window.location.origin;
  try {
    const url = new URL(value, origin);
    if (url.origin !== origin) return "/";
    const path = `${url.pathname}${url.search}${url.hash}`;
    return path.startsWith("/") && !path.startsWith("//") ? path : "/";
  } catch {
    return "/";
  }
}

function safeUrlSuffix(value: unknown, prefix: "?" | "#"): string {
  if (typeof value !== "string" || value === "") return "";
  return value.startsWith(prefix) ? value : "";
}

export function getRedirectPath(state: unknown): string {
  const from = (state as AuthLocationState | null)?.from;
  if (typeof from === "string") {
    return sanitizeRedirectPath(from);
  }
  if (!from || typeof from !== "object") return "/";
  const pathname = from.pathname || "/";
  const search = safeUrlSuffix(from.search, "?");
  const hash = safeUrlSuffix(from.hash, "#");
  return sanitizeRedirectPath(`${pathname}${search}${hash}`);
}
