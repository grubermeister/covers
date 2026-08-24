/**
 * Return-path helpers for the Catalog Search view.
 *
 * Catalog Search already mirrors its filters, sort, page and page size into the
 * URL, so a filtered view survives a reload and is shareable. What it did not
 * survive was the round trip through a marking detail: the detail screen's back
 * control navigates forward to `/search` rather than popping history, so it
 * dropped the query string and the catalog re-mounted with every filter cleared.
 *
 * These helpers are the catalog twin of `rememberDashboardLocation` /
 * `dashboardHref` in `dashboardParams.ts` (Issue #87). Every URL write mirrors
 * the serialized params into sessionStorage, and any "back to catalog" caller
 * rebuilds the view from there without needing to know what it was. The mirror
 * is deliberately sessionStorage rather than react-router location state: detail
 * screens can be reloaded or arrived at several navigations deep, and router
 * state does not survive either.
 */

const STORAGE_KEY = "worldcovers.catalog.lastView";

export function rememberCatalogLocation(search: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, search.replace(/^\?/, ""));
  } catch {
    // Private-browsing / storage-disabled: fall back to the plain catalog.
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
 * Path to return a browser to. Pass an explicit search string when the caller
 * has one (e.g. the catalog's own location); otherwise the last remembered view
 * is used, falling back to a bare `/search`.
 */
export function catalogHref(search?: string | null): string {
  const raw = (search ?? readRememberedSearch()).replace(/^\?/, "");
  return raw ? `/search?${raw}` : "/search";
}
