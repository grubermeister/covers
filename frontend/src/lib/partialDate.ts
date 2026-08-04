export type DateSeenGranularity =
  | "YEAR"
  | "MONTH"
  | "DAY"
  | "MONTH_ONLY"
  | "DAY_ONLY"
  | "YEAR_DAY"
  | "MONTH_DAY";

export type PartialDateInput = {
  unknown: boolean;
  year: string;
  month: string;
  day: string;
};

export type PartialDatePayloadKeys = {
  unknown: string;
  year: string;
  month: string;
  day: string;
  legacyDate: string;
  legacyGranularity: string;
};

export type PartialDateValue = {
  unknown: boolean;
  granularity: DateSeenGranularity | null;
  year: number | null;
  month: number | null;
  day: number | null;
  legacyDate: string | null;
  legacyGranularity: "YEAR" | "MONTH" | "DAY" | null;
};

export type PartialDateValidation =
  | { ok: true; value: PartialDateValue }
  | { ok: false; error: string };

const MONTH_LABELS = [
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

function intFromText(value: unknown): number | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  if (!/^\d+$/.test(text)) return NaN;
  return Number.parseInt(text, 10);
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function isLeapYear(year: number): boolean {
  return year % 400 === 0 || (year % 4 === 0 && year % 100 !== 0);
}

function maxDayForMonth(year: number | null, month: number): number {
  if ([1, 3, 5, 7, 8, 10, 12].includes(month)) return 31;
  if ([4, 6, 9, 11].includes(month)) return 30;
  if (month === 2) return year == null || isLeapYear(year) ? 29 : 28;
  return 31;
}

export function granularityForParts(
  year: number | null,
  month: number | null,
  day: number | null,
): DateSeenGranularity | null {
  const hasYear = year != null;
  const hasMonth = month != null;
  const hasDay = day != null;
  if (hasYear && hasMonth && hasDay) return "DAY";
  if (hasYear && hasMonth) return "MONTH";
  if (hasYear && hasDay) return "YEAR_DAY";
  if (hasMonth && hasDay) return "MONTH_DAY";
  if (hasYear) return "YEAR";
  if (hasMonth) return "MONTH_ONLY";
  if (hasDay) return "DAY_ONLY";
  return null;
}

export function validatePartialDate(input: PartialDateInput): PartialDateValidation {
  if (input.unknown) {
    return {
      ok: true,
      value: {
        unknown: true,
        granularity: null,
        year: null,
        month: null,
        day: null,
        legacyDate: null,
        legacyGranularity: null,
      },
    };
  }

  const year = intFromText(input.year);
  const month = intFromText(input.month);
  const day = intFromText(input.day);

  if (Number.isNaN(year)) return { ok: false, error: "Year must contain digits only." };
  if (Number.isNaN(month)) return { ok: false, error: "Month must contain digits only." };
  if (Number.isNaN(day)) return { ok: false, error: "Day must contain digits only." };
  if (year != null && (year < 1 || year > 9999)) {
    return { ok: false, error: "Year must be between 1 and 9999." };
  }
  if (month != null && (month < 1 || month > 12)) {
    return { ok: false, error: "Month must be between 1 and 12." };
  }
  if (day != null && (day < 1 || day > 31)) {
    return { ok: false, error: "Day must be between 1 and 31." };
  }
  if (month != null && day != null && day > maxDayForMonth(year, month)) {
    return { ok: false, error: "Day is not valid for the selected month." };
  }

  const granularity = granularityForParts(year, month, day);
  if (granularity == null) {
    return { ok: false, error: "Enter a date component or select Date unknown." };
  }

  let legacyDate: string | null = null;
  let legacyGranularity: "YEAR" | "MONTH" | "DAY" | null = null;
  if (granularity === "YEAR" && year != null) {
    legacyDate = `${year}-01-01`;
    legacyGranularity = "YEAR";
  } else if (granularity === "MONTH" && year != null && month != null) {
    legacyDate = `${year}-${pad2(month)}-01`;
    legacyGranularity = "MONTH";
  } else if (granularity === "DAY" && year != null && month != null && day != null) {
    legacyDate = `${year}-${pad2(month)}-${pad2(day)}`;
    legacyGranularity = "DAY";
  }

  return {
    ok: true,
    value: {
      unknown: false,
      granularity,
      year,
      month,
      day,
      legacyDate,
      legacyGranularity,
    },
  };
}

export function partialDateInputFromSubmittedData(sd: Record<string, unknown>): PartialDateInput {
  return partialDateInputFromPayload(sd, {
    unknown: "cover_date_unknown",
    year: "cover_date_year",
    month: "cover_date_month",
    day: "cover_date_day",
    legacyDate: "cover_date",
    legacyGranularity: "cover_granularity",
  }, {
    legacyDate: "coverDate",
    legacyGranularity: "coverGranularity",
  });
}

export function partialDateInputFromPayload(
  sd: Record<string, unknown>,
  keys: PartialDatePayloadKeys,
  fallbackKeys: Partial<PartialDatePayloadKeys> = {},
): PartialDateInput {
  const unknown = String(sd[keys.unknown] ?? sd[fallbackKeys.unknown ?? ""] ?? "")
    .trim()
    .toLowerCase();
  if (["true", "1", "yes", "on"].includes(unknown)) {
    return { unknown: true, year: "", month: "", day: "" };
  }

  const year = String(sd[keys.year] ?? sd[fallbackKeys.year ?? ""] ?? "").trim();
  const month = String(sd[keys.month] ?? sd[fallbackKeys.month ?? ""] ?? "").trim();
  const day = String(sd[keys.day] ?? sd[fallbackKeys.day ?? ""] ?? "").trim();
  if (year || month || day) {
    return { unknown: false, year, month, day };
  }

  const legacy = String(sd[keys.legacyDate] ?? sd[fallbackKeys.legacyDate ?? ""] ?? "").trim();
  if (!legacy) return { unknown: false, year: "", month: "", day: "" };
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(legacy);
  if (!parts) return { unknown: false, year: "", month: "", day: "" };
  const granularity = String(sd[keys.legacyGranularity] ?? sd[fallbackKeys.legacyGranularity ?? ""] ?? "DAY")
    .trim()
    .toUpperCase();
  if (granularity === "YEAR") {
    return { unknown: false, year: parts[1], month: "", day: "" };
  }
  if (granularity === "MONTH") {
    return { unknown: false, year: parts[1], month: String(Number(parts[2])), day: "" };
  }
  return {
    unknown: false,
    year: parts[1],
    month: String(Number(parts[2])),
    day: String(Number(parts[3])),
  };
}

export function partialDateInputFromDateSeen(row: {
  date?: string | null;
  granularity?: string | null;
  dateYear?: number | null;
  dateMonth?: number | null;
  dateDay?: number | null;
}): PartialDateInput {
  if (row.dateYear != null || row.dateMonth != null || row.dateDay != null) {
    return {
      unknown: false,
      year: row.dateYear == null ? "" : String(row.dateYear),
      month: row.dateMonth == null ? "" : String(row.dateMonth),
      day: row.dateDay == null ? "" : String(row.dateDay),
    };
  }
  return partialDateInputFromSubmittedData({
    cover_date: row.date ?? "",
    cover_granularity: row.granularity ?? "",
  });
}

export function formatPartialDateValue(value: PartialDateValue): string {
  if (value.unknown) return "Date unknown";
  return formatPartialDateParts(value.year, value.month, value.day);
}

export function formatPartialDateParts(
  year: number | null,
  month: number | null,
  day: number | null,
): string {
  const monthLabel = month == null ? "" : MONTH_LABELS[month - 1] ?? "";
  if (year != null && monthLabel && day != null) return `${pad2(month)}/${pad2(day)}/${year}`;
  if (year != null && monthLabel) return `${monthLabel}, ${year}`;
  if (year != null && day != null) return `Day ${day}, ${year} (month unknown)`;
  if (monthLabel && day != null) return `${monthLabel} ${day} (year unknown)`;
  if (year != null) return String(year);
  if (monthLabel) return `${monthLabel} (year unknown)`;
  if (day != null) return `Day ${day} (month/year unknown)`;
  return "";
}

export function formatPartialDateInput(input: PartialDateInput): string {
  const result = validatePartialDate(input);
  return result.ok ? formatPartialDateValue(result.value) : "";
}

export function formatDateSeenLike(row: {
  date?: string | null;
  granularity?: string | null;
  dateYear?: number | null;
  dateMonth?: number | null;
  dateDay?: number | null;
}): string {
  const input = partialDateInputFromDateSeen(row);
  return formatPartialDateInput(input);
}
