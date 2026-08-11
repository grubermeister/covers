/**
 * Multi-territory support (Issue #24): a town can belong to several
 * territories/states over time. The marking mapper must surface them as a
 * `regions` list, and `regionsDisplay` must render all of them (current-first)
 * for the "State/Territory" detail row, falling back to the single primary
 * region when the list is absent (e.g. search-list payloads).
 */
import { mapApiMarkingToRecord, regionsDisplay } from "./markings";

describe("multi-territory regions", () => {
  it("maps the API regions array, dropping malformed entries", () => {
    const record = mapApiMarkingToRecord({
      id: 1,
      type: "TOWNMARK",
      region_name: "Michigan",
      regions: [
        { id: 5, name: "Michigan", abbrev: "MI", region_tier: "STATE" },
        { id: 6, name: "Michigan Territory", abbrev: "MIT", region_tier: "TERRITORY" },
        { id: null, name: "no-id dropped" },
        { id: 7, name: "" },
      ],
    });
    expect(record.regions).toEqual([
      { id: 5, name: "Michigan", abbrev: "MI", regionTier: "STATE" },
      { id: 6, name: "Michigan Territory", abbrev: "MIT", regionTier: "TERRITORY" },
    ]);
  });

  it("defaults regions to [] when the payload omits them", () => {
    expect(mapApiMarkingToRecord({ id: 2, type: "TOWNMARK" }).regions).toEqual([]);
  });

  describe("regionsDisplay", () => {
    const region = (name: string) => ({ id: 1, name, abbrev: "", regionTier: "" });

    it("joins every territory current-first when there are several", () => {
      expect(
        regionsDisplay({
          state: "Michigan",
          regions: [region("Michigan"), region("Michigan Territory")],
        }),
      ).toBe("Michigan, Michigan Territory");
    });

    it("shows the single region when there is one", () => {
      expect(regionsDisplay({ state: "Michigan", regions: [region("Michigan")] })).toBe(
        "Michigan",
      );
    });

    it("falls back to the primary state when the list is empty", () => {
      expect(regionsDisplay({ state: "Virginia", regions: [] })).toBe("Virginia");
    });
  });
});
