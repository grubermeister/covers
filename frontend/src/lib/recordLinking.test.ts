import { parseCoverIdInput, parseMarkingIdInput } from "./recordLinking";

describe("parseCoverIdInput", () => {
  it("accepts bare ids and C- prefixed codes", () => {
    expect(parseCoverIdInput("42")).toBe(42);
    expect(parseCoverIdInput("C-42")).toBe(42);
    expect(parseCoverIdInput("c42")).toBe(42);
    expect(parseCoverIdInput(" 42 ")).toBe(42);
  });

  it("rejects non-linkable input", () => {
    expect(parseCoverIdInput("")).toBeNull();
    expect(parseCoverIdInput("0")).toBeNull();
    expect(parseCoverIdInput("-3")).toBeNull();
    expect(parseCoverIdInput("cover")).toBeNull();
  });
});

describe("parseMarkingIdInput", () => {
  it("accepts bare ids and api- route ids", () => {
    expect(parseMarkingIdInput("12")).toBe(12);
    expect(parseMarkingIdInput("api-12")).toBe(12);
  });

  it("rejects non-linkable input", () => {
    expect(parseMarkingIdInput("")).toBeNull();
    expect(parseMarkingIdInput("api-")).toBeNull();
    expect(parseMarkingIdInput("marking")).toBeNull();
  });
});
