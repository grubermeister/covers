/**
 * Canonical user-facing submission-guideline and field-help copy.
 *
 * Centralized here (DRY) so the Marking submit page (Contribute.tsx) and the
 * Cover submit/edit page (CoverEdit.tsx) render identical shared guidance from a
 * single source instead of copy-pasted JSX. Edit once; both pages stay in sync.
 *
 * Authority: Ian/Michael UI-change requests (2026-06). The philatelic wording
 * in MARKING_TYPE_GUIDELINE is a sensible default; Ian can refine it later.
 */

export interface Guideline {
  label: string;
  body: string;
}

/** Guidance shared by both the Marking and Cover submission forms. */
export const SHARED_GUIDELINES: Guideline[] = [
  {
    label: "Image Quality",
    body: "Provide clear, high-resolution scans or photographs — 300 dpi preferred.",
  },
  {
    label: "Accuracy",
    body: "Verify all dates, locations, and details before submission.",
  },
  {
    label: "Reference works",
    body:
      "Include established reference works when available. To cite a reference " +
      "that is not in the list, describe it in a note to the editor.",
  },
  {
    label: "Review Time",
    body: "Most submissions are reviewed within 1-3 business days.",
  },
];

/**
 * Marking-only: explains the difference between a Rate Mark and an Auxiliary
 * marking. Reasonable default wording; Ian can refine the phrasing later.
 */
export const MARKING_TYPE_GUIDELINE: Guideline = {
  label: "Rate vs. Auxiliary markings",
  body:
    'A Rate Mark shows the postage rate paid or due (often a numeral, e.g. "3", ' +
    '"5", or "PAID 3"). An Auxiliary marking is any other handstamp that is ' +
    'neither a town/postmark nor a rate — e.g. "FORWARDED", "MISSENT", or ' +
    '"DUE". If unsure, pick the closest type and add a note for the editor.',
};

/** Cover-only: request date verification when the date is not on the exterior. */
export const COVER_DATE_VERIFICATION_GUIDELINE: Guideline = {
  label: "Date verification",
  body:
    "If the date is not shown on the exterior of the cover, please include an " +
    "image that verifies the date (for example, the enclosed letter or docketing).",
};

/** Composed list rendered on the Marking submission form (Contribute.tsx). */
export const MARKING_SUBMISSION_GUIDELINES: Guideline[] = [
  ...SHARED_GUIDELINES,
  MARKING_TYPE_GUIDELINE,
];

/** Composed list rendered on the Cover submission form (CoverEdit.tsx). */
export const COVER_SUBMISSION_GUIDELINES: Guideline[] = [
  ...SHARED_GUIDELINES,
  COVER_DATE_VERIFICATION_GUIDELINE,
];

/**
 * Field help (visible description, not a hover-only tooltip — essential form
 * guidance must be persistent for touch and screen-reader users).
 */
export const INSCRIPTION_TEXT_HELP =
  "Enter the exact text as it appears on the marking, including abbreviations and punctuation.";

export const DATE_FORMAT_HELP =
  "How the date appears on the marking. Each option shows its code and meaning — e.g. MD = Month / Day.";
