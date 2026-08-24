import { mapApiMarkingToRecord } from "./markings";

// Issue #110. The ingest's "[VPHC: ...]" markers are stripped out of `desc` on
// approval because `desc` renders publicly, so this field is the only route the
// doubt has to a catalog record. The backend gates it -- null for anonymous and
// unrelated users -- which is why RecordDetail can render it without checking
// permissions itself. That contract is what these tests pin: whatever the
// server sends survives the mapper intact, and anything that is not an object
// becomes null rather than reaching the card as junk.

const BLOB = {
  key: "va-wytheville-2",
  flags: ["ambiguous", "county_repaired"],
  src: "T1:r6495",
};

function apiMarking(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    code: "ASCC6-VA-M2110",
    type: "TOWNMARK",
    inscription_txt: "WYTHEVILLE VA",
    is_manuscript: false,
    ...overrides,
  };
}

describe("mapApiMarkingToRecord / vphcProvenance", () => {
  it("passes the blob through when the server sends one", () => {
    const record = mapApiMarkingToRecord(apiMarking({ vphc_provenance: BLOB }));

    expect(record.vphcProvenance).toEqual(BLOB);
  });

  it("is null when the server withholds it", () => {
    // What anonymous and unrelated viewers get. A truthy value here would put
    // the flag vocabulary back on the public page the strip just cleared.
    expect(mapApiMarkingToRecord(apiMarking({ vphc_provenance: null })).vphcProvenance).toBeNull();
    expect(mapApiMarkingToRecord(apiMarking()).vphcProvenance).toBeNull();
  });

  it("is null for values that are not a plain object", () => {
    // readVphcProvenance would reject these downstream, but the card should
    // never be handed them in the first place.
    for (const bad of [[], "ambiguous", 0, true]) {
      expect(
        mapApiMarkingToRecord(apiMarking({ vphc_provenance: bad })).vphcProvenance,
      ).toBeNull();
    }
  });
});
