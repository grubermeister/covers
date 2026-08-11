/**
 * @jest-environment jsdom
 *
 * Needed for the sessionStorage-backed return-href helpers: jest defaults to the
 * node environment, where `sessionStorage` is not defined. (Newer Node versions
 * happen to expose it as a global, so omitting this passes locally and fails on
 * CI's Node 22.) Matches auth.test.ts / authRedirect.test.ts.
 */
import {
  DEFAULT_PAGE_SIZE,
  buildDashboardParams,
  dashboardHref,
  dashboardHrefForTab,
  defaultTabParams,
  parseDashboardParams,
  rememberDashboardLocation,
  type DashboardParams,
} from "./dashboardParams";

function defaults(): DashboardParams {
  return { tab: "submissions", submissions: defaultTabParams(), editor: defaultTabParams() };
}

describe("dashboard URL params", () => {
  it("omits every default, so a clean dashboard has no query string", () => {
    expect(buildDashboardParams(defaults()).toString()).toBe("");
  });

  it("round-trips a fully populated editor view", () => {
    const state = defaults();
    state.tab = "editor";
    state.editor = {
      ...defaultTabParams(),
      status: "pending",
      state: "Michigan",
      town: "Detroit",
      sort: [{ field: "town", dir: "asc" }],
      page: 3,
      pageSize: 100,
    };
    const parsed = parseDashboardParams(buildDashboardParams(state));
    expect(parsed).toEqual(state);
  });

  it("keeps the two tabs' state independent", () => {
    const state = defaults();
    state.submissions = { ...defaultTabParams(), status: "draft", page: 2 };
    state.editor = { ...defaultTabParams(), status: "approved", page: 7 };
    const parsed = parseDashboardParams(buildDashboardParams(state));
    expect(parsed.submissions.status).toBe("draft");
    expect(parsed.submissions.page).toBe(2);
    expect(parsed.editor.status).toBe("approved");
    expect(parsed.editor.page).toBe(7);
  });

  it("persists an explicitly cleared sort instead of restoring the default", () => {
    const state = defaults();
    state.editor = { ...defaultTabParams(), sort: [] };
    const url = buildDashboardParams(state);
    expect(url.get("e_order")).toBe("none");
    expect(parseDashboardParams(url).editor.sort).toEqual([]);
  });

  it("falls back to defaults on junk values rather than throwing", () => {
    const parsed = parseDashboardParams(
      new URLSearchParams("page=abc&pageSize=-5&order=nonsense&tab=sideways"),
    );
    expect(parsed.tab).toBe("submissions");
    expect(parsed.submissions.page).toBe(1);
    expect(parsed.submissions.pageSize).toBe(DEFAULT_PAGE_SIZE);
    expect(parsed.submissions.sort).toEqual([{ field: "submitted", dir: "desc" }]);
  });
});

describe("dashboard return href", () => {
  beforeEach(() => sessionStorage.clear());

  it("uses the caller's search string when given one", () => {
    expect(dashboardHref("tab=editor&e_page=3")).toBe("/dashboard?tab=editor&e_page=3");
    expect(dashboardHref("")).toBe("/dashboard");
  });

  it("falls back to the last remembered view", () => {
    rememberDashboardLocation("?tab=editor&e_pageSize=100");
    expect(dashboardHref()).toBe("/dashboard?tab=editor&e_pageSize=100");
  });

  it("forces the tab while keeping the remembered filters", () => {
    rememberDashboardLocation("e_status=pending&e_pageSize=100");
    expect(dashboardHrefForTab("editor")).toBe(
      "/dashboard?e_status=pending&e_pageSize=100&tab=editor",
    );
  });

  it("returns a bare dashboard when nothing is remembered", () => {
    expect(dashboardHref()).toBe("/dashboard");
    expect(dashboardHrefForTab("submissions")).toBe("/dashboard");
  });
});
