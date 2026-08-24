/**
 * State options for filters/contribution forms.
 * Single source: GET /regions/ (supports assigned_only=true for State Editors).
 */
import apiClient from "@/lib/api";

/** One item from GET /regions/ (DRF snake_case) */
export interface RegionApiItem {
  region_id?: number;
  id?: number;
  abbrev?: string | null;
  name?: string | null;
  region_tier?: string | null;
  parent_region_id?: number | null;
  established_date?: string | null;
  defunct_date?: string | null;
}

/** Paginated response from GET /regions/ */
export interface RegionsApiResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: RegionApiItem[];
}

type PagedApiResponse<T> = {
  count?: number;
  next?: string | null;
  previous?: string | null;
  results?: T[];
};

/** Option for state filter dropdown */
export interface StateOption {
  value: string;
  label: string;
}

function getResponseRows<T>(data: unknown): { rows: T[]; next: string | null } {
  if (Array.isArray(data)) {
    return { rows: data as T[], next: null };
  }
  const paged = data as PagedApiResponse<T> | null;
  if (paged && Array.isArray(paged.results)) {
    const next =
      typeof paged.next === "string" && paged.next.trim() !== "" ? paged.next : null;
    return { rows: paged.results, next };
  }
  throw new Error("State options API: invalid response (missing results array)");
}

/**
 * Tiers that are a legitimate answer to "which state/territory?".
 *
 * An allowlist, unlike the backend's Region.SUBREGION_TIERS exclusion, because
 * this list also has to drop COUNTRY ("United States of America" is not a
 * state). The cost of that is the reminder below.
 *
 * ⚠ A future PROVINCE (Canada) must be added here or it will silently not
 * appear in the dropdown.
 *
 * Without this the endpoint returns every Region and the dropdown reads
 * "Accomack, Alabama, Alaska, Albemarle, Alexandria, Alleghany, Amelia…" --
 * 196 entries for 57 real jurisdictions, because the VPHC ingest added 141
 * county rows. (issue #103)
 */
export const STATE_SELECTOR_TIERS = ["STATE", "TERRITORY", "DISTRICT"] as const;

async function collectRegionNames(assignedOnly: boolean): Promise<string[]> {
  const names: string[] = [];
  const params: Record<string, string> = {
    page_size: "500",
    region_tier__in: STATE_SELECTOR_TIERS.join(","),
  };
  if (assignedOnly) params.assigned_only = "true";

  let nextUrl: string | null = "/regions/";
  let useParams: Record<string, string> | undefined = params;
  let safetyCounter = 0;

  while (nextUrl && safetyCounter < 50) {
    const res = await apiClient.get(nextUrl, {
      params: useParams,
      withCredentials: true,
    });
    const { rows, next } = getResponseRows<RegionApiItem>(res.data);
    rows.forEach((row) => {
      const name = String(row.name ?? "").trim();
      if (name) names.push(name);
    });
    nextUrl = next;
    useParams = undefined; // next URL already includes cursor/page
    safetyCounter += 1;
  }

  return names;
}

/**
 * Fetches regions from GET /regions/.
 * Follows pagination so the filter dropdown gets every region.
 * @param assignedOnly - when true, only regions assigned to the current State Editor.
 */
export async function getRegions(assignedOnly?: boolean): Promise<StateOption[]> {
  const collected = await collectRegionNames(Boolean(assignedOnly));

  const seen = new Set<string>();
  return collected
    .map((name) => String(name).trim())
    .filter((name) => {
      if (!name || seen.has(name)) return false;
      seen.add(name);
      return true;
    })
    .map((name) => ({ value: name, label: name }))
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
}

/**
 * Fetches only the regions assigned to the current user.
 */
export async function getAssignedRegions(): Promise<StateOption[]> {
  return getRegions(true);
}
