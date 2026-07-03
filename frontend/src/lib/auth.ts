/**
 * Auth utilities using localStorage for session storage.
 */

const AUTH_STORAGE_KEY = "worldcovers_user";

export interface AssignedCollectionRegion {
  id: number;
  name: string;
  abbrev?: string;
}

export interface AssignedCollection {
  id: number;
  name: string;
  region: AssignedCollectionRegion;
}

export type UserRole = "guest" | "contributor" | "editor" | "administrator";

export interface AuthUser {
  id: number;
  username: string;
  email: string;
  is_staff: boolean;
  is_superuser?: boolean;
  // Backend-derived role string. Administrator === is_superuser (single person, by design).
  role?: UserRole | string;
  // For editors: the Collections they curate. Empty for non-editors.
  assigned_collections?: AssignedCollection[];
}

/**
 * Normalize the auth payload coming from the backend before we hand it to the
 * rest of the SPA. The Django auth view returns role="state_editor" for users
 * in the Editors group, but every UI gate in the SPA (RecordDetail.tsx,
 * Navigation.tsx, Dashboard.tsx, ContributionDetail.tsx, ...) checks
 * `role === "editor"`. We do the rename in one place here so individual
 * components don't need to know about the legacy string.
 */
function normalizeAuthUser<T extends AuthUser | null | undefined>(user: T): T {
  if (!user || typeof user !== "object") return user;
  const role = (user as AuthUser).role;
  if (role === "state_editor") {
    return { ...(user as AuthUser), role: "editor" } as T;
  }
  return user;
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthUser;
    if (!parsed || typeof parsed.id !== "number") return null;
    return normalizeAuthUser(parsed);
  } catch {
    return null;
  }
}

export function setStoredUser(user: AuthUser): void {
  const normalized = normalizeAuthUser(user) ?? user;
  localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(normalized));
  window.dispatchEvent(new CustomEvent("auth-change", { detail: normalized }));
}

export function clearStoredUser(): void {
  localStorage.removeItem(AUTH_STORAGE_KEY);
  window.dispatchEvent(new CustomEvent("auth-change", { detail: null }));
}

/** Base URL for API (same as login). No trailing slash. */
function getApiBase(): string {
  const base = import.meta.env.VITE_API_URL || "";
  return base ? String(base).replace(/\/+$/, "") : "";
}

type CachedCurrentUser = { user: AuthUser | null; fetchedAtMs: number };

// Dedupes /api/me/ across the whole app (Navigation, Dashboard, etc.)
let currentUserInFlight: Promise<AuthUser | null> | null = null;
let currentUserCache: CachedCurrentUser | null = null;
const CURRENT_USER_CACHE_TTL_MS = 30_000;

/**
 * Fetch current user from the server (session). Use to sync role and assigned_collections
 * so the UI shows Editor with correct Collections instead of defaulting to Contributor.
 */
export async function fetchCurrentUser(): Promise<AuthUser | null> {
  const now = Date.now();
  if (currentUserCache && now - currentUserCache.fetchedAtMs < CURRENT_USER_CACHE_TTL_MS) {
    return currentUserCache.user;
  }

  if (currentUserInFlight) return currentUserInFlight;

  const url = getApiBase() ? `${getApiBase()}/me/` : (import.meta.env.VITE_API_BASE_URL || "/api/v2") + "/me/";
  currentUserInFlight = (async () => {
    try {
      const res = await fetch(url, { credentials: "include" });
      if (!res.ok) return null;
      const data = (await res.json()) as { user?: AuthUser };
      const user = data?.user;
      if (!user || typeof user.id !== "number") return null;
      return normalizeAuthUser(user);
    } finally {
      // Clear inflight no matter what.
      currentUserInFlight = null;
    }
  })();

  const user = await currentUserInFlight;
  currentUserCache = { user, fetchedAtMs: now };
  return user;
}
