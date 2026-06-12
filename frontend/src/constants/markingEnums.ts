/**
 * Inline enums from the v2 Marking contract (see docs/model.md).
 *
 * In v2, `date_fmt`, `date_type`, and `impression` are inline enums on
 * the Marking record. They are not separate DRF resources.
 */

/** v2 Marking.date_fmt values */
export const DATE_FMT_VALUES = ["MD", "MDD", "YD", "YMD", "YMDD"] as const;
export type DateFmt = (typeof DATE_FMT_VALUES)[number];

export const DATE_FMT_LABELS: Record<DateFmt, string> = {
  MD: "Month / Day",
  MDD: "Month / Day (two-digit)",
  YD: "Year / Day-of-year",
  YMD: "Year / Month / Day",
  YMDD: "Year / Month / Day (two-digit)",
};

/** v2 Marking.date_type values */
export const DATE_TYPE_VALUES = ["BISHOP MARK", "FRANKLIN MARK", "QUAKER DATE"] as const;
export type DateType = (typeof DATE_TYPE_VALUES)[number];

/** v2 Marking.impression values */
export const IMPRESSION_VALUES = ["Normal", "Stencil", "Negative"] as const;
export type Impression = (typeof IMPRESSION_VALUES)[number];

/**
 * Shape compatible with the old dateFormats service's option type.
 * `id` is a stable 1-based ordinal (the position in DATE_FMT_VALUES)
 * preserved so callers that submit numeric `date_format_id` keep working
 * through the transition to enum-string payloads.
 */
export interface DateFormatOption {
  id: number;
  name: string;
  description: string;
}

export const DATE_FMT_OPTIONS: DateFormatOption[] = DATE_FMT_VALUES.map(
  (code, index) => ({
    id: index + 1,
    name: DATE_FMT_LABELS[code],
    description: code,
  })
);

/**
 * Drop-in replacement for the old `getDateFormats()` service.
 * Returns the static list; no network call.
 */
export async function getDateFormats(): Promise<DateFormatOption[]> {
  return DATE_FMT_OPTIONS;
}
