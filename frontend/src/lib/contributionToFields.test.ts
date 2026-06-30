import { submittedDataToFieldInput } from "./contributionToFields";

const lookups = {
  letteringOptions: [],
  dateFormatOptions: [],
};

describe("submittedDataToFieldInput", () => {
  it("accepts pending marking ERD/LRD keys and maps them to seen years", () => {
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
    expect(fieldInput.latestSeen).toBe("1850");
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

    expect(fieldInput.earliestSeen).toBe("1845");
    expect(fieldInput.latestSeen).toBe("1850");
  });
});
