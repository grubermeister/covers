/**
 * @jest-environment jsdom
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BulkReviewBar, CONFIRM_THRESHOLD } from "./BulkReviewBar";
import {
  bulkReviewContributions,
  listContributionIds,
} from "@/services/contributions";

jest.mock("@/services/contributions", () => ({
  __esModule: true,
  bulkReviewContributions: jest.fn(),
  listContributionIds: jest.fn(),
}));

const mockBulk = bulkReviewContributions as jest.Mock;
const mockIds = listContributionIds as jest.Mock;

// Issue #101. The two behaviours here that are easy to regress into something
// that looks fine and is wrong:
//
//   * "select all matching" must ask the SERVER, not extrapolate from the page
//     on screen. Deriving it client-side is #109's defect wearing a new hat,
//     and here it would approve rows the editor never saw.
//   * a large batch must be confirmed deliberately, because approval mints
//     permanent catalog codes and cannot be undone.

function setup(overrides = {}) {
  const onSelectionChange = jest.fn();
  const onCompleted = jest.fn();
  const props = {
    filters: { mode: "editor" as const, source: "vphc" as const },
    selectedIds: [1, 2, 3],
    onSelectionChange,
    matchCount: 2443,
    onCompleted,
    ...overrides,
  };
  render(<BulkReviewBar {...props} />);
  return { onSelectionChange, onCompleted };
}

beforeEach(() => {
  mockBulk.mockReset();
  mockIds.mockReset();
  mockBulk.mockResolvedValue({ succeeded: [1, 2, 3], failed: [] });
});

it("renders nothing until something is selected", () => {
  const { container } = render(
    <BulkReviewBar
      filters={{ mode: "editor" }}
      selectedIds={[]}
      onSelectionChange={jest.fn()}
      matchCount={10}
      onCompleted={jest.fn()}
    />,
  );

  expect(container.innerHTML).toBe("");
});

it("offers the full match set, distinct from the page selection", () => {
  setup();

  expect(screen.getByText("3 selected")).not.toBeNull();
  expect(
    screen.getByRole("button", { name: /select all 2443 matching/i }),
  ).not.toBeNull();
});

it("asks the server for the ids rather than deriving them", async () => {
  const ids = Array.from({ length: 2443 }, (_, i) => i + 1);
  mockIds.mockResolvedValue({ ids, count: ids.length });
  const { onSelectionChange } = setup();

  await userEvent.click(
    screen.getByRole("button", { name: /select all 2443 matching/i }),
  );

  await waitFor(() => expect(mockIds).toHaveBeenCalled());
  expect(mockIds).toHaveBeenCalledWith({ mode: "editor", source: "vphc" });
  expect(onSelectionChange).toHaveBeenCalledWith(ids);
});

it("runs a small batch straight away", async () => {
  setup();

  await userEvent.click(screen.getByRole("button", { name: "Approve" }));

  await waitFor(() => expect(mockBulk).toHaveBeenCalled());
  expect(mockBulk).toHaveBeenCalledWith("approve", [1, 2, 3], expect.anything());
});

it("requires a typed confirmation above the threshold", async () => {
  const big = Array.from({ length: CONFIRM_THRESHOLD }, (_, i) => i + 1);
  setup({ selectedIds: big, matchCount: CONFIRM_THRESHOLD });

  await userEvent.click(screen.getByRole("button", { name: "Approve" }));

  // Nothing has run yet -- the point of the gate.
  expect(mockBulk).not.toHaveBeenCalled();
  expect(
    screen.getByText(new RegExp(`Approve ${CONFIRM_THRESHOLD} submissions`, "i")),
  ).not.toBeNull();

  const confirmButton = screen.getByRole("button", {
    name: `Approve ${CONFIRM_THRESHOLD}`,
  });
  expect((confirmButton as HTMLButtonElement).disabled).toBe(true);

  await userEvent.type(
    screen.getByLabelText(`Type ${CONFIRM_THRESHOLD} to confirm`),
    String(CONFIRM_THRESHOLD),
  );
  expect((confirmButton as HTMLButtonElement).disabled).toBe(false);
  await userEvent.click(confirmButton);

  await waitFor(() => expect(mockBulk).toHaveBeenCalled());
});

it("lists failures individually and can retry only those", async () => {
  mockBulk.mockResolvedValueOnce({
    succeeded: [1],
    failed: [
      { id: 2, reason: "not pending" },
      { id: 3, reason: "no permission" },
    ],
  });
  setup();

  await userEvent.click(screen.getByRole("button", { name: "Approve" }));

  await screen.findByText(/1 approved, 2 failed/i);
  // Listed, not counted: a bare "2 failed" is unactionable across 2,443 rows.
  expect(screen.getByText(/not pending/)).not.toBeNull();
  expect(screen.getByText(/no permission/)).not.toBeNull();

  mockBulk.mockResolvedValueOnce({ succeeded: [2, 3], failed: [] });
  await userEvent.click(screen.getByRole("button", { name: /retry 2 failed/i }));

  await waitFor(() => expect(mockBulk).toHaveBeenCalledTimes(2));
  expect(mockBulk).toHaveBeenLastCalledWith("approve", [2, 3], expect.anything());
});

it("reports completion so the queue can refetch", async () => {
  const { onCompleted } = setup();

  await userEvent.click(screen.getByRole("button", { name: "Approve" }));

  await waitFor(() => expect(onCompleted).toHaveBeenCalled());
});

it("surfaces a failure to load the match set without changing anything", async () => {
  mockIds.mockRejectedValue(new Error("boom"));
  setup();

  await userEvent.click(
    screen.getByRole("button", { name: /select all 2443 matching/i }),
  );

  expect(await screen.findByText(/nothing was changed/i)).not.toBeNull();
  expect(mockBulk).not.toHaveBeenCalled();
});
