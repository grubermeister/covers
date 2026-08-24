/**
 * Issue #104 / C3 -- candidate targets for "move this image to another marking".
 *
 * A VPHC scan can hold two devices in one PNG (a PAID handstamp above a 10
 * ratemark). The editor crops the second device out and reassigns the crop to
 * the marking it belongs to, which is always at the same post office. Offering
 * the wrong marking is silent damage to the catalog, so the scoping rules are
 * pinned here rather than left to the caller's query.
 */
import { moveTargetCandidates } from "./markings";
import type { MarkingRecord } from "./markings";

const marking = (id: number, postOfficeId: number | null, code = `M${id}`) =>
  ({ id, postOfficeId, code }) as MarkingRecord;

const current = { id: 1, postOfficeId: 100 };

describe("moveTargetCandidates", () => {
  it("offers the other markings at the same post office", () => {
    const siblings = [marking(1, 100), marking(2, 100), marking(3, 100)];
    expect(moveTargetCandidates(siblings, current).map((m) => m.id)).toEqual([2, 3]);
  });

  it("never offers the marking you are moving from", () => {
    expect(moveTargetCandidates([marking(1, 100)], current)).toEqual([]);
  });

  it("drops markings at another post office even if the caller sent them", () => {
    // The server filter should already exclude these; re-checked because a
    // mis-scoped query would otherwise file an image under an unrelated town.
    const siblings = [marking(2, 100), marking(3, 999), marking(4, null)];
    expect(moveTargetCandidates(siblings, current).map((m) => m.id)).toEqual([2]);
  });

  it("offers nothing when the current marking has no post office", () => {
    const siblings = [marking(2, null), marking(3, null)];
    expect(moveTargetCandidates(siblings, { id: 1, postOfficeId: null })).toEqual([]);
  });

  it("handles a town with only this one marking", () => {
    expect(moveTargetCandidates([], current)).toEqual([]);
  });
});
