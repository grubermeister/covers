/**
 * @jest-environment jsdom
 */
import { act, render, screen } from "@testing-library/react";
import { useAuth } from "./useAuth";
import { setStoredUser, type AuthUser } from "@/lib/auth";

function AuthProbe() {
  const user = useAuth();
  return <div data-testid="user">{user ? user.username : "none"}</div>;
}

describe("useAuth", () => {
  beforeEach(() => {
    localStorage.clear();
    jest.restoreAllMocks();
  });

  it("keeps a new login event when an older server sync resolves later", async () => {
    const oldUser: AuthUser = {
      id: 1,
      username: "olduser",
      email: "old@example.com",
      is_staff: false,
    };
    const newUser: AuthUser = {
      id: 2,
      username: "newuser",
      email: "new@example.com",
      is_staff: false,
    };

    localStorage.setItem("worldcovers_user", JSON.stringify(oldUser));

    let resolveFetch: (value: { ok: boolean; json: () => Promise<unknown> }) => void = () => {};
    global.fetch = jest.fn(() => new Promise((resolve) => {
      resolveFetch = resolve;
    })) as jest.Mock;

    render(<AuthProbe />);
    expect(screen.getByTestId("user").textContent).toBe("olduser");

    act(() => {
      setStoredUser(newUser);
    });
    expect(screen.getByTestId("user").textContent).toBe("newuser");

    await act(async () => {
      resolveFetch({
        ok: true,
        json: async () => ({ user: oldUser }),
      });
    });

    expect(screen.getByTestId("user").textContent).toBe("newuser");
  });
});
