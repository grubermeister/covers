/**
 * @jest-environment jsdom
 */
import { render, screen } from "@testing-library/react";
import { VphcProvenanceCard } from "./VphcProvenanceCard";
import { readVphcProvenance } from "@/lib/vphcProvenance";

// The exact blob apply_vphc_ledger writes, taken from a real pending row.
const REAL_BLOB = {
  vphc: {
    src: "T1:r8",
    flags: ["date_low_confidence", "county_repaired"],
    state: "VA",
    county: "WASHINGTON",
    cancel_no: "5",
    vphc_code: "VPHC-VA-ABINGDON-5",
    rules_version: 2,
    why_unmatched: "ambiguous",
  },
};

function renderReal() {
  return render(<VphcProvenanceCard provenance={readVphcProvenance(REAL_BLOB)!} />);
}

describe("VphcProvenanceCard", () => {
  it("shows the source coordinates a reviewer needs to find the row", () => {
    renderReal();

    expect(screen.queryByText("VPHC-VA-ABINGDON-5")).not.toBeNull();
    expect(screen.queryByText("WASHINGTON")).not.toBeNull();
    expect(screen.queryByText("T1:r8")).not.toBeNull();
    expect(screen.queryByText("5")).not.toBeNull();
  });

  it("explains why the marking was catalogued as new instead of matched", () => {
    const { container } = renderReal();

    expect(container.textContent).toContain("Matched more than one existing record");
  });

  it("lists each uncertainty flag with its explanation", () => {
    const { container } = renderReal();

    expect(screen.queryByText("Date low confidence")).not.toBeNull();
    expect(container.textContent).toContain("run was too short to corroborate the century");
    expect(screen.queryByText("County repaired")).not.toBeNull();
    expect(container.textContent).toContain("repaired by fuzzy match");
  });

  // 778 of the 2,062 ingested rows carry no flags. Those should read as clean,
  // not show an empty "Needs checking" heading.
  it("omits the needs-checking block when nothing is flagged", () => {
    const provenance = readVphcProvenance({
      vphc: { vphc_code: "VPHC-VA-ABINGDON-5", flags: [], why_unmatched: "" },
    })!;
    render(<VphcProvenanceCard provenance={provenance} />);

    expect(screen.queryByText("Needs checking")).toBeNull();
    expect(screen.queryByText("VPHC-VA-ABINGDON-5")).not.toBeNull();
  });

  it("renders nothing when the blob is empty", () => {
    const { container } = render(
      <VphcProvenanceCard provenance={readVphcProvenance({ vphc: {} })!} />,
    );

    expect(container.innerHTML).toBe("");
  });
});
