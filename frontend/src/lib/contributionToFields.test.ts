import { submittedDataToFieldInput } from "./contributionToFields";

const lookups = {
  letteringOptions: [],
  dateFormatOptions: [],
};

describe("submittedDataToFieldInput", () => {
  it("accepts pending marking ERD/LRD keys and formats them by precision", () => {
    const fieldInput = submittedDataToFieldInput(
      {
        submission_kind: "marking",
        state: "VA",
        town: "Richmond",
        type: "TOWNMARK",
        marking_erd: "1845-01-01",
        marking_erd_granularity: "YEAR",
        marking_lrd: "1850-12-31",
        marking_lrd_granularity: "DAY",
      },
      lookups,
      { contributionId: 56 },
    );

    expect(fieldInput.earliestSeen).toBe("1845");
    expect(fieldInput.latestSeen).toBe("12/31/1850");
  });

  it("falls back to marking ERD/LRD when legacy seen fields are empty", () => {
    const fieldInput = submittedDataToFieldInput(
      {
        first_seen: "",
        last_seen: "",
        marking_erd: "1845-01-01",
        marking_lrd: "1850-12-31",
      },
      lookups,
      { contributionId: 56 },
    );

    expect(fieldInput.earliestSeen).toBe("01/01/1845");
    expect(fieldInput.latestSeen).toBe("12/31/1850");
  });

  it("formats marking ERD/LRD component dates without a year", () => {
    const fieldInput = submittedDataToFieldInput(
      {
        submission_kind: "marking",
        state: "VA",
        town: "Richmond",
        type: "TOWNMARK",
        marking_erd_date_month: "12",
        marking_erd_date_day: "1",
        marking_lrd_date_year: "1850",
        marking_lrd_date_day: "14",
      },
      lookups,
      { contributionId: 56 },
    );

    expect(fieldInput.earliestSeen).toBe("DEC 1 (year unknown)");
    expect(fieldInput.latestSeen).toBe("Day 14, 1850 (month unknown)");
  });

  it("preserves manuscript and irregular flags from string form payloads", () => {
    const fieldInput = submittedDataToFieldInput(
      {
        submission_kind: "marking",
        state: "VA",
        town: "Richmond",
        type: "TOWNMARK",
        is_manuscript: "true",
        is_irreg: "false",
      },
      lookups,
      { contributionId: 57 },
    );

    expect(fieldInput.isManuscript).toBe(true);
    expect(fieldInput.isIrreg).toBe(false);
  });

  it("preserves manuscript and irregular flags from camelCase payloads", () => {
    const fieldInput = submittedDataToFieldInput(
      {
        submissionKind: "marking",
        state: "VA",
        town: "Richmond",
        type: "TOWNMARK",
        isManuscript: true,
        isIrreg: true,
      },
      lookups,
      { contributionId: 58 },
    );

    expect(fieldInput.isManuscript).toBe(true);
    expect(fieldInput.isIrreg).toBe(true);
  });
});
