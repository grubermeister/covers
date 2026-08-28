import {
  getTenuresForPostOffice,
  tenureDateLabel,
  tenureEventLabel,
  type PostmasterTenure,
} from "./postmasters";
import apiClient from "@/lib/api";

jest.mock("@/lib/api", () => ({
  __esModule: true,
  default: { get: jest.fn() },
  ensureCsrfToken: jest.fn(),
}));

const mockedGet = apiClient.get as jest.Mock;

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

describe("getTenuresForPostOffice", () => {
  beforeEach(() => mockedGet.mockReset());

  const page = (results: unknown[], next: string | null = null) => ({
    data: { count: results.length, next, previous: null, results },
  });

  // The API defaults to 10 per page and this loop awaits each page in turn, so
  // a 40-appointment office used to cost four serial round trips.
  it("asks for a page big enough to hold any office in one request", async () => {
    mockedGet.mockResolvedValueOnce(page([]));
    await getTenuresForPostOffice(42);
    expect(mockedGet).toHaveBeenCalledTimes(1);
    expect(mockedGet.mock.calls[0][0]).toContain("page_size=100");
    expect(mockedGet.mock.calls[0][0]).toContain("post_office=42");
  });

  it("still follows the next link when the API sends one", async () => {
    mockedGet
      .mockResolvedValueOnce(
        page(
          [{ tenure_id: 1, post_office: 42, postmaster: 1, postmaster_name: "A", event: "appointment", date_appointed: "1793-04-25" }],
          "/postmaster-tenures/?post_office=42&page=2",
        ),
      )
      .mockResolvedValueOnce(
        page([{ tenure_id: 2, post_office: 42, postmaster: 2, postmaster_name: "B", event: "appointment", date_appointed: "1796-07-01" }]),
      );
    const rows = await getTenuresForPostOffice(42);
    expect(mockedGet).toHaveBeenCalledTimes(2);
    expect(rows.map((r) => r.postmasterName)).toEqual(["A", "B"]);
  });
});
