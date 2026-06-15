import { formatDateSeen } from "./catalogRecordDisplay";

describe("formatDateSeen", () => {
  it("formats DAY granularity as MM/DD/YYYY", () => {
    expect(formatDateSeen("1865-08-14", "DAY")).toBe("08/14/1865");
  });

  it("formats MONTH granularity as MMM, YYYY", () => {
    expect(formatDateSeen("1865-08-01", "MONTH")).toBe("AUG, 1865");
  });

  it("formats YEAR granularity as YYYY", () => {
    expect(formatDateSeen("1865-01-01", "YEAR")).toBe("1865");
  });
});
