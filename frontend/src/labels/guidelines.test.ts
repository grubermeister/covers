/**
 * Regression tests for the centralized submission-guideline copy.
 * These lock in the specific wording Ian requested (2026-06) and the DRY
 * guarantee that both submission forms render the same shared guidance.
 */
import {
  SHARED_GUIDELINES,
  MARKING_SUBMISSION_GUIDELINES,
  COVER_SUBMISSION_GUIDELINES,
  INSCRIPTION_TEXT_HELP,
  DATE_FORMAT_HELP,
  LETTERING_HELP,
} from "./guidelines";

const bodyFor = (list: { label: string; body: string }[], label: string) =>
  list.find((g) => g.label === label)?.body ?? "";

describe("submission guidelines copy", () => {
  it("states the 300 dpi image-quality preference", () => {
    expect(bodyFor(SHARED_GUIDELINES, "Image Quality")).toMatch(/300 dpi/i);
  });

  it("directs submitters to note new references to the editor", () => {
    expect(bodyFor(SHARED_GUIDELINES, "Reference works")).toMatch(
      /note to the editor/i,
    );
  });

  it("explains Rate vs. Auxiliary markings on the marking form only", () => {
    const marking = bodyFor(MARKING_SUBMISSION_GUIDELINES, "Rate vs. Auxiliary markings");
    expect(marking).toMatch(/rate mark/i);
    expect(marking).toMatch(/auxiliary/i);
    // Issue #23: the copy must state the number-present heuristic explicitly.
    expect(marking).toMatch(/number/i);
    // Cover form must NOT carry the marking-type explanation.
    expect(
      COVER_SUBMISSION_GUIDELINES.some((g) => g.label === "Rate vs. Auxiliary markings"),
    ).toBe(false);
  });

  it("asks for date verification on the cover form only", () => {
    expect(bodyFor(COVER_SUBMISSION_GUIDELINES, "Date verification")).toMatch(
      /exterior of the cover/i,
    );
    expect(
      MARKING_SUBMISSION_GUIDELINES.some((g) => g.label === "Date verification"),
    ).toBe(false);
  });

  it("renders identical shared guidance on both forms (DRY)", () => {
    for (const shared of SHARED_GUIDELINES) {
      expect(MARKING_SUBMISSION_GUIDELINES).toContainEqual(shared);
      expect(COVER_SUBMISSION_GUIDELINES).toContainEqual(shared);
    }
  });
});

describe("field help copy", () => {
  it("tells submitters to enter the exact marking text", () => {
    expect(INSCRIPTION_TEXT_HELP).toMatch(/exact text/i);
  });

  it("explains the date-format codes by example", () => {
    expect(DATE_FORMAT_HELP).toMatch(/MD/);
  });

  it("explains the lettering field and its manuscript exception", () => {
    expect(LETTERING_HELP).toMatch(/letter/i);
    expect(LETTERING_HELP).toMatch(/manuscript/i);
  });
});
