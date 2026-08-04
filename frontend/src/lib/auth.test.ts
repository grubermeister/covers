/**
 * @jest-environment jsdom
 */

describe("auth storage cache", () => {
  beforeEach(() => {
    jest.resetModules();
    localStorage.clear();
    jest.restoreAllMocks();
  });

  it("does not let an older current-user request replace a newer login cache", async () => {
    const oldUser = {
      id: 1,
      username: "olduser",
      email: "old@example.com",
      is_staff: false,
    };
    const newUser = {
      id: 2,
      username: "newuser",
      email: "new@example.com",
      is_staff: false,
    };

    let resolveFetch: (value: { ok: boolean; json: () => Promise<unknown> }) => void = () => {};
    const fetchMock = jest.fn(() => new Promise((resolve) => {
      resolveFetch = resolve;
    }));
    global.fetch = fetchMock as jest.Mock;

    const auth = await import("./auth");
    const staleRequest = auth.fetchCurrentUser();

    auth.setStoredUser(newUser);
    resolveFetch({
      ok: true,
      json: async () => ({ user: oldUser }),
    });

    await expect(staleRequest).resolves.toEqual(oldUser);
    await expect(auth.fetchCurrentUser()).resolves.toEqual(newUser);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
