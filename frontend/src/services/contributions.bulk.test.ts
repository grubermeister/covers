import apiClient from "@/lib/api";
import {
  BULK_REVIEW_BATCH_SIZE,
  bulkReviewContributions,
  listContributionIds,
} from "./contributions";

jest.mock("@/lib/api", () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() },
  ensureCsrfToken: jest.fn(),
}));

const mockClient = apiClient as unknown as {
  get: jest.Mock;
  post: jest.Mock;
};

// Issue #101. The batch runner is what turns a 2,443-row queue into ~98
// requests. What matters is not that it loops, but that it never loses a row:
// every id an editor selected has to come back either succeeded or failed with
// a reason, including when the network drops a whole slice. "1,150 of 2,443"
// with no account of the rest is the outcome to prevent.

beforeEach(() => {
  mockClient.get.mockReset();
  mockClient.post.mockReset();
});

function okResponse(ids: number[], failed: { id: number; reason: string }[] = []) {
  return { data: { approved: ids, failed } };
}

describe("bulkReviewContributions", () => {
  it("splits into batches of the documented size", async () => {
    const ids = Array.from({ length: 60 }, (_, i) => i + 1);
    mockClient.post.mockImplementation((_url, body) =>
      Promise.resolve(okResponse((body as { ids: number[] }).ids)),
    );

    const result = await bulkReviewContributions("approve", ids);

    expect(mockClient.post).toHaveBeenCalledTimes(3); // 25 + 25 + 10
    const sizes = mockClient.post.mock.calls.map(
      (c) => (c[1] as { ids: number[] }).ids.length,
    );
    expect(sizes).toEqual([BULK_REVIEW_BATCH_SIZE, BULK_REVIEW_BATCH_SIZE, 10]);
    expect(result.succeeded).toHaveLength(60);
    expect(result.failed).toHaveLength(0);
  });

  it("keeps going after a batch reports per-row failures", async () => {
    // The server commits each row independently, so a failure inside a batch
    // must not stop the ones after it.
    mockClient.post
      .mockResolvedValueOnce(okResponse([1, 2], [{ id: 3, reason: "not pending" }]))
      .mockResolvedValueOnce(okResponse([4]));

    const result = await bulkReviewContributions(
      "approve",
      Array.from({ length: 26 }, (_, i) => i + 1),
    );

    expect(mockClient.post).toHaveBeenCalledTimes(2);
    expect(result.succeeded).toEqual([1, 2, 4]);
    expect(result.failed).toEqual([{ id: 3, reason: "not pending" }]);
  });

  it("reports every id in a slice the network lost", async () => {
    // The case that silently drops rows if unhandled: the request itself
    // fails, so the server never reports on those ids at all.
    mockClient.post
      .mockRejectedValueOnce({ response: { data: { detail: "Gateway timeout" } } })
      .mockResolvedValueOnce(okResponse([26]));

    const result = await bulkReviewContributions(
      "approve",
      Array.from({ length: 26 }, (_, i) => i + 1),
    );

    expect(result.failed).toHaveLength(25);
    expect(result.failed.every((f) => f.reason === "Gateway timeout")).toBe(true);
    expect(result.succeeded).toEqual([26]);
    // Nothing vanishes: 25 failed + 1 succeeded accounts for all 26.
    expect(result.failed.length + result.succeeded.length).toBe(26);
  });

  it("reads the response key matching the action", async () => {
    mockClient.post.mockResolvedValue({ data: { rejected: [7], failed: [] } });

    const result = await bulkReviewContributions("reject", [7]);

    expect(mockClient.post).toHaveBeenCalledWith(
      "/contributions/bulk-reject/",
      expect.objectContaining({ ids: [7] }),
      expect.anything(),
    );
    expect(result.succeeded).toEqual([7]);
  });

  it("reports progress against the full total, not the batch", async () => {
    mockClient.post.mockImplementation((_url, body) =>
      Promise.resolve(okResponse((body as { ids: number[] }).ids)),
    );
    const seen: Array<[number, number]> = [];

    await bulkReviewContributions(
      "approve",
      Array.from({ length: 30 }, (_, i) => i + 1),
      { onProgress: (done, total) => seen.push([done, total]) },
    );

    expect(seen).toEqual([
      [25, 30],
      [30, 30],
    ]);
  });

  it("stops early when aborted", async () => {
    const controller = new AbortController();
    mockClient.post.mockImplementation((_url, body) => {
      controller.abort();
      return Promise.resolve(okResponse((body as { ids: number[] }).ids));
    });

    const result = await bulkReviewContributions(
      "approve",
      Array.from({ length: 60 }, (_, i) => i + 1),
      { signal: controller.signal },
    );

    expect(mockClient.post).toHaveBeenCalledTimes(1);
    expect(result.succeeded).toHaveLength(25);
  });
});

describe("listContributionIds", () => {
  it("sends the queue filters and omits pagination", async () => {
    // The id list is the whole match set. A page param here would make
    // "select all matching" mean "select all on this page" again -- the exact
    // defect #109 fixed.
    mockClient.get.mockResolvedValue({ data: { ids: [1, 2], count: 2 } });

    await listContributionIds({
      mode: "editor",
      q: "farm",
      source: "vphc",
      page: 3,
      pageSize: 100,
      ordering: "-created_at",
    });

    const params = mockClient.get.mock.calls[0][1].params;
    expect(params).toEqual({ mode: "editor", q: "farm", source: "vphc" });
    expect(params).not.toHaveProperty("page");
    expect(params).not.toHaveProperty("page_size");
  });

  it("tolerates a malformed payload rather than throwing", async () => {
    mockClient.get.mockResolvedValue({ data: { ids: [1, "x", 3] } });

    const result = await listContributionIds();

    expect(result.ids).toEqual([1, 3]);
    expect(result.count).toBe(2);
  });
});
