/**
 * @jest-environment jsdom
 */
import { classifyImageShape, looksLikeWrongKind, measureImageFile } from "./imageShape";

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

describe("measureImageFile", () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;

  beforeEach(() => {
    URL.createObjectURL = jest.fn(() => "blob:stub");
    URL.revokeObjectURL = jest.fn();
  });

  afterEach(() => {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    jest.restoreAllMocks();
  });

  function stubImageLoad(outcome: "load" | "error", size = { w: 0, h: 0 }) {
    Object.defineProperty(global.Image.prototype, "src", {
      configurable: true,
      set(this: HTMLImageElement) {
        Object.defineProperty(this, "naturalWidth", { value: size.w, configurable: true });
        Object.defineProperty(this, "naturalHeight", { value: size.h, configurable: true });
        setTimeout(() => {
          if (outcome === "load") this.onload?.(new Event("load"));
          else this.onerror?.(new Event("error"));
        }, 0);
      },
    });
  }

  it("reports the natural dimensions of a decodable image", async () => {
    stubImageLoad("load", { w: 2631, h: 1290 });
    await expect(measureImageFile(new Blob())).resolves.toEqual({
      width: 2631,
      height: 1290,
    });
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it("resolves null rather than rejecting when the image will not decode", async () => {
    // TIFF is an allowed upload type most browsers cannot render. A failed
    // measurement must never block an upload the server would accept.
    stubImageLoad("error");
    await expect(measureImageFile(new Blob())).resolves.toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });
});
