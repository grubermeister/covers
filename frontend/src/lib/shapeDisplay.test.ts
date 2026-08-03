import { isTrueCircleShapeName, shapeCodeFromName } from "./shapeDisplay";

describe("shape display helpers", () => {
  it("extracts the shape code prefix", () => {
    expect(shapeCodeFromName("ARC - Arc or Semi-circle")).toBe("ARC");
    expect(shapeCodeFromName("C - Circle")).toBe("C");
  });

  it("only treats true circle-family codes as diameter shapes", () => {
    expect(isTrueCircleShapeName("C - Circle")).toBe(true);
    expect(isTrueCircleShapeName("Circle")).toBe(true);
    expect(isTrueCircleShapeName("CDS")).toBe(true);
    expect(isTrueCircleShapeName("DC - Double Circle")).toBe(true);
    expect(isTrueCircleShapeName("Double Circle")).toBe(true);
    expect(isTrueCircleShapeName("DLC - Double Line Circle")).toBe(true);
    expect(isTrueCircleShapeName("DLDC - Double Line Double Circle")).toBe(true);

    expect(isTrueCircleShapeName("ARC - Arc or Semi-circle")).toBe(false);
    expect(isTrueCircleShapeName("O - Oval")).toBe(false);
  });
});
