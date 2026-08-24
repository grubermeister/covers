import type { MarkingRecord } from "@/services/markings";
import { getMarkingListImageUrl, normalizeImageUrl } from "@/services/markings";
import { formatRateValue } from "@/lib/rateDisplay";
import {
  formatDateSeenLike,
  type DateSeenGranularity,
} from "@/lib/partialDate";
import { isTrueCircleShapeName } from "@/lib/shapeDisplay";

/** Shown when a catalog field has no value (Catalog Search / Record Detail contract). */
export const CATALOG_FIELD_EMPTY = "-";

export function displayCatalogField(v: string | null | undefined): string {
  const s = v != null ? String(v).trim() : "";
  return s.length > 0 ? s : CATALOG_FIELD_EMPTY;
}

const MARKING_TYPE_LABELS: Record<string, string> = {
  TOWNMARK: "Townmark",
  RATEMARK: "Ratemark",
  AUXMARK: "Auxmark",
};

export function markingTypeLabel(type: string | null | undefined): string {
  const key = String(type ?? "").trim().toUpperCase();
  return MARKING_TYPE_LABELS[key] ?? "";
}

function markingTextFromRecord(record: MarkingRecord): string {
  const cat = record.catalogTxt?.trim();
  const ins = record.inscriptionTxt?.trim();
  if (cat && ins) return `${cat} (${ins})`;
  return cat || ins || "";
}

/**
 * Format a partial-or-full ISO date for catalog display.
 * "YYYY-MM-DD" -> "MM/DD/YYYY", "YYYY-MM" -> "MM/YYYY", "YYYY" -> "YYYY".
 * Anything else (already-formatted ranges, etc.) is returned unchanged.
 */
export function formatCatalogDate(value: string | null | undefined): string {
  const s = value != null ? String(value).trim() : "";
  if (!s) return "";
  const dayMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (dayMatch) return `${dayMatch[2]}/${dayMatch[3]}/${dayMatch[1]}`;
  const monthMatch = /^(\d{4})-(\d{2})$/.exec(s);
  if (monthMatch) return `${monthMatch[2]}/${monthMatch[1]}`;
  if (/^\d{4}$/.test(s)) return s;
  return s;
}

const DATE_SEEN_MONTH_LABELS = [
  "JAN",
  "FEB",
  "MAR",
  "APR",
  "MAY",
  "JUN",
  "JUL",
  "AUG",
  "SEP",
  "OCT",
  "NOV",
  "DEC",
];

function normalizeDateSeenGranularity(
  value: string | null | undefined,
): DateSeenGranularity {
  const s = String(value ?? "").trim().toUpperCase();
  if (s === "MONTH") return "MONTH";
  if (s === "YEAR") return "YEAR";
  return "DAY";
}

/**
 * Format a DateSeen row by its stored granularity.
 * DAY: "1865-08-14" -> "08/14/1865"
 * MONTH: "1865-08-01" -> "AUG, 1865"
 * YEAR: "1865-01-01" -> "1865"
 */
export function formatDateSeen(
  value: string | null | undefined,
  granularity: string | null | undefined,
  parts?: {
    dateYear?: number | null;
    dateMonth?: number | null;
    dateDay?: number | null;
  },
): string {
  if (
    parts &&
    (parts.dateYear != null || parts.dateMonth != null || parts.dateDay != null)
  ) {
    return formatDateSeenLike({
      date: value,
      granularity,
      dateYear: parts.dateYear,
      dateMonth: parts.dateMonth,
      dateDay: parts.dateDay,
    });
  }
  const rawGranularity = String(granularity ?? "").trim().toUpperCase();
  if (
    rawGranularity === "MONTH_ONLY" ||
    rawGranularity === "DAY_ONLY" ||
    rawGranularity === "YEAR_DAY" ||
    rawGranularity === "MONTH_DAY"
  ) {
    return formatDateSeenLike({ date: value, granularity });
  }
  const s = value != null ? String(value).trim() : "";
  if (!s) return "";
  const g = normalizeDateSeenGranularity(granularity);
  const isoMatch = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(s);
  if (g === "YEAR") return isoMatch ? isoMatch[1] : s;
  if (g === "MONTH") {
    if (!isoMatch || !isoMatch[2]) return s;
    const monthIndex = Number(isoMatch[2]) - 1;
    const monthLabel = DATE_SEEN_MONTH_LABELS[monthIndex];
    return monthLabel ? `${monthLabel}, ${isoMatch[1]}` : s;
  }
  return formatCatalogDate(s) || s;
}

