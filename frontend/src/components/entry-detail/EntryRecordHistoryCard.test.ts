import { formatDiffValue } from "./historyDiffFormatting";

describe("formatDiffValue", () => {
  it("formats citation snapshot rows as readable text", () => {
    expect(
      formatDiffValue([
        { reference_work_id: 12, citation_detail: "p. 5" },
        { reference_work_id: 18, citation_detail: "" },
      ]),
    ).toBe("Reference work #12: p. 5; Reference work #18");
  });

  it("formats generic object rows without JavaScript object coercion", () => {
    const value = formatDiffValue([
      { original_filename: "front.jpg", display_order: 0 },
    ]);

    expect(value).toBe("original filename: front.jpg, display order: 0");
    expect(value).not.toContain("[object Object]");
  });

  it("caps long arrays in the history card summary", () => {
    expect(formatDiffValue(["one", "two", "three", "four"])).toBe(
      "one; two; three; +1 more",
    );
  });
});
