/**
 * Turn appointment events into terms of service, for display only (#125).
 *
 * The source catalogs record one row per *appointment*, never a term, so
 * PostmasterTenure has no end date and cannot get one: nobody wrote down when
 * these people stopped. What we can say is that a postmaster served until the
 * next thing that happened at that office, so each span's end is INFERRED from
 * the following event and is not asserted by any source.
 *
 * That inference is confined to this module and to the display layer on
 * purpose. It is deliberately not serialized by the API -- see
 * services/postmasters.ts -- because a derived end date sitting in a payload
 * reads like a recorded one, and every future consumer would inherit the guess.
 *
 * Two consequences worth keeping in mind when reading the output:
 *   - Spans are rendered as years only. A year-precision source and a
 *     day-precision one both yield "1793", so the span never claims precision
 *     the source lacks. The exact dates survive in `precise`.
 *   - 452 of 11,426 tenures have no usable date at all (the `Late` / `?`
 *     cohort, issue #102). They cannot be placed in a succession, so they are
 *     listed at the end without years rather than being ordered by guesswork
 *     or dropped. A tail row is the reader's signal that the succession above
 *     it is incomplete.
 */
import { formatDateSeen, yearFromCatalogDate } from "@/lib/catalogRecordDisplay";
import { tenureEventLabel, type PostmasterTenure } from "@/services/postmasters";

export type PostmasterSpanKind = "tenure" | "event" | "undated";

export interface PostmasterSpanRow {
  /** Stable React key. */
  key: string;
  kind: PostmasterSpanKind;
  /** The person's name, or the label for an office-level event. */
  label: string;
  /** "1793 – 1796" | "1831" | "1865 –" | "" for an undated row. */
  years: string;
  isOpenEnded: boolean;
  /** This is the person serving when the marking was struck. */
  isFocus: boolean;
  /** "office discontinued" | "date not recorded" | "" */
  note: string;
  /** Full-precision disclosure, for a title attribute and screen readers. */
  precise: string;
}

const EN_DASH = "–";

/**
 * `unknown` is the source shrugging: every such row is person-less and 14 of
 * the 17 are also undated, so a row on a card headed "Postmasters" would
 * assert nothing about a postmaster. They stay visible in full on the town
 * page, which is the exhaustive view.
 */
const SKIPPED_EVENTS = new Set(["unknown"]);

/**
 * An event that ends whoever is currently serving. Somebody demonstrably took
 * office, or the office closed, so letting the incumbent's span run through it
 * would assert a term the source contradicts.
 *
 * Two clauses, and the second one matters more than it looks:
 *
 *  - By event: an `appointment` or a `discontinued` always ends the standing
 *    term. A **person-less `reappointment` does NOT** -- it is the
 *    re-commissioning of the incumbent, and ending the term there would
 *    manufacture a gap the card cannot name anybody in. All 45 reappointments
 *    in the data today are person-less, so this is the case that fires.
 *
 *  - By name: ANY row carrying a name ends the standing term, including a
 *    reappointment. This is not a contradiction of the rule above, it is the
 *    other half of it: if a row names somebody, that person holds the office
 *    from that date, whatever the event is called.
 *
 * ⛔ Do not "simplify" this to `if (event === "reappointment") return false`.
 * A *named* reappointment would then fail to close the previous term while
 * still opening its own, leaving two open-ended spans -- which the corpus
 * invariant forbids. `a named reappointment ends the previous term` in the
 * tests locks this down. There are 0 named reappointments today; the ingest
 * could produce one.
 */
function terminatesIncumbent(tenure: PostmasterTenure, name: string): boolean {
  return (
    tenure.event === "appointment" ||
    tenure.event === "discontinued" ||
    name !== ""
  );
}

function hasUsableDate(value: string | null): boolean {
  return /^\d{4}/.test(String(value ?? "").trim());
}

/** "1793 – 1796", "1831" for a same-year term, "1865 –" while still open. */
function formatSpanYears(start: string | null, end: string | null): string {
  if (!start) return "";
  const from = yearFromCatalogDate(start);
  if (!end) return `${from} ${EN_DASH}`;
  const to = yearFromCatalogDate(end);
  return to === from ? from : `${from} ${EN_DASH} ${to}`;
}

interface WorkingRow extends PostmasterSpanRow {
  startDate: string | null;
  endDate: string | null;
  startGranularity: string;
  endGranularity: string;
}

function blankRow(over: Partial<WorkingRow>): WorkingRow {
  return {
    key: "",
    kind: "event",
    label: "",
    years: "",
    isOpenEnded: false,
    isFocus: false,
    note: "",
    precise: "",
    startDate: null,
    endDate: null,
    startGranularity: "",
    endGranularity: "",
    ...over,
  };
}

function preciseLabel(row: WorkingRow): string {
  if (row.kind === "undated") {
    return "The source did not record a date for this appointment.";
  }
  const appointed = formatDateSeen(row.startDate, row.startGranularity);
  if (row.kind === "event") return appointed;
  if (!row.endDate) {
    return `Appointed ${appointed}. No later record for this office.`;
  }
  const ended = formatDateSeen(row.endDate, row.endGranularity);
  return `Appointed ${appointed}. End inferred from the next record for this office, ${ended}.`;
}

