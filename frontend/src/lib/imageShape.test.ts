import { classifyImageShape, looksLikeWrongKind } from "./imageShape";

/**
 * Issue #76. The numbers below are real measurements from prod, not invented
 * fixtures -- see docs/issues.md #76 for how the thresholds were derived.
 */
describe("classifyImageShape", () => {
  it("calls a whole-cover scan cover-like", () => {
    // image 2417 on prod: a Fetterman VA envelope filed as a marking image.
    expect(classifyImageShape({ width: 2631, height: 1290 })).toBe("cover-like");
  });

  it("calls a marking closeup marking-like", () => {
    // image 2288 on prod: a correctly-slotted Berkeley Springs CDS.
    expect(classifyImageShape({ width: 458, height: 465 })).toBe("marking-like");
  });

  it("leaves the ambiguous middle band alone", () => {
    // Landscape but small: a wide marking crop, not a cover scan.
    expect(classifyImageShape({ width: 900, height: 400 })).toBe("indeterminate");
    // Big but square: could be either, so say nothing.
    expect(classifyImageShape({ width: 1200, height: 1100 })).toBe("indeterminate");
  });

  it("does not flag a wide straight-line handstamp as a cover", () => {
    // Observed marking aspect ratios reach 8.6; only size separates them.
    expect(classifyImageShape({ width: 1720, height: 200 })).toBe("indeterminate");
  });

  it("treats unusable dimensions as indeterminate", () => {
    expect(classifyImageShape({ width: 0, height: 0 })).toBe("indeterminate");
    expect(classifyImageShape({ width: NaN, height: 100 })).toBe("indeterminate");
  });
});

describe("looksLikeWrongKind", () => {
  it("flags a cover on the marking form and not on the cover form", () => {
    const cover = { width: 2631, height: 1290 };
    expect(looksLikeWrongKind(cover, "MARKING")).toBe(true);
    expect(looksLikeWrongKind(cover, "COVER")).toBe(false);
  });

  it("flags a marking closeup on the cover form and not on the marking form", () => {
    const marking = { width: 458, height: 465 };
    expect(looksLikeWrongKind(marking, "COVER")).toBe(true);
    expect(looksLikeWrongKind(marking, "MARKING")).toBe(false);
  });

  it("never flags the ambiguous middle on either form", () => {
    const middling = { width: 900, height: 400 };
    expect(looksLikeWrongKind(middling, "MARKING")).toBe(false);
    expect(looksLikeWrongKind(middling, "COVER")).toBe(false);
  });
});
