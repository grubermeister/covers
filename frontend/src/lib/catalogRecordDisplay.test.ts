import { formatDateSeen, formatDatesSeenList } from "./catalogRecordDisplay";

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
