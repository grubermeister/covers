import { tenureDateLabel, tenureEventLabel, type PostmasterTenure } from "./postmasters";

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

describe("tenureDateLabel", () => {
  it("shows a full date when the source gave one", () => {
    expect(tenureDateLabel(tenure({}))).toBe("1793-04-25");
  });

  // The source often records only a year. Showing 1 January would assert a
  // precision nobody has, which is why granularity is stored at all.
  it("shows only the year when that is all the source stated", () => {
    expect(tenureDateLabel(tenure({ granularity: "YEAR" }))).toBe("1793");
  });

  it("shows year and month for a month-precision record", () => {
    expect(tenureDateLabel(tenure({ granularity: "MONTH" }))).toBe("1793-04");
  });

  it("says so plainly when there is no date at all", () => {
    expect(tenureDateLabel(tenure({ dateAppointed: null }))).toBe("date unknown");
  });
});

describe("tenureEventLabel", () => {
  it("reads as English, not as a database value", () => {
    expect(tenureEventLabel("discontinued")).toBe("Office discontinued");
    expect(tenureEventLabel("reappointment")).toBe("Reappointed");
  });

  it("falls back to the raw value rather than hiding an unknown event", () => {
    expect(tenureEventLabel("something_new")).toBe("something_new");
  });
});
