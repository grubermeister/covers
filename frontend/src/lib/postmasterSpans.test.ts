import { buildPostmasterSpans } from "./postmasterSpans";
import type { PostmasterTenure } from "@/services/postmasters";

// Defaults are a real Abingdon row, matching services/postmasters.test.ts.
const tenure = (over: Partial<PostmasterTenure>): PostmasterTenure => ({
  id: 1,
  postOfficeId: 1,
  postOfficeName: "Abingdon",
  postmasterId: 1,
  postmasterName: "Gerrard T. Conn",
  event: "appointment",
  dateAppointed: "1793-04-25",
  granularity: "DAY",
  sourceRef: "T3:r15904",
  ...over,
});

const conn = tenure({});
const simpson = tenure({
  id: 2,
  postmasterId: 2,
  postmasterName: "George Simpson",
  dateAppointed: "1796-07-01",
});
const mcCormack = tenure({
  id: 3,
  postmasterId: 3,
  postmasterName: "John W. McCormack",
  dateAppointed: "1800-03-28",
});

describe("buildPostmasterSpans", () => {
  it("ends each term where the next appointment begins", () => {
    const rows = buildPostmasterSpans([conn, simpson]);
    expect(rows.map((r) => [r.label, r.years])).toEqual([
      ["Gerrard T. Conn", "1793 – 1796"],
      ["George Simpson", "1796 –"],
    ]);
  });

  // Nobody recorded when the last postmaster left, so the span stays open
  // rather than collapsing to the day they started.
  it("leaves a lone appointment open-ended, not zero-length", () => {
    const [row] = buildPostmasterSpans([conn]);
    expect(row.years).toBe("1793 –");
    expect(row.isOpenEnded).toBe(true);
  });

  it("renders a term that began and ended in one year as a single year", () => {
    const rows = buildPostmasterSpans([
      tenure({ id: 1, dateAppointed: "1831-02-01" }),
      tenure({
        id: 2,
        postmasterId: 2,
        postmasterName: "James Gibson",
        dateAppointed: "1831-12-19",
      }),
    ]);
    expect(rows[0].years).toBe("1831");
  });

  // A year-precision source and a day-precision one must look identical, or
  // the card claims a precision the book never had.
  it("shows the same years whatever precision the source stated", () => {
    const dayPrecise = buildPostmasterSpans([conn, simpson])[0];
    const yearOnly = buildPostmasterSpans([
      tenure({ granularity: "YEAR" }),
      simpson,
    ])[0];
    expect(yearOnly.years).toBe(dayPrecise.years);
    expect(dayPrecise.precise).toContain("04/25/1793");
    expect(yearOnly.precise).toContain("1793");
    expect(yearOnly.precise).not.toContain("04/25");
  });

  it("says plainly that the end of a term is inferred, not recorded", () => {
    const [closed, open] = buildPostmasterSpans([conn, simpson]);
    expect(closed.precise).toContain("End inferred from the next record");
    expect(open.precise).toContain("No later record for this office");
  });

  it("returns nothing for an office with no tenures", () => {
    expect(buildPostmasterSpans([])).toEqual([]);
  });
});

describe("buildPostmasterSpans — office-level events", () => {
  const discontinued = tenure({
    id: 9,
    postmasterId: null,
    postmasterName: "",
    event: "discontinued",
    dateAppointed: "1796-07-01",
  });

  it("closes the standing term when the office is discontinued, without a second row", () => {
    const rows = buildPostmasterSpans([conn, discontinued]);
    expect(rows).toHaveLength(1);
    expect(rows[0].years).toBe("1793 – 1796");
    expect(rows[0].note).toBe("office discontinued");
  });

  it("gives a discontinuation its own row when no term is standing", () => {
    const rows = buildPostmasterSpans([discontinued]);
    expect(rows.map((r) => [r.kind, r.label])).toEqual([
      ["event", "Office discontinued"],
    ]);
  });

  it("starts a fresh term after a discontinuation rather than continuing one", () => {
    const rows = buildPostmasterSpans([conn, discontinued, mcCormack]);
    expect(rows.map((r) => [r.label, r.years])).toEqual([
      ["Gerrard T. Conn", "1793 – 1796"],
      ["John W. McCormack", "1800 –"],
    ]);
  });

  // A reappointment re-commissions the incumbent. Ending the term there would
  // invent a gap, and all 45 such rows are person-less so there is nobody to
  // name in it.
  it("does not end the incumbent's term at a reappointment", () => {
    const rows = buildPostmasterSpans([
      conn,
      tenure({
        id: 9,
        postmasterId: null,
        postmasterName: "",
        event: "reappointment",
        dateAppointed: "1795-01-01",
      }),
      simpson,
    ]);
    const conns = rows.filter((r) => r.label === "Gerrard T. Conn");
    expect(conns).toHaveLength(1);
    expect(conns[0].years).toBe("1793 – 1796");
    expect(rows.map((r) => r.label)).toContain("Reappointed");
  });

  // Somebody demonstrably took office, so the previous term cannot run on.
  it("ends the incumbent's term at an appointment whose name was lost", () => {
    const rows = buildPostmasterSpans([
      conn,
      tenure({
        id: 9,
        postmasterId: null,
        postmasterName: "",
        dateAppointed: "1796-07-01",
      }),
    ]);
    expect(rows[0].years).toBe("1793 – 1796");
    expect(rows[1].label).toBe("Appointed (name not recorded)");
  });

  it("drops `unknown` events, which assert nothing about a postmaster", () => {
    const rows = buildPostmasterSpans([
      conn,
      tenure({
        id: 9,
        postmasterId: null,
        postmasterName: "",
        event: "unknown",
        dateAppointed: "1795-01-01",
      }),
    ]);
    expect(rows).toHaveLength(1);
  });
});

