/**
 * Input parsing for the "Link Existing Cover / Marking" dialogs (issue #47).
 *
 * Editors paste IDs in the forms they see around the site: bare numbers,
 * cover codes like "C-42" / "c42", or marking route ids like "api-12".
 * Returns the positive integer id, or null when the input is not linkable.
 */
export function parseCoverIdInput(raw: string): number | null {
  const value = parseInt(raw.trim().replace(/^[Cc]-?/, ""), 10);
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function parseMarkingIdInput(raw: string): number | null {
  const value = parseInt(raw.trim().replace(/^api-/, ""), 10);
  return Number.isFinite(value) && value > 0 ? value : null;
}
