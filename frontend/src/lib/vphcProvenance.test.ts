import { readVphcProvenance, describeVphcUnmatchedReason } from "./vphcProvenance";

describe("readVphcProvenance", () => {
  it("returns null for a contribution that did not come from the ingest", () => {
    expect(readVphcProvenance({ state: "VA", town: "Richmond" })).toBeNull();
  });

  it("reads the blob apply_vphc_ledger writes", () => {
    const p = readVphcProvenance({
      vphc: {
        src: "T1:r8",
        flags: [],
        state: "VA",
        county: "WASHINGTON",
        cancel_no: "5",
        vphc_code: "VPHC-VA-ABINGDON-5",
        rules_version: 2,
        why_unmatched: "",
      },
    });

    expect(p).not.toBeNull();
    expect(p!.code).toBe("VPHC-VA-ABINGDON-5");
    expect(p!.cancelNo).toBe("5");
    expect(p!.county).toBe("WASHINGTON");
    expect(p!.rulesVersion).toBe("2");
    expect(p!.flags).toEqual([]);
  });

  // A reviewer scanning the queue should meet the flags that mean "this may be
  // wrong" before the ones that mean "we fixed this for you".
  it("sorts uncertain flags ahead of repaired ones", () => {
    const p = readVphcProvenance({
      vphc: { flags: ["century_inferred", "state_unknown", "county_repaired", "date_unresolved"] },
    });

    expect(p!.flags.map((f) => f.code)).toEqual([
      "date_unresolved",
      "state_unknown",
      "century_inferred",
      "county_repaired",
    ]);
    expect(p!.flags.map((f) => f.severity)).toEqual([
      "uncertain",
      "uncertain",
      "repaired",
      "repaired",
    ]);
  });

  it("explains a flag with the pipeline's own wording", () => {
    const p = readVphcProvenance({ vphc: { flags: ["date_low_confidence"] } });
    expect(p!.flags[0].label).toBe("Date low confidence");
    expect(p!.flags[0].reason).toContain("run was too short to corroborate the century");
  });

  // The crossexam vocabulary can grow. A flag we don't recognise is the one a
  // reviewer most needs to see, so it must survive rather than be filtered out.
  it("keeps a flag that is not in the glossary", () => {
    const p = readVphcProvenance({ vphc: { flags: ["some_new_flag"] } });
    expect(p!.flags).toHaveLength(1);
    expect(p!.flags[0].label).toBe("Some new flag");
    expect(p!.flags[0].severity).toBe("uncertain");
  });

  it("survives a malformed blob", () => {
    expect(readVphcProvenance({ vphc: "nope" })).toBeNull();
    expect(readVphcProvenance({ vphc: ["nope"] })).toBeNull();
    expect(readVphcProvenance({ vphc: {} })!.flags).toEqual([]);
    expect(readVphcProvenance({ vphc: { flags: "not-an-array" } })!.flags).toEqual([]);
  });
});

describe("describeVphcUnmatchedReason", () => {
  it("explains why an unmatched marking was catalogued as new", () => {
    expect(describeVphcUnmatchedReason("ambiguous")).toContain("Matched more than one");
    expect(describeVphcUnmatchedReason("create_no_town")).toContain("No post office");
  });

  it("falls back to the raw code when the verdict is unknown", () => {
    expect(describeVphcUnmatchedReason("brand_new_verdict")).toBe("Brand new verdict");
  });
});