/**
 * Format a marking's full set of observed dates into a single "Dates Seen"
 * string (issue #25). Each row is formatted via formatDateSeen, then de-duped
 * preserving order. The caller (RecordDetail) decides where to render it; this
 * helper is pure so it can be unit-tested.
 *
 * Returns "" — collapsing the row — unless the listing adds something the
 * Earliest/Latest Seen rows do not already convey (issue #122):
 *   - fewer than two distinct dates: the single date IS Earliest/Latest; or
 *   - every date equals one of the two boundary labels, so the row would just
 *     restate the rows above it.
 *
 * Ian asked for the row to be removed outright on the grounds that the dates
 * are visible elsewhere. They are not: this listing is MARKING-scoped, while
 * the associated-cover cards show COVER-scoped dates — disjoint sets. On the
 * VA/WV catalogue that distinction hides ~430 recorded observations across
 * ~283 markings, so instead of deleting the row it is shown only where it
 * still carries information (~7% of markings) and collapses everywhere else.
 * Pass the formatted boundary labels to get that behaviour; omit them and the
 * helper keeps its original issue-#25 semantics.
 */
export function formatDatesSeenList(
  rows: ReadonlyArray<{
    date: string | null;
    granularity: string | null;
    dateYear?: number | null;
    dateMonth?: number | null;
    dateDay?: number | null;
  }>,
  boundaryLabels: ReadonlyArray<string> = [],
): string {
  const seen = new Set<string>();
  const formatted: string[] = [];
  for (const row of rows) {
    const label = formatDateSeen(row.date, row.granularity, {
      dateYear: row.dateYear,
      dateMonth: row.dateMonth,
      dateDay: row.dateDay,
    });
    if (label && !seen.has(label)) {
      seen.add(label);
      formatted.push(label);
    }
  }
  if (formatted.length < 2) return "";
  const bounds = new Set(boundaryLabels.filter(Boolean));
  // Every date already shown as Earliest/Latest => the row adds nothing.
  if (bounds.size > 0 && formatted.every((label) => bounds.has(label))) return "";
  return formatted.join(", ");
}

/** Extract the leading 4-digit year from a partial-or-full ISO date. */
export function yearFromCatalogDate(value: string | null | undefined): string {
  const s = value != null ? String(value).trim() : "";
  if (!s) return "";
  const m = /^(\d{4})/.exec(s);
  return m ? m[1] : s;
}

/** Values for the fixed catalog field block (search cards + record detail). */
export type CatalogFieldValues = {
  type: string;
  town: string;
  state: string;
  regionAbbrev: string;
  manuscript: string;
  desc: string;
  markingTextLines: string[];
  markingTextSingle: string;
  shape: string;
  lettering: string;
  impression: string;
  irregular: string;
  dimensions: string;
  color: string;
  rateValue: string;
  earliestSeen: string;
  latestSeen: string;
};

function dimensionsField(record: MarkingRecord): string {
  const w = record.width?.trim() ?? "";
  const h = record.height?.trim() ?? "";

  // Circle: display as diameter (Search + Record Detail requirement parity).
  if (!record.isManuscript && isTrueCircleShapeName(record.shapeName)) {
    const d = w || h;
    if (d) return `${d} mm diameter`;
    return "";
  }

  if (record.sizeDisplay && record.sizeDisplay.trim()) {
    return record.sizeDisplay.trim().includes("mm")
      ? record.sizeDisplay.trim()
      : `${record.sizeDisplay.trim()} mm`;
  }
  if (w && h) return `${w}x${h} mm`;
  if (w) return `${w} mm`;
  if (h) return `${h} mm`;
  return "";
}

