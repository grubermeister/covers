import { materializedCoverIdFromContribution } from "./contributionDisplay";

describe("materializedCoverIdFromContribution", () => {
  it("reads snake_case and camelCase cover ids", () => {
    expect(materializedCoverIdFromContribution({ cover_id: 12 })).toBe(12);
    expect(materializedCoverIdFromContribution({ coverId: "34" })).toBe(34);
  });

  it("returns null for missing, empty, or invalid cover ids", () => {
    expect(materializedCoverIdFromContribution({})).toBeNull();
    expect(materializedCoverIdFromContribution({ cover_id: "" })).toBeNull();
    expect(materializedCoverIdFromContribution({ cover_id: "0" })).toBeNull();
    expect(materializedCoverIdFromContribution({ cover_id: "abc" })).toBeNull();
  });
});
