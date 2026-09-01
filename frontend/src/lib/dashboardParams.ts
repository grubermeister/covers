/**
 * URL-search-param backing for the Dashboard's view state (Issue #87).
 *
 * The dashboard's page, page size, sort and filters used to live in plain
 * `useState`, so reviewing a single entry threw the editor back to a default
 * view -- page 1, 10 per page, filters cleared. Putting that state in the URL
 * makes it survive the round trip to a detail screen, a browser reload and the
 * back button, and makes a dashboard view shareable.
 *
 * The two tabs keep independent state, so editor-tab keys are prefixed `e_`.
 * Every field defaults to the value the dashboard starts with and is omitted
 * from the URL when it holds that default, so a clean dashboard is `/dashboard`
 * with no query string.
 */

export type DashboardTab = "submissions" | "editor";
export type SortDir = "asc" | "desc";
export type DashboardSortField =
  | "status"
  | "state"
  | "town"
  | "shape"
  | "color"
  | "submitted";
export type SortEntry = { field: DashboardSortField; dir: SortDir };

const SORT_FIELDS: readonly DashboardSortField[] = [
  "status",
  "state",
  "town",
  "shape",
  "color",
  "submitted",
];

export const DEFAULT_DASHBOARD_SORT: SortEntry[] = [{ field: "submitted", dir: "desc" }];
export const DEFAULT_PAGE_SIZE = 10;

/** Read a single search param with a default. Shared with Catalog Search. */
export function getSearchParam(
  params: URLSearchParams,
  key: string,
  defaultValue: string,
): string {
  const v = params.get(key);
  return v ?? defaultValue;
}

/**
 * Submission source for the editor queue (issue #136). Mirrors the backend's
 * ContributionListFilter.source choices; the union is what keeps a hand-edited
 * `?e_source=` out of a request the API would reject.
 *
 * "all" is the default. It used to be "vphc" -- issue #101's guard, from when
 * the ingest was live -- but a default that hides rows reads as missing data,
 * which is exactly how it read once the ingest had drained.
 */
export type DashboardSource = "vphc" | "human" | "all";

const SOURCES: readonly DashboardSource[] = ["vphc", "human", "all"];

export const DEFAULT_SOURCE: DashboardSource = "all";

function parseSource(raw: string | null): DashboardSource {
  return SOURCES.includes(raw as DashboardSource) ? (raw as DashboardSource) : DEFAULT_SOURCE;
}

/** Per-tab filter/sort/pagination state. One shape, used twice. */
export interface DashboardTabParams {
  q: string;
  status: string;
  state: string;
  town: string;
  shape: string;
  color: string;
  /** Submission source; editor tab only, ignored by My Submissions. */
  source: DashboardSource;
  from: string;
  to: string;
  sort: SortEntry[];
  page: number;
  pageSize: number;
}

export interface DashboardParams {
  tab: DashboardTab;
  submissions: DashboardTabParams;
  editor: DashboardTabParams;
}

export function defaultTabParams(): DashboardTabParams {
  return {
    q: "",
    status: "all",
    state: "all",
    town: "",
    shape: "all",
    color: "all",
    source: DEFAULT_SOURCE,
    from: "",
    to: "",
    sort: [...DEFAULT_DASHBOARD_SORT],
    page: 1,
    pageSize: DEFAULT_PAGE_SIZE,
  };
}

/**
 * Serialize the dashboard's single-column sort. The empty list is a real user
 * state (they toggled the active arrow off, returning to API order), so it
 * persists as the sentinel "none" rather than as an absent param -- otherwise a
 * reload would silently restore the default sort.
 */
export function serializeSort(entries: SortEntry[]): string {
  if (entries.length === 0) return "none";
  return entries.map((e) => `${e.field}_${e.dir}`).join(",");
}

/**
 * Sort field -> the API's public `?ordering=` key. The wire spelling is not
 * always the display one: the serializer emits `created_at`, not `submitted`.
 * See ContributionViewSet.ordering_aliases, which is the other half of this.
 */
const EDITOR_ORDERING_KEY: Record<DashboardSortField, string> = {
  status: "status",
  state: "state",
  town: "town",
  shape: "shape",
  color: "color",
  submitted: "created_at",
};

/**
 * The `?ordering=` value for the editor queue, or undefined for "server's
 * choice" -- which is what the empty-list sentinel means (see serializeSort).
 *
 * `id` is appended as a final key. Without a unique one, rows that tie on the
 * chosen column swap places between page requests under LIMIT/OFFSET, so one
 * submission is served twice and another is unreachable (DRF #6886). The
 * backend appends it too, for clients that forget; sending it keeps the intent
 * visible in the request.
 */
export function editorOrderingParam(entries: SortEntry[]): string | undefined {
  if (entries.length === 0) return undefined;
  const keys = entries.map((e) => `${e.dir === "desc" ? "-" : ""}${EDITOR_ORDERING_KEY[e.field]}`);
  return [...keys, "id"].join(",");
}

