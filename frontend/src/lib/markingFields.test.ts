import { buildMarkingFields, type MarkingFieldInput } from "./markingFields";

// Minimal valid input; individual tests override only what they exercise.
function baseInput(overrides: Partial<MarkingFieldInput> = {}): MarkingFieldInput {
  return {
    type: "TOWNMARK",
    isManuscript: true,
    state: "Virginia",
    town: "Aquila",
    inscriptionTxt: "AQUILA VA",
    earliestSeen: "1811",
    latestSeen: "1855",
    shapeName: "",
    rateValFormatted: "",
    dateFmt: "",
    impression: "",
    isIrreg: null,
    colorName: "",
    letteringName: "",
    dimensions: "",
    catalogTxt: "",
    code: "",
    ...overrides,
  };
}

describe("buildMarkingFields — Dates Seen row (issue #25)", () => {
  it("renders the Dates Seen value when multiple dates were supplied", () => {
    const rows = buildMarkingFields(
      baseInput({ datesSeen: "1811, 1849, 1855" }),
      { isStaff: false },
    );
    const dates = rows.find((r) => r.label === "Dates Seen");
    expect(dates?.value).toBe("1811, 1849, 1855");
  });

  it("leaves the Dates Seen row blank (collapsible) when not supplied", () => {
    const rows = buildMarkingFields(baseInput(), { isStaff: false });
    const dates = rows.find((r) => r.label === "Dates Seen");
    // Present but empty so RecordDetail's hasDisplayValue filter hides it; the
    // row is not alwaysShow, so a blank value collapses.
    expect(dates).toBeDefined();
    expect(dates?.value).toBe("");
    expect(dates?.alwaysShow).toBe(false);
  });

  it("orders Dates Seen immediately after Latest Seen", () => {
    const rows = buildMarkingFields(
      baseInput({ datesSeen: "1811, 1855" }),
      { isStaff: false },
    );
    const labels = rows.map((r) => r.label);
    expect(labels.indexOf("Dates Seen")).toBe(labels.indexOf("Latest Seen") + 1);
  });
});
