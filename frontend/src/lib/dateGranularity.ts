// Shared date-granularity helper for the contribution forms.
//
// Both the cover-date field (CoverEdit) and the marking ERD/LRD fields
// (Contribute) take a single ISO date from an <input type="date"> and infer
// the intended precision from the date's shape, matching the DateSeen.
// granularity model on the backend (DAY / MONTH / YEAR):
//   YYYY-01-01 -> YEAR     (year-only catalog dates, e.g. "1845")
//   YYYY-MM-01 -> MONTH
//   YYYY-MM-DD -> DAY
// This keeps a "1845" catalog entry from being stored as a spurious Jan-1 day.

export type DateGranularity = "DAY" | "MONTH" | "YEAR";

export function deriveGranularityFromIso(
  iso: string,
): { granularity: DateGranularity; normalizedDate: string } | null {
  const trimmed = iso.trim();
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(trimmed);
  if (!m) return null;
  const y = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (!Number.isFinite(y) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;

  const normalizedDate = `${m[1]}-${m[2]}-${m[3]}`;
  if (month === 1 && day === 1) {
    return { granularity: "YEAR", normalizedDate: `${y}-01-01` };
  }
  if (day === 1) {
    return { granularity: "MONTH", normalizedDate: `${y}-${String(month).padStart(2, "0")}-01` };
  }
  return { granularity: "DAY", normalizedDate };
}
