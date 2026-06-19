// Canonical field order shared by RecordDetail (read-only catalog view) and
// ContributionDetail (read-only contribution view). Keeping the order here
// guarantees both pages render rows in the same sequence so reviewers can
// scan them side-by-side without re-orienting.

import type { MarkingTypeValue } from "@/services/markings";

export interface MarkingFieldRow {
  label: string;
  // Pre-formatted display string. "" means blank; the renderer decides
  // whether to show a "-" placeholder or hide the row.
  value: string;
  // RecordDetail honors this (rows where alwaysShow=true survive the
  // hasDisplayValue filter even when blank). ContributionDetail ignores it
  // and shows every row.
  alwaysShow: boolean;
}

export interface MarkingFieldInput {
  type: MarkingTypeValue | null;
  isManuscript: boolean;
  state: string;
  town: string;
  inscriptionTxt: string;
  // Already formatted according to DateSeen granularity.
  earliestSeen: string;
  latestSeen: string;
  // Pre-formatted comma-joined list of all observed dates (via
  // formatDatesSeenList), populated only when a marking has multiple distinct
  // dates; "" otherwise. Optional so the contribution path (a single
  // submission) need not supply it. (issue #25)
  datesSeen?: string;
  shapeName: string;
  // Pre-formatted via formatRateValue (e.g. "3 cents" or "").
  rateValFormatted: string;
  dateFmt: string;
  // Already mapped: empty string for the default "Normal" impression so
  // the row collapses on RecordDetail; otherwise the impression label.
  impression: string;
  isIrreg: boolean | null;
  colorName: string;
  letteringName: string;
  // Already formatted via dimensionsDisplay ("28x32 mm" or "28 mm diameter").
  dimensions: string;
  catalogTxt: string;
  code: string;
}

function typeLabel(t: MarkingTypeValue | null): string {
  if (t === "TOWNMARK") return "Townmark";
  if (t === "RATEMARK") return "Ratemark";
  if (t === "AUXMARK") return "Auxmark";
  return "";
}

function inscriptionLabel(t: MarkingTypeValue | null): string {
  if (t === "RATEMARK") return "Ratemark Text";
  if (t === "AUXMARK") return "Auxmark Text";
  return "Townmark Text";
}

// Mirrors RecordDetail.tsx details array (lines ~613-639). Manuscripts have
// no shape/lettering/dimensions by data model. Rate Value is always shown
// for Ratemarks, shown for Auxmarks only when populated, never for
// Townmarks. Catalog text row is editor-only.
export function buildMarkingFields(
  i: MarkingFieldInput,
  opts: { isStaff: boolean },
): MarkingFieldRow[] {
  const showPhysical = !i.isManuscript;
  const rows: MarkingFieldRow[] = [
    { label: "Type", value: typeLabel(i.type), alwaysShow: false },
    { label: "Manuscript", value: i.isManuscript ? "Yes" : "No", alwaysShow: false },
    { label: "State/Territory", value: i.state, alwaysShow: false },
    { label: "Town", value: i.town, alwaysShow: false },
    { label: inscriptionLabel(i.type), value: i.inscriptionTxt, alwaysShow: false },
    { label: "Earliest Seen", value: i.earliestSeen, alwaysShow: true },
    { label: "Latest Seen", value: i.latestSeen, alwaysShow: true },
    // Only populated (and only shown) when the marking has multiple distinct
    // dates; the helper returns "" otherwise so this row collapses. (issue #25)
    { label: "Dates Seen", value: i.datesSeen ?? "", alwaysShow: false },
  ];
  if (showPhysical) {
    rows.push({ label: "Shape", value: i.shapeName, alwaysShow: false });
  }
  if (i.type === "RATEMARK") {
    rows.push({ label: "Rate Value", value: i.rateValFormatted, alwaysShow: true });
  } else if (i.type === "AUXMARK") {
    rows.push({ label: "Rate Value", value: i.rateValFormatted, alwaysShow: false });
  }
  rows.push({ label: "Date Format", value: i.dateFmt, alwaysShow: false });
  rows.push({ label: "Impression", value: i.impression, alwaysShow: false });
  rows.push({ label: "Is Irregular", value: i.isIrreg === true ? "Yes" : "", alwaysShow: false });
  rows.push({ label: "Color", value: i.colorName, alwaysShow: false });
  if (showPhysical) {
    rows.push({ label: "Lettering", value: i.letteringName, alwaysShow: false });
    rows.push({ label: "Dimensions", value: i.dimensions, alwaysShow: false });
  }
  if (opts.isStaff) {
    rows.push({ label: "Catalog text", value: i.catalogTxt, alwaysShow: false });
    rows.push({ label: "Catalog code", value: i.code, alwaysShow: false });
  }
  return rows;
}

// Treat "", "-", and "unknown" as no value. Matches the inline helper that
// previously lived in RecordDetail.tsx.
export function hasDisplayValue(v: unknown): boolean {
  const s = String(v ?? "").trim();
  return s !== "" && s !== "-" && s.toLowerCase() !== "unknown";
}
