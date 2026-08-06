import {
  formatPartialDateInput,
  partialDateInputFromSubmittedData,
  validatePartialDate,
} from "./partialDate";

describe("partial date helpers", () => {
  it("formats every supported component combination", () => {
    expect(formatPartialDateInput({ unknown: true, year: "", month: "", day: "" })).toBe(
      "Date unknown",
    );
    expect(formatPartialDateInput({ unknown: false, year: "1850", month: "", day: "" })).toBe(
      "1850",
    );
    expect(formatPartialDateInput({ unknown: false, year: "1850", month: "6", day: "" })).toBe(
      "JUN, 1850",
    );
    expect(formatPartialDateInput({ unknown: false, year: "1850", month: "6", day: "14" })).toBe(
      "06/14/1850",
    );
    expect(formatPartialDateInput({ unknown: false, year: "", month: "6", day: "" })).toBe(
      "JUN (year unknown)",
    );
    expect(formatPartialDateInput({ unknown: false, year: "", month: "", day: "14" })).toBe(
      "Day 14 (month/year unknown)",
    );
    expect(formatPartialDateInput({ unknown: false, year: "1850", month: "", day: "14" })).toBe(
      "Day 14, 1850 (month unknown)",
    );
    expect(formatPartialDateInput({ unknown: false, year: "", month: "6", day: "14" })).toBe(
      "JUN 14 (year unknown)",
    );
  });

  it("generates legacy sortable dates only when enough parts exist", () => {
    expect(validatePartialDate({ unknown: false, year: "1850", month: "", day: "" })).toEqual({
      ok: true,
      value: {
        unknown: false,
        granularity: "YEAR",
        year: 1850,
        month: null,
        day: null,
        legacyDate: "1850-01-01",
        legacyGranularity: "YEAR",
      },
    });
    expect(validatePartialDate({ unknown: false, year: "1850", month: "", day: "14" })).toEqual({
      ok: true,
      value: {
        unknown: false,
        granularity: "YEAR_DAY",
        year: 1850,
        month: null,
        day: 14,
        legacyDate: null,
        legacyGranularity: null,
      },
    });
  });

  it("rejects empty and impossible final dates", () => {
    expect(validatePartialDate({ unknown: false, year: "", month: "", day: "" })).toEqual({
      ok: false,
      error: "Enter a date component or select Date unknown.",
    });
    expect(validatePartialDate({ unknown: false, year: "1851", month: "2", day: "29" })).toEqual({
      ok: false,
      error: "Day is not valid for the selected month.",
    });
    expect(validatePartialDate({ unknown: false, year: "", month: "2", day: "29" }).ok).toBe(true);
  });

  it("hydrates legacy and component contribution payloads", () => {
    expect(
      partialDateInputFromSubmittedData({
        cover_date: "1850-06-01",
        cover_granularity: "MONTH",
      }),
    ).toEqual({ unknown: false, year: "1850", month: "6", day: "" });
    expect(
      partialDateInputFromSubmittedData({
        cover_date_year: "1850",
        cover_date_day: "14",
      }),
    ).toEqual({ unknown: false, year: "1850", month: "", day: "14" });
    expect(partialDateInputFromSubmittedData({ cover_date_unknown: "true" })).toEqual({
      unknown: true,
      year: "",
      month: "",
      day: "",
    });
  });
});
