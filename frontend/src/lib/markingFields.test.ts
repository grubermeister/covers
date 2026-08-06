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

describe("buildMarkingFields - boundary date links", () => {
  it("attaches cover detail targets to earliest and latest rows when supplied", () => {
    const rows = buildMarkingFields(
      baseInput({
        earliestSeenTo: "/record/12/cover/34",
        latestSeenTo: "/record/12/cover/56",
      }),
      { isStaff: false },
    );

    expect(rows.find((r) => r.label === "Earliest Seen")?.to).toBe(
      "/record/12/cover/34",
    );
    expect(rows.find((r) => r.label === "Latest Seen")?.to).toBe(
      "/record/12/cover/56",
    );
  });
});

describe("buildMarkingFields — State/Territory tags (issue #28)", () => {
  it("attaches region tags (search links) to the State/Territory row when supplied", () => {
    const rows = buildMarkingFields(
      baseInput({
        state: "Michigan, Michigan Territory",
        regionTags: [
          { label: "Michigan", to: "/search?state=Michigan" },
          { label: "Michigan Territory", to: "/search?state=Michigan%20Territory" },
        ],
      }),
      { isStaff: false },
    );
    const row = rows.find((r) => r.label === "State/Territory");
    expect(row?.tags).toHaveLength(2);
    expect(row?.tags?.[0]).toEqual({ label: "Michigan", to: "/search?state=Michigan" });
    // value is kept as the comma-joined fallback (visibility key + ContributionDetail).
    expect(row?.value).toBe("Michigan, Michigan Territory");
  });

  it("leaves tags undefined when no region tags are supplied (ContributionDetail path)", () => {
    const rows = buildMarkingFields(baseInput({ state: "Virginia" }), { isStaff: false });
    const row = rows.find((r) => r.label === "State/Territory");
    expect(row?.tags).toBeUndefined();
    expect(row?.value).toBe("Virginia");
  });

  it("treats an empty regionTags array as no tags (falls back to the value string)", () => {
    const rows = buildMarkingFields(
      baseInput({ state: "Virginia", regionTags: [] }),
      { isStaff: false },
    );
    const row = rows.find((r) => r.label === "State/Territory");
    expect(row?.tags).toBeUndefined();
  });
});
