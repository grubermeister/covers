/**
 * Canonical user-facing labels for the Submission workflow.
 * Authority: docs/glossary.md (Submission) and docs/devel/vocab.md (Action Verbs).
 */

export const SUBMISSION_LABELS = {
  toast: {
    received: "Submission received",
    failed: "Submission failed",
  },
  action: {
    submitMarking: "Submit Marking",
    submitNewMarking: "Submit New Marking",
    submitEditToMarking: "Submit Edit to Existing Marking",
    submitCover: "Submit Cover",
    submitNewCover: "Submit New Cover",
    submitEditToCover: "Submit Edit to Cover",
    resubmit: "Resubmit",
    saveDraft: "Save Draft",
  },
} as const;
