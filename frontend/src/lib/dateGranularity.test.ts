import { deriveGranularityFromIso } from "./dateGranularity";

describe("deriveGranularityFromIso", () => {
  it("treats Jan-1 as a year-only date (the catalog ERD/LRD case)", () => {
    expect(deriveGranularityFromIso("1845-01-01")).toEqual({
      granularity: "YEAR",
      normalizedDate: "1845-01-01",
    });
  });

  it("treats day-1 of a month as month granularity", () => {
    expect(deriveGranularityFromIso("1845-06-01")).toEqual({
      granularity: "MONTH",
      normalizedDate: "1845-06-01",
    });
  });

  it("treats a full date as day granularity", () => {
    expect(deriveGranularityFromIso("1845-06-14")).toEqual({
      granularity: "DAY",
      normalizedDate: "1845-06-14",
    });
  });

  it("returns null for empty or malformed input", () => {
    expect(deriveGranularityFromIso("")).toBeNull();
    expect(deriveGranularityFromIso("1845")).toBeNull();
    expect(deriveGranularityFromIso("not-a-date")).toBeNull();
  });

  it("rejects out-of-range month/day", () => {
    expect(deriveGranularityFromIso("1845-13-01")).toBeNull();
    expect(deriveGranularityFromIso("1845-06-32")).toBeNull();
  });
});