export function buildCatalogFieldValues(record: MarkingRecord): CatalogFieldValues {
  const combined = markingTextFromRecord(record);
  const markingTextLines = combined
    ? combined.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
    : [];
  const markingTextSingle =
    markingTextLines.length <= 1
      ? displayCatalogField(markingTextLines.length === 1 ? markingTextLines[0] : combined)
      : "";

  return {
    type: displayCatalogField(markingTypeLabel(record.type) || "Townmark"),
    town: displayCatalogField(record.town),
    state: displayCatalogField(record.state),
    regionAbbrev: displayCatalogField(record.stateAbbrev),
    manuscript: displayCatalogField(record.isManuscript ? "Yes" : "No"),
    desc: displayCatalogField(record.desc),
    markingTextLines: markingTextLines.length > 1 ? markingTextLines : [],
    markingTextSingle,
    shape: displayCatalogField(record.shapeName),
    lettering: displayCatalogField(record.letteringName),
    impression: displayCatalogField(record.impression),
    irregular: displayCatalogField(
      record.isIrreg == null ? null : record.isIrreg ? "Yes" : "No",
    ),
    dimensions: displayCatalogField(dimensionsField(record)),
    color: displayCatalogField(record.colorName),
    rateValue: displayCatalogField(formatRateValue(record.rateVal)),
    earliestSeen: displayCatalogField(
      formatDateSeen(record.earliestSeen, record.earliestSeenGranularity),
    ),
    latestSeen: displayCatalogField(
      formatDateSeen(record.latestSeen, record.latestSeenGranularity),
    ),
  };
}

export type CatalogSearchRowDisplay = CatalogFieldValues & {
  cardId: string;
  title: string;
  image: string | null;
  image2: string | null;
};

export function buildCatalogSearchTitleFromParts({
  town,
  region,
  inscription,
  code,
}: {
  town?: string | null;
  region?: string | null;
  inscription?: string | null;
  code?: string | null;
}): string {
  const townPart = town?.trim() ?? "";
  const regionPart = region?.trim() ?? "";
  const inscriptionPartRaw = inscription?.trim() ?? "";
  let location = "";
  if (townPart && regionPart) location = `${townPart}, ${regionPart}`;
  else if (townPart) location = townPart;
  else if (regionPart) location = regionPart;

  const inscriptionPart = inscriptionPartRaw ? `"${inscriptionPartRaw}"` : "";

  if (location && inscriptionPart) return `${location} - ${inscriptionPart}`;
  if (location) return location;
  if (inscriptionPart) return inscriptionPart;
  return code?.trim() || CATALOG_FIELD_EMPTY;
}

/**
 * Build the search-listing title.
 * Format: `<post office>, <region abbrev> - "<inscription>"`.
 * Example: `Williamsburg, VA - "Wmsburg/VA"`.
 */
function buildSearchTitle(record: MarkingRecord): string {
  return buildCatalogSearchTitleFromParts({
    town: record.postOfficeName,
    region: record.stateAbbrev,
    inscription: record.inscriptionTxt,
    code: record.code,
  });
}

export function buildCatalogSearchRow(record: MarkingRecord): CatalogSearchRowDisplay {
  const fields = buildCatalogFieldValues(record);

  return {
    ...fields,
    cardId: `api-${record.id}`,
    title: buildSearchTitle(record),
    image: normalizeImageUrl(getMarkingListImageUrl(record.mainImage)),
    image2: normalizeImageUrl(getMarkingListImageUrl(record.secondImage)),
  };
}
