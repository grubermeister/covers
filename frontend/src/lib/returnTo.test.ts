/**
 * Issue #147 -- the `?from=` return target.
 *
 * The round-trip cases are the feature. The rejection cases are the reason this
 * is a module rather than three inline lines: `from` comes out of the address
 * bar, and whatever it says is where the app navigates after a successful
 * approve. That is an open redirect, and a persuasive one, because the editor
 * is mid-workflow on a site they trust and has just been told the save worked.
 */
import { readReturnTo, withReturnTo } from "./returnTo";

function read(url: string): string | null {
  return readReturnTo(new URLSearchParams(url));
}

describe("withReturnTo", () => {
  it("attaches the target, preserving a query string the path already had", () => {
    expect(withReturnTo("/contribution/9145", "/dashboard?tab=editor")).toBe(
      "/contribution/9145?from=%2Fdashboard%3Ftab%3Deditor",
    );
    expect(withReturnTo("/contribution/9145?x=1", "/dashboard")).toBe(
      "/contribution/9145?x=1&from=%2Fdashboard",
    );
  });

  it("round-trips a full dashboard view, which is the whole point", () => {
    // Todd's complaint was losing the page size specifically.
    const view = "/dashboard?tab=editor&e_status=pending&e_pageSize=100&e_page=3";
    const url = withReturnTo("/contribution/9145", view);
    expect(read(url.split("?")[1])).toBe(view);
  });

  it("leaves the path alone rather than emitting a target it would refuse to read", () => {
    expect(withReturnTo("/contribution/9145", "https://evil.example")).toBe("/contribution/9145");
    expect(withReturnTo("/contribution/9145", "")).toBe("/contribution/9145");
    expect(withReturnTo("/contribution/9145", null)).toBe("/contribution/9145");
  });
});

describe("readReturnTo", () => {
  it("returns null when the param is absent", () => {
    expect(read("tab=editor")).toBeNull();
  });

  it("accepts a rooted same-origin path", () => {
    expect(read("from=%2Fdashboard%3Ftab%3Deditor")).toBe("/dashboard?tab=editor");
    expect(read("from=%2Fsearch%3Fstate%3DAlabama%26pageSize%3D100")).toBe(
      "/search?state=Alabama&pageSize=100",
    );
  });

  // ⛔ Each of these navigates off-origin, or is not a path at all. A bare
  // startsWith("/") check passes the first two.
  it.each([
    ["protocol-relative", "//evil.example"],
    ["backslash-smuggled host", "/\\evil.example"],
    ["absolute http", "https://evil.example"],
    ["javascript scheme", "javascript:alert(1)"],
    ["unrooted, resolves relatively", "dashboard?tab=editor"],
    ["newline-smuggled scheme", "/\njavascript:alert(1)"],
  ])("rejects %s", (_label, target) => {
    expect(read(`from=${encodeURIComponent(target)}`)).toBeNull();
  });
});
