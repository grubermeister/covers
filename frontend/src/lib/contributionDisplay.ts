/** Helpers to distinguish cover vs marking rows in Contribution.submitted_data. */

const COVER_TYPE_LABELS: Record<string, string> = {
  FC: "Folded Cover",
  FL: "Folded Letter",
};

export function isCoverContributionData(sd: Record<string, unknown> | null | undefined): boolean {
  if (!sd || typeof sd !== "object") return false;
  const kind = String(sd.submission_kind ?? sd.submissionKind ?? "")
    .trim()
    .toLowerCase();
  if (kind === "cover") return true;
  if (kind === "marking" || kind === "postmark") return false;

  const type = String(sd.type ?? "")
    .trim()
    .toUpperCase();
  const hasCoverType = type === "FC" || type === "FL";
  const hasMarkingType =
    type === "TOWNMARK" || type === "RATEMARK" || type === "AUXMARK";
  const hasTown = String(sd.town ?? "").trim().length > 0;
  const parentRaw = sd.parent_marking_id ?? sd.marking_id ?? sd.parentMarkingId;
  const hasParent =
    parentRaw != null && String(parentRaw).trim() !== "" && String(parentRaw) !== "0";
  const hasCoverDate = String(sd.cover_date ?? sd.coverDate ?? "").trim().length > 0;

  return Boolean(hasParent && (hasCoverType || hasCoverDate) && !hasTown && !hasMarkingType);
}

export function parentMarkingIdFromContribution(sd: Record<string, unknown>): number | null {
  const raw = sd.parent_marking_id ?? sd.marking_id ?? sd.parentMarkingId;
  if (raw == null || raw === "") return null;
  const n = parseInt(String(raw), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function materializedCoverIdFromContribution(sd: Record<string, unknown>): number | null {
  const raw = sd.cover_id ?? sd.coverId;
  if (raw == null || raw === "") return null;
  const n = parseInt(String(raw), 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function coverContributionDisplayName(
  sd: Record<string, unknown>,
  contributionId: number,
  status?: string,
): string {
  const typeCode = String(sd.type ?? "").trim().toUpperCase();
  const typeLabel = COVER_TYPE_LABELS[typeCode] || typeCode || "Cover";
  const date = String(sd.cover_date ?? sd.coverDate ?? "").trim();
  const markingId = parentMarkingIdFromContribution(sd);
  const isDraft = String(status ?? "").trim().toLowerCase() === "draft";
  const parts = [isDraft ? "Cover draft" : "Cover", typeLabel];
  if (date) parts.push(date);
  if (markingId != null) parts.push(`Marking #${markingId}`);
  const label = parts.filter(Boolean).join(" · ");
  return label || `${isDraft ? "Cover draft" : "Cover"} #${contributionId}`;
}
