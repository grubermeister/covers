/**
 * Issue #103 -- county rows must be split out of the State/Territory display.
 *
 * A marking's `regions` list carries every post_office_regions link. Since the
 * VPHC ingest that includes a COUNTY-tier row for every VA/WV town, so the
 * State/Territory row read "Virginia, Accomack" and the #28 chips rendered an
 * "Accomack" chip linking to ?state=Accomack. County is now its own field.
 */
import {
  countyDisplay,
  countyRegions,
  primaryRegions,
  regionsDisplay,
} from "./markings";

const region = (name: string, regionTier: string, abbrev = "VA") => ({
  id: name.length,
  name,
  abbrev,
  regionTier,
});

// The shape a Virginia marking detail actually returns from woco.dev today.
const virginiaWithCounty = {
  state: "Virginia",
  regions: [region("Virginia", "STATE"), region("Accomack", "COUNTY")],
};

describe("splitting counties out of a marking's regions", () => {
  it("keeps counties out of the State/Territory row", () => {
    expect(regionsDisplay(virginiaWithCounty)).toBe("Virginia");
  });

  it("keeps counties out of the chips", () => {
    expect(primaryRegions(virginiaWithCounty).map((r) => r.name)).toEqual(["Virginia"]);
  });

  it("surfaces the county as its own value", () => {
    expect(countyDisplay(virginiaWithCounty)).toBe("Accomack");
    expect(countyRegions(virginiaWithCounty).map((r) => r.name)).toEqual(["Accomack"]);
  });

  it("leaves multi-territory display intact (issue #24 must not regress)", () => {
    const detroit = {
      state: "Michigan",
      regions: [
        region("Michigan", "STATE", "MI"),
        region("Michigan Territory", "TERRITORY", "MIT"),
        region("Wayne", "COUNTY", "MI"),
      ],
    };
    expect(regionsDisplay(detroit)).toBe("Michigan, Michigan Territory");
    expect(countyDisplay(detroit)).toBe("Wayne");
  });

  it("returns an empty county string when there is no county link", () => {
    expect(countyDisplay({ regions: [region("Michigan", "STATE", "MI")] })).toBe("");
  });

  it("still falls back to the primary state when only a county is linked", () => {
    // A quarantined town: its only link is its county, so the state row must
    // fall back rather than claim the town is in a state called Accomack.
    expect(
      regionsDisplay({ state: "Virginia", regions: [region("Accomack", "COUNTY")] }),
    ).toBe("Virginia");
  });

  it("matches the tier case-insensitively", () => {
    expect(countyDisplay({ regions: [region("Accomack", "county")] })).toBe("Accomack");
  });
});
