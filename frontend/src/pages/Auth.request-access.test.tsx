/**
 * @jest-environment jsdom
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import Auth from "./Auth";

// Auth pulls in the site chrome, the API client and toasts; none of that is what
// issue #70 is about, so keep the test to the page's own content.
jest.mock("@/components/Navigation", () => ({ Navigation: () => null }));
jest.mock("@/components/Footer", () => ({ Footer: () => null }));
jest.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({
  getStoredUser: () => null,
  setStoredUser: jest.fn(),
}));
jest.mock("@/lib/api", () => ({
  __esModule: true,
  default: { post: jest.fn() },
  ensureCsrfToken: jest.fn().mockResolvedValue(undefined),
}));

const renderAuth = () =>
  render(
    <MemoryRouter>
      <Auth />
    </MemoryRouter>,
  );

describe("Auth page — request-access path (issue #70)", () => {
  it("tells a visitor with no account what to do, without them having to be told", async () => {
    renderAuth();

    // The card must not imply signing in is the only way in.
    // findBy* lets Formik's validate-on-mount settle before we assert.
    expect(await screen.findByText(/request access if you do not have an account/i)).toBeTruthy();
    expect(screen.getByText(/new to the catalog/i)).toBeTruthy();
    expect(screen.getByText(/accounts are created for you/i)).toBeTruthy();
  });

  it("opens the request-a-login dialog", async () => {
    const user = userEvent.setup();
    renderAuth();

    await user.click(screen.getByRole("button", { name: /request a login/i }));

    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("keeps the request-access button usable before the sign-in form is filled in", async () => {
    renderAuth();

    // Once validation settles, Sign In is disabled on an empty form. The way in for a
    // first-time editor must never be gated on a form they cannot complete.
    await waitFor(() => {
      const signIn = screen.getByRole("button", { name: /^sign in$/i }) as HTMLButtonElement;
      expect(signIn.disabled).toBe(true);
    });

    const request = screen.getByRole("button", { name: /request a login/i }) as HTMLButtonElement;
    expect(request.disabled).toBe(false);
  });
});
