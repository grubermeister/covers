/**
 * @jest-environment jsdom
 */
import { render } from "@testing-library/react";
import type { CatalogFieldValues } from "@/lib/catalogRecordDisplay";
import { CatalogRecordFields } from "./CatalogRecordFields";

function catalogRow(
  overrides: Partial<CatalogFieldValues> = {},
): CatalogFieldValues {
  return {
    type: "Townmark",
    town: "Aquila",
    state: "Virginia",
    regionAbbrev: "VA",
    manuscript: "No",
    desc: "-",
    markingTextLines: [],
    markingTextSingle: "AQUILA VA",
    shape: "Circle",
    lettering: "Roman",
    impression: "Normal",
    irregular: "No",
    dimensions: "25 mm diameter",
    color: "Black",
    rateValue: "5 cents",
    earliestSeen: "1811",
    latestSeen: "1855",
    ...overrides,
  };
}

function renderedLabels(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(".text-muted-foreground")).map(
    (node) => (node.textContent ?? "").replace(/:$/, ""),
  );
}

describe("CatalogRecordFields search list fields", () => {
  it("shows the requested Townmark fields in order", () => {
    const { container } = render(
      <CatalogRecordFields row={catalogRow()} variant="list" />,
    );

    expect(renderedLabels(container)).toEqual([
      "Type",
      "Manuscript",
      "Shape",
      "Lettering Style",
      "Dimensions",
      "Color",
      "Earliest Seen",
      "Latest Seen",
    ]);
  });

  it("uses Impression when an Auxmark has no lettering style", () => {
    const { container } = render(
      <CatalogRecordFields
        row={catalogRow({
          type: "Auxmark",
          lettering: "-",
          impression: "Stencil",
        })}
        variant="list"
      />,
    );

    expect(renderedLabels(container)).toEqual([
      "Type",
      "Manuscript",
      "Shape",
      "Impression",
      "Dimensions",
      "Color",
      "Earliest Seen",
      "Latest Seen",
    ]);
  });

  it("uses Irregular when lettering style and impression are missing", () => {
    const { container } = render(
      <CatalogRecordFields
        row={catalogRow({
          lettering: "-",
          impression: "-",
          irregular: "Yes",
        })}
        variant="list"
      />,
    );

    expect(renderedLabels(container)).toEqual([
      "Type",
      "Manuscript",
      "Shape",
      "Irregular",
      "Dimensions",
      "Color",
      "Earliest Seen",
      "Latest Seen",
    ]);
  });

  it("shows the requested Ratemark fields in order", () => {
    const { container } = render(
      <CatalogRecordFields
        row={catalogRow({ type: "Ratemark" })}
        variant="list"
      />,
    );

    expect(renderedLabels(container)).toEqual([
      "Type",
      "Manuscript",
      "Shape",
      "Rate Value",
      "Dimensions",
      "Color",
      "Earliest Seen",
      "Latest Seen",
    ]);
  });
});

describe("CatalogRecordFields search gallery fields", () => {
  it("shows the requested Townmark and Auxmark fields", () => {
    for (const type of ["Townmark", "Auxmark"]) {
      const { container, unmount } = render(
        <CatalogRecordFields
          row={catalogRow({ type })}
          variant="gallery"
        />,
      );

      expect(renderedLabels(container)).toEqual([
        "Type",
        "Manuscript",
        "Dimensions",
        "Color",
        "Earliest Seen",
        "Latest Seen",
      ]);
      unmount();
    }
  });

  it("shows the requested Ratemark fields without Color", () => {
    const { container } = render(
      <CatalogRecordFields
        row={catalogRow({ type: "Ratemark" })}
        variant="gallery"
      />,
    );

    expect(renderedLabels(container)).toEqual([
      "Type",
      "Manuscript",
      "Dimensions",
      "Rate Value",
      "Earliest Seen",
      "Latest Seen",
    ]);
  });
});