/**
 * Build the display rows for one post office.
 *
 * `focusYear` is the year the marking was struck. The row whose term contains
 * it is flagged `isFocus` so the card can open centred on the postmaster who
 * was actually serving -- which is the only thing this card offers that the
 * town page does not.
 */
export function buildPostmasterSpans(
  tenures: PostmasterTenure[],
  focusYear?: number | null,
): PostmasterSpanRow[] {
  const dated: PostmasterTenure[] = [];
  const undated: PostmasterTenure[] = [];

  for (const tenure of tenures) {
    const name = tenure.postmasterName.trim();
    if (SKIPPED_EVENTS.has(tenure.event) && name === "") continue;
    if (hasUsableDate(tenure.dateAppointed)) {
      dated.push(tenure);
    } else if (name !== "") {
      // Named but unplaceable. Ordering it would be invention; dropping it
      // would erase a real postmaster.
      undated.push(tenure);
    }
    // Neither a name nor a date leaves nothing to render.
  }

  // The API already returns this order, but sorting here means the helper is
  // correct for any input and tests can pass rows in any order. ISO date
  // strings sort lexicographically, which is chronologically.
  dated.sort((a, b) => {
    const byDate = String(a.dateAppointed).localeCompare(String(b.dateAppointed));
    return byDate !== 0 ? byDate : a.id - b.id;
  });

  const seen = new Set<string>();
  const rows: WorkingRow[] = [];
  let openIndex: number | null = null;

  for (const tenure of dated) {
    // Dedupe is for the same *person* listed twice, so a person-less row falls
    // back to its own id and can never collapse against another one. Keying
    // them all on "" would silently merge two distinct office events that
    // happened to share a date.
    const identity = `${tenure.postmasterId ?? `#${tenure.id}`}|${tenure.dateAppointed}|${tenure.event}`;
    if (seen.has(identity)) continue;
    seen.add(identity);

    const name = tenure.postmasterName.trim();

    if (openIndex !== null && terminatesIncumbent(tenure, name)) {
      const open = rows[openIndex];
      open.endDate = tenure.dateAppointed;
      open.endGranularity = tenure.granularity;
      // Fold the closure into the row it closes rather than printing the same
      // year twice on adjacent lines.
      if (tenure.event === "discontinued") open.note = "office discontinued";
      openIndex = null;
      if (tenure.event === "discontinued") continue;
    }

    if (name !== "") {
      rows.push(
        blankRow({
          key: `tenure-${tenure.id}`,
          kind: "tenure",
          label: name,
          startDate: tenure.dateAppointed,
          startGranularity: tenure.granularity,
        }),
      );
      openIndex = rows.length - 1;
      continue;
    }

    const label =
      tenure.event === "appointment"
        ? "Appointed (name not recorded)"
        : tenureEventLabel(tenure.event);
    rows.push(
      blankRow({
        key: `event-${tenure.id}`,
        kind: "event",
        label,
        startDate: tenure.dateAppointed,
        startGranularity: tenure.granularity,
        endDate: tenure.dateAppointed,
        endGranularity: tenure.granularity,
      }),
    );
  }

  for (const tenure of undated.sort((a, b) => a.id - b.id)) {
    rows.push(
      blankRow({
        key: `undated-${tenure.id}`,
        kind: "undated",
        label: tenure.postmasterName.trim(),
        note: "date not recorded",
      }),
    );
  }

  for (const row of rows) {
    row.years = formatSpanYears(row.startDate, row.endDate);
    row.isOpenEnded = row.kind === "tenure" && row.endDate === null;
    row.precise = preciseLabel(row);
  }

  markFocus(rows, focusYear);

  return rows.map((row) => ({
    key: row.key,
    kind: row.kind,
    label: row.label,
    years: row.years,
    isOpenEnded: row.isOpenEnded,
    isFocus: row.isFocus,
    note: row.note,
    precise: row.precise,
  }));
}

/**
 * Flag the term containing `focusYear`. Containment is half-open -- a marking
 * struck in the year of a handover belongs to the incoming postmaster, not the
 * outgoing one -- so at most one row can ever match on its own merits.
 *
 * Taking the FIRST match is therefore not an ordering assumption, but the
 * `return` does make one visible: it relies on `rows` being chronological,
 * which the sweep above guarantees by construction. Undated rows cannot
 * interfere whatever order they land in -- they have no `startDate` and are
 * skipped below.
 */
function markFocus(rows: WorkingRow[], focusYear?: number | null): void {
  if (focusYear == null || !Number.isFinite(focusYear)) return;
  for (const row of rows) {
    if (row.kind !== "tenure" || !row.startDate) continue;
    const from = Number(yearFromCatalogDate(row.startDate));
    if (!Number.isFinite(from) || focusYear < from) continue;
    if (row.endDate) {
      const to = Number(yearFromCatalogDate(row.endDate));
      if (Number.isFinite(to) && focusYear >= to) continue;
    }
    row.isFocus = true;
    return;
  }
}
