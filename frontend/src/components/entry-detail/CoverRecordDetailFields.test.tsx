/**
 * @jest-environment jsdom
 */
import { render, screen } from "@testing-library/react";
import { CoverRecordDetailFields } from "./CoverRecordDetailFields";

const base = {
  type: "Folded Cover",
  date: "1850",
  institutionallyOwned: "No",
  backstamp: "Yes",
};

describe("CoverRecordDetailFields — submitter row", () => {
  it("shows 'Submitted by' with the name when the submitter opted in", () => {
    render(<CoverRecordDetailFields {...base} submittedBy="Jane Smith" />);
    expect(screen.getByText("Submitted by:")).toBeTruthy();
    expect(screen.getByText("Jane Smith")).toBeTruthy();
  });

  it("omits the row entirely when there is no submitter name (opted out/absent)", () => {
    render(<CoverRecordDetailFields {...base} />);
    expect(screen.queryByText("Submitted by:")).toBeNull();
  });
});
