import type { MarkingRecord } from "@/services/markings";
import { buildCatalogFieldValues, formatDateSeen, formatDatesSeenList } from "./catalogRecordDisplay";

function markingRecord(overrides: Partial<MarkingRecord> = {}): MarkingRecord {
  return {
    id: 1,
    code: "ASCC6-IL-M1089",
    type: "TOWNMARK",
    catalogTxt: "ASBURY/ILL.(1851;framed arc-32x19;10;Brown) 200",
    inscriptionTxt: "ASBURY/ILL.",
    desc: "framed arc",
    isManuscript: false,
    isIrreg: false,
    width: "32",
    height: "19",
    sizeDisplay: "32x19",
    dateFmt: "",
    impression: "Normal",
    rateVal: null,
    postOfficeId: 31,
    shapeId: 5,
    letteringId: null,
    colorId: 4,
    state: "Illinois",
    stateAbbrev: "IL",
    town: "ASBURY",
    shapeName: "ARC - Arc or Semi-circle",
    letteringName: "",
    colorName: "BROWN",
    postOfficeName: "ASBURY",
    regionName: "Illinois",
    regions: [],
    earliestSeen: "1851-01-01",
    earliestSeenGranularity: "YEAR",
    latestSeen: "1851-01-01",
    latestSeenGranularity: "YEAR",
    datesSeen: [],
    mainImage: null,
    secondImage: null,
    images: [],
    citations: [],
    isRemoved: false,
    canRemove: false,
    isReviewed: false,
    commentForEditor: "",
    editorFeedback: "",
    ...overrides,
  };
}

describe("formatDateSeen", () => {
  it("formats DAY granularity as MM/DD/YYYY", () => {
    expect(formatDateSeen("1865-08-14", "DAY")).toBe("08/14/1865");
  });

  it("formats MONTH granularity as MMM, YYYY", () => {
    expect(formatDateSeen("1865-08-01", "MONTH")).toBe("AUG, 1865");
  });

  it("formats YEAR granularity as YYYY", () => {
    expect(formatDateSeen("1865-01-01", "YEAR")).toBe("1865");
  });

  it("formats partial component dates", () => {
    expect(
      formatDateSeen(null, "MONTH_DAY", {
        dateYear: null,
        dateMonth: 8,
        dateDay: 14,
      }),
    ).toBe("AUG 14 (year unknown)");
  });
});

describe("formatDatesSeenList", () => {
  it("returns '' for an empty list", () => {
    expect(formatDatesSeenList([])).toBe("");
  });

  it("returns '' for a single date (Earliest/Latest already convey it)", () => {
    expect(formatDatesSeenList([{ date: "1811-01-01", granularity: "YEAR" }])).toBe("");
  });

  it("joins multiple distinct dates in order, formatted by granularity", () => {
    // Aquila: 1811, then a 1849-1855 range stored as discrete boundary years.
    expect(
      formatDatesSeenList([
        { date: "1811-01-01", granularity: "YEAR" },
        { date: "1849-01-01", granularity: "YEAR" },
        { date: "1855-01-01", granularity: "YEAR" },
      ]),
    ).toBe("1811, 1849, 1855");
  });

  it("de-dupes identical formatted dates (and may collapse back to '')", () => {
    expect(
      formatDatesSeenList([
        { date: "1849-01-01", granularity: "YEAR" },
        { date: "1849-06-01", granularity: "YEAR" },
      ]),
    ).toBe("");
  });

  it("mixes granularities", () => {
    expect(
      formatDatesSeenList([
        { date: "1849-01-01", granularity: "YEAR" },
        { date: "1855-08-14", granularity: "DAY" },
      ]),
    ).toBe("1849, 08/14/1855");
  });
});

describe("buildCatalogFieldValues dimensions", () => {
  it("does not display ARC semi-circle dimensions as a diameter", () => {
    expect(buildCatalogFieldValues(markingRecord()).dimensions).toBe("32x19 mm");
  });

  it("displays true circle-family dimensions as a diameter", () => {
    expect(
      buildCatalogFieldValues(markingRecord({
        shapeName: "C - Circle",
        width: "32",
        height: "32",
        sizeDisplay: "32x32",
      })).dimensions,
    ).toBe("32 mm diameter");
  });
});
