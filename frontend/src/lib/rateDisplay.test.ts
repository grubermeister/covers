import { formatRateValue } from "./rateDisplay";

describe("formatRateValue", () => {
  it("formats stored cent amounts with the cent glyph", () => {
    expect(formatRateValue("3.00")).toBe("3¢");
    expect(formatRateValue("3")).toBe("3¢");
  });

  it("preserves fractional cents", () => {
    expect(formatRateValue("3.50")).toBe("3.5¢");
    expect(formatRateValue("3.55")).toBe("3.55¢");
  });

  it("returns an empty string for missing or invalid values", () => {
    expect(formatRateValue(null)).toBe("");
    expect(formatRateValue("abc")).toBe("");
  });
});
