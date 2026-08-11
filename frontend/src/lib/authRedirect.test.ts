/**
 * @jest-environment jsdom
 */
import { getRedirectPath } from "./authRedirect";

describe("getRedirectPath", () => {
  it("keeps same-origin app paths from string state", () => {
    expect(getRedirectPath({ from: "/record/149786?tab=dates#notes" })).toBe(
      "/record/149786?tab=dates#notes",
    );
  });

  it("keeps same-origin app paths from location-like state", () => {
    expect(getRedirectPath({
      from: {
        pathname: "/dashboard",
        search: "?status=pending",
        hash: "#submissions",
      },
    })).toBe("/dashboard?status=pending#submissions");
  });

  it("rejects external and malformed redirect targets", () => {
    expect(getRedirectPath({ from: "https://example.com/phish" })).toBe("/");
    expect(getRedirectPath({ from: "//example.com/phish" })).toBe("/");
    expect(getRedirectPath({
      from: {
        pathname: "/safe",
        search: "javascript:alert(1)",
        hash: "javascript:alert(1)",
      },
    })).toBe("/safe");
  });
});
