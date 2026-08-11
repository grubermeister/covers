interface CoverReviewBannerProps {
  /** Review status of the cover or contribution being edited, lower-cased. */
  status: string | null | undefined;
  /** The editor's comment, when there is one. */
  notes: string | null | undefined;
}

/**
 * Tells a contributor where their submission stands while they are editing it.
 *
 * This exists as its own component because the two routes into the cover editor
 * (`/cover/:coverId/edit` and `/cover/new?edit=<contributionId>`) load their review
 * state from different objects. Gating the banner on the editor route alone meant a
 * returned submission reached its contributor with no explanation of what to change.
 */
export const CoverReviewBanner = ({ status, notes }: CoverReviewBannerProps) => {
  const normalized = String(status ?? "").trim().toLowerCase();

  if (normalized === "pending") {
    return (
      <p className="text-sm rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-amber-900 dark:text-amber-100">
        This cover is <strong>pending editor review</strong>. It is only visible to you and assigned
        editors until it is approved.
      </p>
    );
  }

  if (normalized === "needs_revision") {
    const comment = String(notes ?? "").trim();
    return (
      <div className="text-sm rounded-md border border-orange-500/30 bg-orange-500/5 px-3 py-2 space-y-1">
        <p className="font-medium text-foreground">Editor requested changes</p>
        {comment.length > 0 ? (
          <p className="text-muted-foreground whitespace-pre-wrap">{comment}</p>
        ) : (
          <p className="text-muted-foreground">Update the cover below, then save to send it back.</p>
        )}
      </div>
    );
  }

  return null;
};
