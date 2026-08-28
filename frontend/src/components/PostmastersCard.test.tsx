/**
 * @jest-environment jsdom
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { PostmastersCard } from "./PostmastersCard";
import type { PostmasterTenure } from "@/services/postmasters";

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

// The real Abingdon succession, which is 14 appointments long.
const ABINGDON: PostmasterTenure[] = [
  ["William Coon", "1792-08-20"],
  ["Gerrard T. Conn", "1793-04-25"],
  ["George Simpson", "1796-07-01"],
  ["John W. McCormack", "1800-03-28"],
  ["John McClellan", "1813-06-16"],
  ["Augustus Oury", "1820-08-28"],
  ["James Gibson", "1831-12-19"],
  ["Robert R. Preston", "1836-06-11"],
  ["James Gibson", "1842-01-04"],
  ["George R. Barr", "1849-07-26"],
  ["Leonard Bangh", "1853-05-12"],
  ["Henry W. Baker", "1858-12-18"],
  ["George Sanders", "1861-03-27"],
  ["William G. Sanders", "1865-09-06"],
].map(([postmasterName, dateAppointed], i) =>
  tenure({ id: i + 1, postmasterId: i + 1, postmasterName, dateAppointed }),
);

function renderCard(props: Partial<Parameters<typeof PostmastersCard>[0]> = {}) {
  return render(
    <MemoryRouter>
      <PostmastersCard tenures={ABINGDON} postOfficeId={1} {...props} />
    </MemoryRouter>,
  );
}

describe("PostmastersCard", () => {
  // Every marking outside Virginia and West Virginia takes this path, so an
  // empty shell would appear on the whole rest of the catalogue.
  it("renders nothing at all for an office with no postmasters", () => {
    const { container } = renderCard({ tenures: [] });
    expect(container.firstChild).toBeNull();
  });

  it("shows a postmaster with the years they served", () => {
    renderCard();
    expect(screen.queryByText("Gerrard T. Conn")).not.toBeNull();
    expect(screen.queryByText(/1793 – 1796/)).not.toBeNull();
  });

  it("counts the people, and shows only a window of them until asked", () => {
    renderCard();
    expect(screen.queryByText("Postmasters (14)")).not.toBeNull();
    expect(screen.queryByText("Leonard Bangh")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show 9 more" }));
    expect(screen.queryByText("Leonard Bangh")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    expect(screen.queryByText("Leonard Bangh")).toBeNull();
  });

  // The heading counts people. A dateless postmaster is still a postmaster;
  // the office being discontinued is nobody.
  it("counts people, not entries, in the heading", () => {
    renderCard({
      tenures: [
        tenure({ id: 1, postmasterId: 1, postmasterName: "Gerrard T. Conn" }),
        tenure({
          id: 2,
          postmasterId: null,
          postmasterName: "",
          event: "discontinued",
          dateAppointed: "1796-07-01",
        }),
        tenure({
          id: 3,
          postmasterId: 3,
          postmasterName: "Josiah Kelly",
          dateAppointed: null,
          granularity: "",
        }),
      ],
    });
    // Conn + Kelly are people; the discontinuation folds into Conn's term.
    expect(screen.queryByText("Postmasters (2)")).not.toBeNull();
  });

  // The whole reason this card exists rather than a link to the town page.
  it("opens on the postmaster serving when the marking was struck", () => {
    renderCard({ focusYear: 1839 });
    expect(screen.queryByText("Robert R. Preston")).not.toBeNull();
    expect(screen.queryByText(/5 earlier postmasters/)).not.toBeNull();
    expect(screen.queryByText(/4 later postmasters/)).not.toBeNull();
    // The 1793 term is outside the window, so it is not on screen yet.
    expect(screen.queryByText("Gerrard T. Conn")).toBeNull();
  });

  it("falls back to the earliest terms when the marking has no date", () => {
    renderCard();
    expect(screen.queryByText("William Coon")).not.toBeNull();
    expect(screen.queryByText(/earlier postmaster/)).toBeNull();
  });

  // The inference is the one thing a reader could mistake for a recorded fact.
  it("always says the end dates are inferred, and links to the full record", () => {
    renderCard();
    expect(screen.queryByText(/inferred from the next record/)).not.toBeNull();
    const link = screen.getByRole("link", {
      name: "Full postmaster record for this town",
    });
    expect(link.getAttribute("href")).toBe("/post-office/1");
  });
});
