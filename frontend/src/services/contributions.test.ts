/**
 * Issue #109 -- the editor queue's filters must be SENT, not applied to the
 * page the browser is already holding.
 *
 * Dashboard.tsx filtered `editorHistoryItems`, which is one server page. At
 * 2,440 queued contributions that meant searching "farm" returned nothing on
 * page 1 of 25 while Farmville sat on page 16 -- and a lookup that finds
 * nothing reads as "the record isn't there", not as a broken filter.
 *
 * So these tests assert on the request, not on the response: every filter has
 * to appear in the query params, or the queue is page-local again the moment
 * someone adds a caller.
 */
import { listContributions } from "./contributions";
import apiClient from "@/lib/api";

jest.mock("@/lib/api", () => ({
  __esModule: true,
  default: { get: jest.fn() },
  ensureCsrfToken: jest.fn(),
}));

const mockedGet = apiClient.get as jest.Mock;

const page = (results: unknown[] = []) => ({
  data: { count: results.length, next: null, previous: null, results },
});

const paramsOfLastCall = () => mockedGet.mock.calls[0][1].params;

describe("listContributions query params", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue(page());
  });

  it("sends the search text as ?q=", async () => {
    await listContributions({ mode: "editor", q: "farm" });

    const [url, config] = mockedGet.mock.calls[0];
    expect(url).toBe("/contributions/");
    expect(config.params.q).toBe("farm");
  });

  it("sends town, shape, colour and the date range", async () => {
    await listContributions({
      mode: "editor",
      town: "Farmville",
      shape: "C - Circle",
      color: "RED",
      submittedFrom: "2026-07-01",
      submittedTo: "2026-08-12",
    });

    expect(paramsOfLastCall()).toMatchObject({
      town: "Farmville",
      shape: "C - Circle",
      color: "RED",
      submitted_from: "2026-07-01",
      submitted_to: "2026-08-12",
    });
  });

  it("omits blank filters and the 'all' sentinel", async () => {
    await listContributions({
      mode: "editor",
      q: "",
      town: "   ",
      shape: "all",
      color: "all",
      submittedFrom: "",
      submittedTo: "",
    });

    const params = paramsOfLastCall();
    for (const key of ["q", "town", "shape", "color", "submitted_from", "submitted_to"]) {
      expect(params).not.toHaveProperty(key);
    }
    expect(params.mode).toBe("editor");
  });

  it("trims whitespace off q and town", async () => {
    await listContributions({ q: "  farm  ", town: "  Farmville  " });

    expect(paramsOfLastCall()).toMatchObject({ q: "farm", town: "Farmville" });
  });

  it("sends the ordering it is given", async () => {
    await listContributions({ mode: "editor", ordering: "town,id" });

    expect(paramsOfLastCall().ordering).toBe("town,id");
  });

  it("still sends the params it always did", async () => {
    await listContributions({ mode: "editor", status: "pending", state: "VA", page: 3, pageSize: 100 });

    expect(paramsOfLastCall()).toMatchObject({
      mode: "editor",
      status: "pending",
      state: "VA",
      page: 3,
      page_size: 100,
    });
  });
});
