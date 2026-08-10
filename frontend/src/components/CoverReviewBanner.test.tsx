/**
 * @jest-environment jsdom
 */
import { render, screen } from "@testing-library/react";

import { CoverReviewBanner } from "./CoverReviewBanner";

describe("CoverReviewBanner", () => {
  it("renders nothing for a submission that has not been reviewed", () => {
    const { container } = render(<CoverReviewBanner status="draft" notes={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("shows the editor's comment on a returned submission", () => {
    render(
      <CoverReviewBanner
        status="needs_revision"
        notes="Please add the year and a clearer scan of the backstamp."
      />,
    );
    expect(screen.getByText(/editor requested changes/i)).toBeTruthy();
    expect(screen.getByText(/clearer scan of the backstamp/i)).toBeTruthy();
  });

  it("still says what to do when the editor left no comment", () => {
    render(<CoverReviewBanner status="needs_revision" notes="   " />);
    expect(screen.getByText(/editor requested changes/i)).toBeTruthy();
    expect(screen.getByText(/update the cover below/i)).toBeTruthy();
  });

  it("tells a pending submitter their cover is not public yet", () => {
    render(<CoverReviewBanner status="pending" notes={null} />);
    expect(screen.getByText(/pending editor review/i)).toBeTruthy();
  });

  it("accepts the status casing the API actually returns", () => {
    render(<CoverReviewBanner status="NEEDS_REVISION" notes="Add a date." />);
    expect(screen.getByText(/editor requested changes/i)).toBeTruthy();
  });
});
