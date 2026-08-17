/**
 * Issue #103 -- the State/Territory dropdown must ask for jurisdictions only.
 *
 * GET /regions/ returns every Region, and since the VPHC ingest that is 199
 * rows: 141 counties alongside 50 states, 6 territories, DC and the country.
 * collectRegionNames paged all of them and deduped by name, so the dropdown
 * read "Accomack, Alabama, Alaska, Albemarle, Alexandria, Alleghany, Amelia…"
 * -- 196 entries. Greg Stone reported it as "196 State/Territories ?!?".
 *
 * The fix is a tier filter on the request, so that is what is asserted here:
 * the filter has to be *sent*, not applied client-side, or every consumer has
 * to remember to reapply it.
 */
import { getRegions, getAssignedRegions, STATE_SELECTOR_TIERS } from "./regions";
import apiClient from "@/lib/api";

jest.mock("@/lib/api", () => ({
  __esModule: true,
  default: { get: jest.fn() },
  ensureCsrfToken: jest.fn(),
}));

const mockedGet = apiClient.get as jest.Mock;

const page = (results: unknown[], next: string | null = null) => ({
  data: { count: results.length, next, previous: null, results },
});

describe("getRegions tier filtering", () => {
  beforeEach(() => mockedGet.mockReset());

  it("asks the API for jurisdiction tiers only", () => {
    mockedGet.mockResolvedValue(page([{ id: 1, name: "Virginia", region_tier: "STATE" }]));
    return getRegions().then(() => {
      const [url, config] = mockedGet.mock.calls[0];
      expect(url).toBe("/regions/");
      expect(config.params.region_tier__in).toBe("STATE,TERRITORY,DISTRICT");
    });
  });

  it("covers states, territories and DC, and excludes counties and the country", () => {
    // Locking the actual list down: DC is a real postal jurisdiction and must
    // be offered; "United States of America" is not an answer to "which
    // state?"; counties are the defect.
    expect([...STATE_SELECTOR_TIERS]).toEqual(["STATE", "TERRITORY", "DISTRICT"]);
    expect(STATE_SELECTOR_TIERS).not.toContain("COUNTY");
    expect(STATE_SELECTOR_TIERS).not.toContain("COUNTRY");
  });

  it("keeps sending the filter while following pagination", () => {
    // The `next` URL already carries the querystring, so the second call
    // deliberately passes no params -- if that ever changed to re-send a
    // params object it would have to include the tier filter too.
    mockedGet
      .mockResolvedValueOnce(page([{ id: 1, name: "Virginia" }], "/regions/?page=2"))
      .mockResolvedValueOnce(page([{ id: 2, name: "West Virginia" }]));
    return getRegions().then((options) => {
      expect(options.map((o) => o.value)).toEqual(["Virginia", "West Virginia"]);
      expect(mockedGet.mock.calls[1][0]).toBe("/regions/?page=2");
    });
  });

  it("still scopes to the editor's own regions when asked", () => {
    mockedGet.mockResolvedValue(page([{ id: 1, name: "Virginia" }]));
    return getAssignedRegions().then(() => {
      const config = mockedGet.mock.calls[0][1];
      expect(config.params.assigned_only).toBe("true");
      expect(config.params.region_tier__in).toBe("STATE,TERRITORY,DISTRICT");
    });
  });
});
