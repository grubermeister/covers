// Reader for the `vphc` provenance blob that apply_vphc_ledger attaches to
// every contribution produced by the VPHC ingest.
//
// The blob is not catalog data -- contributionToFields deliberately ignores it
// -- but it carries the uncertainty the ingest could not resolve, and a
// reviewer approving one of these records needs to see it. Without this strip
// a marking whose century was guessed and whose county could not be resolved
// looks exactly like one read cleanly off the sheet.

export type VphcFlagSeverity = "uncertain" | "repaired";

export interface VphcFlag {
  code: string;
  label: string;
  reason: string;
  severity: VphcFlagSeverity;
}

export interface VphcProvenance {
  code: string;
  source: string;
  cancelNo: string;
  county: string;
  rulesVersion: string;
  unmatchedReason: string;
  flags: VphcFlag[];
}

// Mirrors FLAG_REASONS in tools/vphc_crossexam.py (the source of truth) plus
// the two flags apply_vphc_ledger adds at contribution time. Keep the wording
// in step with that dict -- a reviewer should read the same sentence here as
// in the crossexam report.
const FLAG_REASONS: Record<string, string> = {
  century_inferred:
    "The century was reconstructed from context — the spreadsheet stored only a two-digit year (rule R1).",
  date_low_confidence:
    "Date confidence is low: the run was too short to corroborate the century against neighbouring dates.",
  date_unresolved:
    "The date could not be read as a date at all — the cell holds vocabulary nobody has defined yet (R1b).",
  size_unparsed: "The size could not be parsed from the source text (R5).",
  color_vocab: "The colour is not in the known vocabulary and needs a human call (R6).",
  county_backfilled:
    "The county was blank in the source and was recovered from elsewhere in the sheet (R2c).",
  county_repaired: "The county name was OCR-damaged and was repaired by fuzzy match (R2).",
  county_uncertain: "The county could not be resolved with confidence (R2).",
  column_shift:
    "This row's columns were shifted one place left in the source and were repaired (R6c).",
  state_unknown:
    "No county, so the row could not be routed to Virginia or West Virginia (R3).",
  no_scan: "The spreadsheet has no scan for this marking.",
  // added by apply_vphc_ledger rather than the crossexam pass
  type_defaulted:
    "The sheet's device code was not recognised, so the marking type was defaulted — please correct it.",
  color_unrecognised:
    "The sheet's colour is not in the catalog's colour list, so no colour was set on this marking.",
};

// "repaired" means the ingest changed a value and is telling you so;
// "uncertain" means the value may still be wrong. Uncertain sorts first.
const REPAIRED_FLAGS = new Set([
  "century_inferred",
  "county_backfilled",
  "county_repaired",
  "column_shift",
]);

// Why the marking was catalogued as new instead of matched to an existing
// record. The create_* verdicts are not failures -- nothing from the sheet is
// ever dropped, so an unmatched marking becomes a new record carrying its
// reason (see CREATE_BUCKETS in tools/vphc_crossexam.py).
const UNMATCHED_REASONS: Record<string, string> = {
  ambiguous:
    "Matched more than one existing record and none could be chosen — catalogued separately rather than guessed (I3/I3c).",
  town_damaged:
    "This town name maps to several post offices in the catalogue, so the marking could not be placed (I0b).",
  no_colour_match:
    "The inscription and device type matched an existing record but this colour is not among its variants.",
  unclassified_device: "The device code is not yet in the vocabulary — needs Ian / Wayne Farley.",
  create_no_town: "No post office with this name existed, so the town was created (I0c).",
  create_no_prod_markings: "The town exists but had no non-manuscript markings to match against.",
  create_no_inscription: "No existing inscription matches, so this was catalogued as a new marking.",
  create_no_type_match: "No existing marking of this device type matches, so this was catalogued as new.",
};

function humanize(code: string): string {
  return code.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function str(value: unknown): string {
  return value == null ? "" : String(value).trim();
}

export function describeVphcFlag(code: string): VphcFlag {
  return {
    code,
    label: humanize(code),
    // An unknown flag is still shown. The pipeline's vocabulary can grow, and
    // dropping a flag we don't recognise would hide exactly the novel problem
    // a reviewer most needs to see.
    reason: FLAG_REASONS[code] ?? "This flag is not in the adapter's glossary yet.",
    severity: REPAIRED_FLAGS.has(code) ? "repaired" : "uncertain",
  };
}

export function describeVphcUnmatchedReason(code: string): string {
  return UNMATCHED_REASONS[code] ?? humanize(code);
}

// Returns null for any contribution that did not come from the VPHC ingest,
// which is what the caller uses to decide whether to render the strip at all.
export function readVphcProvenance(sd: Record<string, unknown>): VphcProvenance | null {
  const raw = sd.vphc;
  if (raw == null || typeof raw !== "object" || Array.isArray(raw)) return null;
  const blob = raw as Record<string, unknown>;

  const rawFlags = Array.isArray(blob.flags) ? blob.flags : [];
  const flags = rawFlags
    .map((f) => str(f))
    .filter(Boolean)
    .map(describeVphcFlag)
    .sort((a, b) => {
      if (a.severity !== b.severity) return a.severity === "uncertain" ? -1 : 1;
      return a.code.localeCompare(b.code);
    });

  return {
    code: str(blob.vphc_code),
    source: str(blob.src),
    cancelNo: str(blob.cancel_no),
    county: str(blob.county),
    rulesVersion: str(blob.rules_version),
    unmatchedReason: str(blob.why_unmatched),
    flags,
  };
}