export function parseSort(raw: string | null): SortEntry[] {
  if (raw === null) return [...DEFAULT_DASHBOARD_SORT];
  if (raw === "none" || raw === "") return [];
  const entries: SortEntry[] = [];
  const seen = new Set<DashboardSortField>();
  for (const token of raw.split(",")) {
    const t = token.trim();
    if (!t) continue;
    const sep = t.lastIndexOf("_");
    if (sep <= 0) continue;
    const field = t.slice(0, sep) as DashboardSortField;
    const dir = t.slice(sep + 1);
    if (!SORT_FIELDS.includes(field) || (dir !== "asc" && dir !== "desc")) continue;
    if (seen.has(field)) continue;
    seen.add(field);
    entries.push({ field, dir });
  }
  // An entirely unparseable value is treated as "no param" rather than as an
  // explicit empty sort, so a mangled URL still shows a sensibly ordered list.
  return entries.length === 0 ? [...DEFAULT_DASHBOARD_SORT] : entries;
}

function isDefaultSort(entries: SortEntry[]): boolean {
  return (
    entries.length === DEFAULT_DASHBOARD_SORT.length &&
    entries.every(
      (e, i) =>
        e.field === DEFAULT_DASHBOARD_SORT[i].field && e.dir === DEFAULT_DASHBOARD_SORT[i].dir,
    )
  );
}

/** Positive integer or the fallback; guards against "?page=abc" and "?page=-3". */
function parsePositiveInt(raw: string | null, fallback: number): number {
  if (raw === null) return fallback;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 1) return fallback;
  return n;
}

const EDITOR_PREFIX = "e_";

function readTab(params: URLSearchParams, prefix: string): DashboardTabParams {
  const k = (key: string) => `${prefix}${key}`;
  return {
    q: getSearchParam(params, k("q"), ""),
    status: getSearchParam(params, k("status"), "all"),
    state: getSearchParam(params, k("state"), "all"),
    town: getSearchParam(params, k("town"), ""),
    shape: getSearchParam(params, k("shape"), "all"),
    color: getSearchParam(params, k("color"), "all"),
    source: parseSource(params.get(k("source"))),
    from: getSearchParam(params, k("from"), ""),
    to: getSearchParam(params, k("to"), ""),
    sort: parseSort(params.get(k("order"))),
    page: parsePositiveInt(params.get(k("page")), 1),
    pageSize: parsePositiveInt(params.get(k("pageSize")), DEFAULT_PAGE_SIZE),
  };
}

function writeTab(out: URLSearchParams, prefix: string, tab: DashboardTabParams): void {
  const k = (key: string) => `${prefix}${key}`;
  if (tab.q.trim()) out.set(k("q"), tab.q);
  if (tab.status !== "all") out.set(k("status"), tab.status);
  if (tab.state !== "all") out.set(k("state"), tab.state);
  if (tab.town.trim()) out.set(k("town"), tab.town);
  if (tab.shape !== "all") out.set(k("shape"), tab.shape);
  if (tab.color !== "all") out.set(k("color"), tab.color);
  if (tab.source !== DEFAULT_SOURCE) out.set(k("source"), tab.source);
  if (tab.from) out.set(k("from"), tab.from);
  if (tab.to) out.set(k("to"), tab.to);
  if (!isDefaultSort(tab.sort)) out.set(k("order"), serializeSort(tab.sort));
  if (tab.pageSize !== DEFAULT_PAGE_SIZE) out.set(k("pageSize"), String(tab.pageSize));
  if (tab.page > 1) out.set(k("page"), String(tab.page));
}

export function parseDashboardParams(params: URLSearchParams): DashboardParams {
  return {
    tab: params.get("tab") === "editor" ? "editor" : "submissions",
    submissions: readTab(params, ""),
    editor: readTab(params, EDITOR_PREFIX),
  };
}

export function buildDashboardParams(state: DashboardParams): URLSearchParams {
  const out = new URLSearchParams();
  if (state.tab === "editor") out.set("tab", "editor");
  writeTab(out, "", state.submissions);
  writeTab(out, EDITOR_PREFIX, state.editor);
  return out;
}

/**
 * Last dashboard view the user was looking at, in sessionStorage.
 *
 * Screens an editor reaches from the dashboard (contribution detail, record
 * detail, the edit forms) are several navigations deep and cannot thread the
 * dashboard's query string through location state reliably -- it is lost on
 * reload and on any redirect. Reading it back from here means every "back to
 * dashboard" path returns to the same view without each caller having to know
 * what that view was.
 */
const STORAGE_KEY = "worldcovers.dashboard.lastView";

export function rememberDashboardLocation(search: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, search.replace(/^\?/, ""));
  } catch {
    // Private-browsing / storage-disabled: fall back to the plain dashboard.
  }
}

function readRememberedSearch(): string {
  try {
    return sessionStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

/**
 * Path to return an editor to. Pass an explicit search string (from the
 * dashboard's own location) when the caller has one; otherwise the last
 * remembered view is used, falling back to a bare `/dashboard`.
 */
export function dashboardHref(search?: string | null): string {
  const raw = (search ?? readRememberedSearch()).replace(/^\?/, "");
  return raw ? `/dashboard?${raw}` : "/dashboard";
}

/**
 * Return path for a caller that only knows which tab it wants -- it keeps the
 * remembered filters but forces the tab, so approving a submission always lands
 * on the review queue rather than on My Submissions.
 */
export function dashboardHrefForTab(tab: DashboardTab): string {
  const params = new URLSearchParams(readRememberedSearch());
  if (tab === "editor") params.set("tab", "editor");
  else params.delete("tab");
  const raw = params.toString();
  return raw ? `/dashboard?${raw}` : "/dashboard";
}