describe("buildPostmasterSpans — the shapes the real data actually has", () => {
  // Abingdon: the same man served twice, with someone else in between.
  it("keeps both terms when a postmaster returns to the same office", () => {
    const rows = buildPostmasterSpans([
      tenure({ id: 1, postmasterId: 7, postmasterName: "James Gibson", dateAppointed: "1831-12-19" }),
      tenure({ id: 2, postmasterId: 8, postmasterName: "Robert R. Preston", dateAppointed: "1836-06-11" }),
      tenure({ id: 3, postmasterId: 7, postmasterName: "James Gibson", dateAppointed: "1842-01-04" }),
    ]);
    expect(rows.map((r) => [r.label, r.years])).toEqual([
      ["James Gibson", "1831 – 1836"],
      ["Robert R. Preston", "1836 – 1842"],
      ["James Gibson", "1842 –"],
    ]);
  });

  // The 452 `Late` / `?` rows. Ordering one would be invention; dropping it
  // would erase a named postmaster.
  it("lists an undated postmaster at the end without splitting the terms above", () => {
    const rows = buildPostmasterSpans([
      conn,
      simpson,
      tenure({
        id: 9,
        postmasterId: 9,
        postmasterName: "Josiah Kelly",
        dateAppointed: null,
        granularity: "",
      }),
    ]);
    expect(rows.map((r) => [r.label, r.years])).toEqual([
      ["Gerrard T. Conn", "1793 – 1796"],
      ["George Simpson", "1796 –"],
      ["Josiah Kelly", ""],
    ]);
    expect(rows[2].note).toBe("date not recorded");
  });

  it("drops rows carrying neither a name nor a date", () => {
    const rows = buildPostmasterSpans([
      conn,
      tenure({
        id: 9,
        postmasterId: null,
        postmasterName: "",
        event: "discontinued",
        dateAppointed: null,
      }),
    ]);
    expect(rows).toHaveLength(1);
  });

  it("does not depend on the order the API returned", () => {
    const ordered = buildPostmasterSpans([conn, simpson, mcCormack]);
    const shuffled = buildPostmasterSpans([mcCormack, conn, simpson]);
    expect(shuffled).toEqual(ordered);
  });

  it("collapses a tenure the source listed twice", () => {
    const rows = buildPostmasterSpans([conn, tenure({ id: 99 }), simpson]);
    expect(rows.map((r) => r.label)).toEqual([
      "Gerrard T. Conn",
      "George Simpson",
    ]);
  });

  it("gives every row a distinct key", () => {
    const rows = buildPostmasterSpans([conn, simpson, mcCormack]);
    expect(new Set(rows.map((r) => r.key)).size).toBe(rows.length);
  });
});

describe("buildPostmasterSpans — the term the marking was struck in", () => {
  const abingdon = [conn, simpson, mcCormack];

  it("flags the postmaster serving when the marking was struck", () => {
    const rows = buildPostmasterSpans(abingdon, 1798);
    expect(rows.filter((r) => r.isFocus).map((r) => r.label)).toEqual([
      "George Simpson",
    ]);
  });

  // A marking struck in the year of a handover belongs to the incoming
  // postmaster, so containment is half-open and only one row can ever match.
  it("gives a handover year to the incoming postmaster", () => {
    const rows = buildPostmasterSpans(abingdon, 1796);
    expect(rows.filter((r) => r.isFocus).map((r) => r.label)).toEqual([
      "George Simpson",
    ]);
  });

  it("flags the open-ended term for any year after it began", () => {
    const rows = buildPostmasterSpans(abingdon, 1850);
    expect(rows.filter((r) => r.isFocus).map((r) => r.label)).toEqual([
      "John W. McCormack",
    ]);
  });

  it("flags nothing when the marking predates every appointment", () => {
    expect(buildPostmasterSpans(abingdon, 1780).some((r) => r.isFocus)).toBe(false);
  });

  it("flags nothing when no year is given", () => {
    expect(buildPostmasterSpans(abingdon).some((r) => r.isFocus)).toBe(false);
    expect(buildPostmasterSpans(abingdon, null).some((r) => r.isFocus)).toBe(false);
  });
});
