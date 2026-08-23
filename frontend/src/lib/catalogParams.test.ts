/**
 * @jest-environment jsdom
 *
 * Needed for the sessionStorage-backed return-href helpers: jest defaults to the
 * node environment, where `sessionStorage` is not defined. (Newer Node versions
 * happen to expose it as a global, so omitting this passes locally and fails on
 * CI's Node 22.) Matches dashboardParams.test.ts.
 */
import { catalogHref, rememberCatalogLocation } from "./catalogParams";

describe("catalog return href", () => {
  beforeEach(() => sessionStorage.clear());

  it("falls back to a bare /search when nothing is remembered", () => {
    expect(catalogHref()).toBe("/search");
  });

  it("restores the last remembered filtered view", () => {
    rememberCatalogLocation("q=norfolk&state=VA&page=3");
    expect(catalogHref()).toBe("/search?q=norfolk&state=VA&page=3");
  });

  it("prefers an explicit search string over the remembered one", () => {
    rememberCatalogLocation("state=VA");
    expect(catalogHref("state=MD&page=2")).toBe("/search?state=MD&page=2");
  });

  it("strips a leading ? on both write and read", () => {
    rememberCatalogLocation("?state=DE");
    expect(catalogHref()).toBe("/search?state=DE");
    expect(catalogHref("?state=FL")).toBe("/search?state=FL");
  });

  it("returns a bare /search when the remembered view had no filters", () => {
    rememberCatalogLocation("state=VA");
    rememberCatalogLocation("");
    expect(catalogHref()).toBe("/search");
  });
});
