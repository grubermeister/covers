/**
 * Return-target passing through the URL (issue #147).
 *
 * Todd Hause (Alabama editor), 2026-08-31: "After editing a cancel entry and
 * approving, we end up on the 'editors dashboard'. To get back to where we were
 * on the listing page we have to repeat the entire search process again
 * including changing the number of 'records shown'."
 *
 * Issue #87 already solved "where do I go back to" twice -- once for the
 * dashboard (rememberDashboardLocation / dashboardHref) and once for the
 * catalog (rememberCatalogLocation / catalogHref). Both mirror the view into
 * sessionStorage. That works while the editor is browsing, and it fails in
 * exactly the case an editor hits most: **arriving by a link someone sent
 * them.** Ian mails contribution URLs directly (`woco.dev/contribution/9145` in
 * the 2026-08-24 thread), and a fresh browsing context has no remembered view
 * and no router state, so the editor is returned to a defaulted screen.
 *
 * Putting the target in the URL fixes that class rather than one instance: it
 * survives a direct link, a reload, a new browser tab and a copy-paste, because
 * it travels with the address. It is also origin-agnostic -- the return target
 * is whatever the caller was looking at, so this works for the dashboard queue,
 * the catalog, or any listing added later, without this module knowing which.
 *
 * sessionStorage stays as the fallback: an editor who navigated normally and
 * has no `?from=` still lands where they used to.
 */

const PARAM = "from";

/**
 * Attach a return target to a path.
 *
 * `returnTo` is an app-relative href including its query string, e.g.
 * "/dashboard?tab=editor&e_pageSize=100". Encoding is left to URLSearchParams.
 */
export function withReturnTo(path: string, returnTo: string | null | undefined): string {
  const target = (returnTo ?? "").trim();
  if (!isSafeReturnTo(target)) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}${PARAM}=${encodeURIComponent(target)}`;
}

/**
 * The return target carried by a URL, or null when absent or not safe to use.
 *
 * ⛔ The validation is not decoration. This value comes from the address bar,
 * so anyone can put anything in it and the app would then navigate there after
 * a successful approve -- an open redirect, and a convincing one, because the
 * user is mid-workflow on a site they trust. Only same-origin *relative* paths
 * are accepted:
 *
 *   "/dashboard?tab=editor"  -> allowed
 *   "//evil.example"         -> rejected (protocol-relative; resolves off-site)
 *   "/\\evil.example"        -> rejected (browsers normalise \ to / in paths)
 *   "https://evil.example"   -> rejected (absolute)
 *   "javascript:alert(1)"    -> rejected (scheme, and not a path)
 *   "dashboard"              -> rejected (not rooted; would resolve relatively)
 */
export function readReturnTo(params: URLSearchParams): string | null {
  const raw = params.get(PARAM);
  if (raw === null) return null;
  const target = raw.trim();
  return isSafeReturnTo(target) ? target : null;
}

function isSafeReturnTo(target: string): boolean {
  if (!target) return false;
  // Must be rooted, so it can only ever resolve against this origin.
  if (!target.startsWith("/")) return false;
  // "//host" and "/\host" both leave the origin once the browser normalises
  // them; a backslash anywhere in the path is never legitimate here.
  if (target.startsWith("//") || target.includes("\\")) return false;
  // Control characters (notably \n, \r and \t) are stripped or normalised by
  // browsers, so "/\tjavascript:..."-style smuggling is rejected outright
  // rather than reasoned about.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001F\u007F]/.test(target)) return false;
  return true;
}
