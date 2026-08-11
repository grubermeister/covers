/**
 * Issue #22 -- state editor "reviewed/confirmed" flag.
 *
 * Covers the two pure/service seams the UI relies on: the API mapper carries
 * `is_reviewed` through to the camelCase record, and the editor-write helper
 * reports success/failure without throwing.
 */
import { mapApiMarkingToRecord, updateMarkingReviewed } from "./markings";
import apiClient from "@/lib/api";

jest.mock("@/lib/api", () => ({
  __esModule: true,
  default: { patch: jest.fn() },
  ensureCsrfToken: jest.fn(),
}));

const mockedPatch = apiClient.patch as jest.Mock;

describe("markings reviewed flag", () => {
  beforeEach(() => {
    mockedPatch.mockReset();
  });

  it("maps is_reviewed onto the record", () => {
    expect(mapApiMarkingToRecord({ id: 1, type: "TOWNMARK", is_reviewed: true }).isReviewed).toBe(true);
    expect(mapApiMarkingToRecord({ id: 2, type: "TOWNMARK", is_reviewed: false }).isReviewed).toBe(false);
    // Absent flag defaults to false (older payloads / list rows).
    expect(mapApiMarkingToRecord({ id: 3, type: "TOWNMARK" }).isReviewed).toBe(false);
  });

  it("PATCHes the marking and returns true on success", async () => {
    mockedPatch.mockResolvedValueOnce({ data: {} });
    const ok = await updateMarkingReviewed(42, true);
    expect(ok).toBe(true);
    expect(mockedPatch).toHaveBeenCalledWith("/markings/42/", { is_reviewed: true });
  });

  it("returns false when the write is rejected", async () => {
    mockedPatch.mockRejectedValueOnce(new Error("403"));
    const ok = await updateMarkingReviewed(42, true);
    expect(ok).toBe(false);
  });
});
